"""SQLite storage. Single-file, WAL mode, guarded by a lock (used from the asyncio loop and worker threads)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .util import iso, now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    fingerprint TEXT,
    title TEXT, company TEXT, location TEXT,
    url TEXT, apply_url TEXT, apply_kind TEXT,
    company_linkedin TEXT,
    description TEXT,
    posted_at TEXT, first_seen TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'seen',
    dup_of INTEGER,
    reason TEXT,
    score INTEGER,
    analysis TEXT,
    meta TEXT,
    tg_message_id INTEGER,
    sent_at TEXT,
    user_status TEXT,
    user_reason TEXT,
    updated TEXT
);
CREATE INDEX IF NOT EXISTS jobs_fp ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS jobs_stage ON jobs(stage);
CREATE INDEX IF NOT EXISTS jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS jobs_sent_at ON jobs(sent_at);

CREATE TABLE IF NOT EXISTS boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, key TEXT NOT NULL,
    company TEXT, origin TEXT DEFAULT 'seed',
    params TEXT, enabled INTEGER DEFAULT 1,
    bootstrapped INTEGER DEFAULT 0,
    last_ok TEXT, last_error TEXT, fails INTEGER DEFAULT 0,
    created TEXT,
    UNIQUE(kind, key)
);

CREATE TABLE IF NOT EXISTS source_health (
    name TEXT PRIMARY KEY,
    last_run TEXT, last_ok TEXT, last_error TEXT,
    fails INTEGER DEFAULT 0, alerted INTEGER DEFAULT 0,
    runs INTEGER DEFAULT 0, items_total INTEGER DEFAULT 0, new_total INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS emails (
    msg_id TEXT PRIMARY KEY,
    thread_id TEXT, received TEXT, sender TEXT, subject TEXT,
    kind TEXT, company TEXT, tracker_id TEXT, analysis TEXT, processed_at TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER, action TEXT, reason TEXT, title TEXT, company TEXT, created TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    tracker_id TEXT PRIMARY KEY,
    job_id INTEGER,
    company TEXT, title TEXT, url TEXT,
    status TEXT, applied_at TEXT, followup_due TEXT, followup_count INTEGER DEFAULT 0,
    last_email_at TEXT, contact TEXT, updated TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    day TEXT, kind TEXT, calls INTEGER DEFAULT 0, in_tokens INTEGER DEFAULT 0,
    out_tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
    PRIMARY KEY (day, kind)
);
"""

STAGES_FINAL = ("sent", "below_threshold", "blocked", "triage_reject", "prefiltered_out", "duplicate", "bootstrap")


class DB:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ------------------------------------------------------------ basics
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    # ------------------------------------------------------------ kv
    def kv_get(self, k: str, default: Any = None) -> Any:
        row = self.one("SELECT v FROM kv WHERE k=?", (k,))
        if row is None:
            return default
        try:
            return json.loads(row["v"])
        except (TypeError, ValueError):
            return row["v"]

    def kv_set(self, k: str, v: Any) -> None:
        self.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    # ------------------------------------------------------------ jobs
    def job_exists(self, uid: str) -> bool:
        return self.one("SELECT 1 FROM jobs WHERE uid=?", (uid,)) is not None

    def insert_job(self, raw, fingerprint: str, stage: str = "seen", dup_of: int | None = None, reason: str = "") -> int:
        cur = self.execute(
            """INSERT OR IGNORE INTO jobs(uid, source, external_id, fingerprint, title, company, location, url, apply_url,
                   company_linkedin, description, posted_at, first_seen, stage, dup_of, reason, meta, updated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                raw.uid, raw.source, raw.external_id, fingerprint, raw.title, raw.company, raw.location, raw.url,
                raw.apply_url, raw.company_linkedin, raw.description, iso(raw.posted_at), iso(now_utc()), stage,
                dup_of, reason, json.dumps(raw.meta, ensure_ascii=False, default=str), iso(now_utc()),
            ),
        )
        if cur.lastrowid:
            return cur.lastrowid
        row = self.one("SELECT id FROM jobs WHERE uid=?", (raw.uid,))
        return row["id"] if row else 0

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated"] = iso(now_utc())
        for k in ("analysis", "meta"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], ensure_ascii=False, default=str)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def get_job(self, job_id: int) -> dict | None:
        row = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        return self.row_to_job(row) if row else None

    @staticmethod
    def row_to_job(row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in ("analysis", "meta"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else {}
            except (TypeError, ValueError):
                d[k] = {}
        return d

    #: An earlier row that never got a fair look must not swallow a later copy of the same ad: ads dropped for their
    #: place (query "remote"), failed ones, snippet-only blocks. `fresh=True` (the copy is new/undated) also skips
    #: never-evaluated "bootstrap" backlog rows.
    _UNFAIR = ("NOT (stage='prefiltered_out' AND reason IN ('not remote','location after detail',"
               "'dropped by source (location/removed)')) AND stage<>'failed' "
               "AND NOT (stage='blocked' AND reason LIKE 'snippet_only%')")

    def find_by_fingerprint(self, fp: str, days: int = 45, exclude_uid: str = "", fresh: bool = False) -> dict | None:
        since = iso(now_utc() - timedelta(days=days))
        row = self.one(
            "SELECT * FROM jobs WHERE fingerprint=? AND first_seen>=? AND uid<>? AND stage<>'duplicate' AND "
            + self._UNFAIR + (" AND stage<>'bootstrap'" if fresh else "") + " ORDER BY id ASC LIMIT 1",
            (fp, since, exclude_uid),
        )
        return self.row_to_job(row) if row else None

    def find_same_title(self, title_key: str, days: int = 45, exclude_uid: str = "", fresh: bool = False) -> list[dict]:
        """Jobs with the same normalised title regardless of company (for cross-source duplicates)."""
        since = iso(now_utc() - timedelta(days=days))
        rows = self.query(
            "SELECT * FROM jobs WHERE fingerprint LIKE ? AND first_seen>=? AND uid<>? AND stage<>'duplicate' AND "
            + self._UNFAIR + (" AND stage<>'bootstrap'" if fresh else "") + " ORDER BY id ASC LIMIT 20",
            ("%|" + title_key, since, exclude_uid),
        )
        return [self.row_to_job(r) for r in rows]

    def find_sent_by_company(self, company_key: str, days: int = 60) -> list[dict]:
        since = iso(now_utc() - timedelta(days=days))
        rows = self.query(
            "SELECT * FROM jobs WHERE fingerprint LIKE ? AND first_seen>=? AND stage='sent' ORDER BY id DESC",
            (company_key + "|%", since),
        )
        return [self.row_to_job(r) for r in rows]

    def recent_jobs_by_company_key(self, company_key: str, days: int = 30) -> list[dict]:
        since = iso(now_utc() - timedelta(days=days))
        rows = self.query(
            "SELECT id, uid, source, title, company, url, apply_url FROM jobs WHERE fingerprint LIKE ? AND first_seen>=?",
            (company_key + "|%", since),
        )
        return [dict(r) for r in rows]

    def stage_counts(self, since: datetime) -> dict[str, int]:
        rows = self.query("SELECT stage, COUNT(*) n FROM jobs WHERE first_seen>=? GROUP BY stage", (iso(since),))
        return {r["stage"]: r["n"] for r in rows}

    def source_counts(self, since: datetime, stage: str | None = None) -> dict[str, int]:
        if stage:
            rows = self.query(
                "SELECT source, COUNT(*) n FROM jobs WHERE first_seen>=? AND stage=? GROUP BY source",
                (iso(since), stage),
            )
        else:
            rows = self.query("SELECT source, COUNT(*) n FROM jobs WHERE first_seen>=? GROUP BY source", (iso(since),))
        out: dict[str, int] = {}
        for r in rows:
            fam = r["source"].split(":", 1)[0]
            out[fam] = out.get(fam, 0) + r["n"]
        return out

    # ------------------------------------------------------------ boards
    def upsert_board(self, kind: str, key: str, company: str, origin: str, params: dict | None = None) -> bool:
        """Returns True if a new board was inserted."""
        if kind in ("smartrecruiters", "greenhouse", "lever", "workable", "recruitee", "personio"):
            key = key.lower()
        cur = self.execute(
            "INSERT OR IGNORE INTO boards(kind,key,company,origin,params,created) VALUES(?,?,?,?,?,?)",
            (kind, key, company, origin, json.dumps(params or {}), iso(now_utc())),
        )
        if cur.rowcount == 0 and origin == "seed":
            self.execute(
                "UPDATE boards SET company=?, params=?, enabled=1 WHERE kind=? AND key=? AND origin='seed'",
                (company, json.dumps(params or {}), kind, key),
            )
        return cur.rowcount > 0

    def boards(self, enabled_only: bool = True) -> list[dict]:
        rows = self.query("SELECT * FROM boards" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id")
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"] or "{}")
            out.append(d)
        return out

    def board_result(self, board_id: int, ok: bool, error: str = "") -> None:
        if ok:
            self.execute("UPDATE boards SET last_ok=?, fails=0, last_error='', bootstrapped=1 WHERE id=?",
                         (iso(now_utc()), board_id))
        else:
            self.execute("UPDATE boards SET fails=fails+1, last_error=? WHERE id=?", (error[:300], board_id))
            # disable learned boards that keep failing (e.g. wrong slug); seeds stay enabled
            self.execute("UPDATE boards SET enabled=0 WHERE id=? AND origin<>'seed' AND fails>=20", (board_id,))

    def failing_boards(self, min_fails: int) -> list[dict]:
        """Enabled company sites that have failed `min_fails` runs in a row (a dead slug, a site that moved, a block)."""
        return [dict(r) for r in self.query(
            "SELECT id,kind,key,company,fails,last_error FROM boards WHERE enabled=1 AND fails>=? ORDER BY fails DESC",
            (min_fails,))]

    # ------------------------------------------------------------ health
    def health_run(self, name: str, ok: bool, items: int = 0, new: int = 0, error: str = "") -> dict:
        now = iso(now_utc())
        self.execute("INSERT OR IGNORE INTO source_health(name) VALUES(?)", (name,))
        if ok:
            self.execute(
                "UPDATE source_health SET last_run=?, last_ok=?, fails=0, runs=runs+1, items_total=items_total+?, "
                "new_total=new_total+? WHERE name=?",
                (now, now, items, new, name),
            )
        else:
            self.execute(
                "UPDATE source_health SET last_run=?, last_error=?, fails=fails+1, runs=runs+1 WHERE name=?",
                (now, error[:400], name),
            )
        return dict(self.one("SELECT * FROM source_health WHERE name=?", (name,)))

    def set_alerted(self, name: str, value: int) -> None:
        self.execute("UPDATE source_health SET alerted=? WHERE name=?", (value, name))

    def health_all(self) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM source_health ORDER BY name")]

    # ------------------------------------------------------------ feedback
    def add_feedback(self, job_id: int, action: str, reason: str, title: str, company: str) -> None:
        self.execute(
            "INSERT INTO feedback(job_id,action,reason,title,company,created) VALUES(?,?,?,?,?,?)",
            (job_id, action, reason, title, company, iso(now_utc())),
        )

    def recent_feedback(self, limit: int = 25) -> list[dict]:
        return [dict(r) for r in self.query("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------ applications (mirror of the tracker)
    APP_COLS = {"job_id", "company", "title", "url", "status", "applied_at", "followup_due", "followup_count",
                "last_email_at", "contact", "updated"}

    def upsert_application(self, tracker_id: str, **f: Any) -> None:
        f = {k: v for k, v in f.items() if k in self.APP_COLS}  # e.g. "last_email" lives only in the sheet
        f["updated"] = iso(now_utc())
        existing = self.one("SELECT tracker_id FROM applications WHERE tracker_id=?", (tracker_id,))
        if existing:
            cols = ", ".join(f"{k}=?" for k in f)
            self.execute(f"UPDATE applications SET {cols} WHERE tracker_id=?", (*f.values(), tracker_id))
        else:
            cols = ", ".join(["tracker_id", *f.keys()])
            qs = ", ".join("?" for _ in range(len(f) + 1))
            self.execute(f"INSERT INTO applications({cols}) VALUES({qs})", (tracker_id, *f.values()))

    def get_application(self, tracker_id: str) -> dict | None:
        row = self.one("SELECT * FROM applications WHERE tracker_id=?", (tracker_id,))
        return dict(row) if row else None

    def applications(self, statuses: Iterable[str] | None = None) -> list[dict]:
        if statuses:
            st = list(statuses)
            q = ",".join("?" for _ in st)
            return [dict(r) for r in self.query(f"SELECT * FROM applications WHERE status IN ({q})", st)]
        return [dict(r) for r in self.query("SELECT * FROM applications")]

    # ------------------------------------------------------------ emails
    def email_seen(self, msg_id: str) -> bool:
        return self.one("SELECT 1 FROM emails WHERE msg_id=?", (msg_id,)) is not None

    def save_email(self, msg_id: str, **f: Any) -> None:
        f.setdefault("processed_at", iso(now_utc()))
        if "analysis" in f and not isinstance(f["analysis"], (str, type(None))):
            f["analysis"] = json.dumps(f["analysis"], ensure_ascii=False)
        cols = ", ".join(["msg_id", *f.keys()])
        qs = ", ".join("?" for _ in range(len(f) + 1))
        self.execute(f"INSERT OR REPLACE INTO emails({cols}) VALUES({qs})", (msg_id, *f.values()))

    # ------------------------------------------------------------ usage / cost
    def add_usage(self, kind: str, in_tokens: int, out_tokens: int, cost: float) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.execute(
            """INSERT INTO usage(day,kind,calls,in_tokens,out_tokens,cost) VALUES(?,?,1,?,?,?)
               ON CONFLICT(day,kind) DO UPDATE SET calls=calls+1, in_tokens=in_tokens+excluded.in_tokens,
               out_tokens=out_tokens+excluded.out_tokens, cost=cost+excluded.cost""",
            (day, kind, in_tokens, out_tokens, cost),
        )

    def usage_since(self, day: str) -> dict[str, Any]:
        row = self.one(
            "SELECT COALESCE(SUM(calls),0) calls, COALESCE(SUM(cost),0) cost FROM usage WHERE day>=?", (day,)
        )
        return dict(row) if row else {"calls": 0, "cost": 0.0}

    def count_usage_today(self, kind: str) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.one("SELECT calls FROM usage WHERE day=? AND kind=?", (day, kind))
        return int(row["calls"]) if row else 0

    def prune(self, days: int = 60) -> int:
        """Drop the stored text of old jobs that were never sent (keeps the row: uid/fingerprint still dedupe).

        The DB grew ~6 MB/day, mostly descriptions of duplicates and rejected ads."""
        since = iso(now_utc() - timedelta(days=days))
        cur = self.execute(
            "UPDATE jobs SET description=NULL WHERE first_seen<? AND sent_at IS NULL AND description IS NOT NULL "
            "AND stage IN ('duplicate','bootstrap','prefiltered_out','triage_reject','blocked','below_threshold',"
            "'failed')", (since,))
        return cur.rowcount or 0

    def vacuum(self) -> None:
        with self._lock:
            self.conn.execute("VACUUM")

    def backup(self, dest: Path) -> None:
        with self._lock:
            target = sqlite3.connect(str(dest))
            self.conn.backup(target)
            target.close()
