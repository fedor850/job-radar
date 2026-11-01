"""Find the career systems (ATS) of employers that recently posted Vienna jobs (company names from the radar's own DB).

usage: python tools/discover_from_db.py probe|verify <db copy> <workdir> <days> [extra_names.txt|-] [seconds]

1. names  = companies with jobs in the DB in the last <days> days, not yet covered by a board
2. probes = public endpoints of Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Personio, Workable, Teamtailor,
            BambooHR, Breezy, Pinpoint, JOIN for 1–3 slug guesses per name
3. verify = run the radar's real adapter; keep a board only if it currently lists ≥ 1 Vienna/remote-AT job AND it is
            really this employer: one of its titles matches a title the DB saw from that company, or the slug is long
            and specific (≥ 7 chars) and equals the full normalised name.
"""
import json
import os
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
from jobradar.sources.boards import ADAPTERS
from jobradar.util import company_key, slug_candidates
from jobradar.links import title_similarity

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (JobRadar board discovery)"
S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64, max_retries=0))

RECRUITERS = re.compile(r"\b(hays|iventa|trenkwalder|manpower|adecco|randstad|page ?group|michael page|robert walters|"
                        r"jobster|talentir|personal(?:service|bereitstellung|management|dienst)|recruit|staffing|"
                        r"headhunt|jobs?\b|karriere|stellen|zeitarbeit|leasing)\b", re.I)


def ok_json(url):
    try:
        r = S.get(url, timeout=12, allow_redirects=False)
        return r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def ok_text(url, must):
    try:
        r = S.get(url, timeout=12, allow_redirects=False)
        return r.text if r.status_code == 200 and must in r.text else None
    except requests.RequestException:
        return None


PROBES = {
    "greenhouse": lambda s: ("jobs" in (ok_json(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs") or {})) and {},
    "greenhouse_eu": lambda s: ("jobs" in (ok_json(f"https://boards-api.eu.greenhouse.io/v1/boards/{s}/jobs") or {})) and {"eu": True},
    "lever": lambda s: isinstance(ok_json(f"https://api.lever.co/v0/postings/{s}?mode=json"), list) and {},
    "lever_eu": lambda s: isinstance(ok_json(f"https://api.eu.lever.co/v0/postings/{s}?mode=json"), list) and {"eu": True},
    "ashby": lambda s: ("jobs" in (ok_json(f"https://api.ashbyhq.com/posting-api/job-board/{s}") or {})) and {},
    "smartrecruiters": lambda s: ((ok_json(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1") or {}).get("totalFound") or 0) > 0 and {},
    "recruitee": lambda s: ("offers" in (ok_json(f"https://{s}.recruitee.com/api/offers/") or {})) and {},
    "personio": lambda s: bool(ok_text(f"https://{s}.jobs.personio.de/xml", "<workzag-jobs")) and {"tld": "de"},
    "personio_com": lambda s: bool(ok_text(f"https://{s}.jobs.personio.com/xml", "<workzag-jobs")) and {"tld": "com"},
    "workable": lambda s: ("jobs" in (ok_json(f"https://apply.workable.com/api/v1/widget/accounts/{s}") or {})) and {},
    "teamtailor": lambda s: bool(ok_text(f"https://{s}.teamtailor.com/jobs.rss", "<rss")) and {},
    "bamboohr": lambda s: ("result" in (ok_json(f"https://{s}.bamboohr.com/careers/list") or {})) and {},
    "breezy": lambda s: isinstance(ok_json(f"https://{s}.breezy.hr/json"), list) and {},
    "pinpoint": lambda s: ("data" in (ok_json(f"https://{s}.pinpointhq.com/postings.json") or {})) and {},
    "join": lambda s: bool(ok_text(f"https://join.com/companies/{s}", "__NEXT_DATA__")) and {},
}


def load_names(db: str, days: int, extra: str | None) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    covered = {company_key(r["company"] or r["key"]) for r in c.execute("select company, key from boards")}
    names: dict[str, dict] = {}
    for r in c.execute("select company, title from jobs where first_seen>=? and company<>''", (since,)):
        name = (r["company"] or "").strip()
        k = company_key(name)
        if not k or k in covered or len(k) < 3 or RECRUITERS.search(name):
            continue
        d = names.setdefault(k, {"name": name, "titles": set(), "n": 0})
        d["n"] += 1
        if len(d["titles"]) < 60:
            d["titles"].add(r["title"] or "")
    if extra:
        for line in open(extra, encoding="utf-8"):
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            k = company_key(name)
            if k and k not in covered:
                names.setdefault(k, {"name": name, "titles": set(), "n": 0})
    return names


def tasks_for(names: dict) -> list:
    tasks = []
    for k in sorted(names):
        for slug in slug_candidates(names[k]["name"])[:2]:
            if len(slug) >= 3:
                tasks += [(k, slug, kind) for kind in PROBES]
    return tasks


def probe(t):
    k, slug, kind = t
    try:
        res = PROBES[kind](slug)
    except Exception:  # noqa: BLE001
        return None
    return [k, slug, kind, res] if res is not False and res is not None else None


def main():
    """Resumable (each call ≤ 3 min on the desktop shell):
        probe  <db> <workdir> <days> [extra_names.txt] [seconds]  → appends endpoint hits to workdir/hits.jsonl
        verify <db> <workdir> <days> [extra_names.txt]            → workdir/boards.json"""
    import time
    mode, db, wd, days = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    extra = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else None
    names = load_names(db, days, extra)
    tasks = tasks_for(names)
    os.makedirs(wd, exist_ok=True)
    hits_f = os.path.join(wd, "hits.jsonl")
    if mode == "probe":
        budget = float(sys.argv[6]) if len(sys.argv) > 6 else 150
        pos_f = os.path.join(wd, os.environ.get("POS_FILE", "pos.txt"))
        hits_f = os.path.join(wd, os.environ.get("HITS_FILE", "hits.jsonl"))
        pos = int(open(pos_f).read()) if os.path.exists(pos_f) else int(os.environ.get("START", 0))
        end = int(os.environ.get("END", len(tasks)))
        skip = set(filter(None, os.environ.get("SKIP_KINDS", "").split(",")))
        step = int(os.environ.get("STEP", 1600))
        t0 = time.time()
        print(f"names={len(names)} tasks={len(tasks)} start={pos} end={end}", flush=True)
        with ThreadPoolExecutor(int(os.environ.get("THREADS", 160))) as ex:
            while pos < end and time.time() - t0 < budget:
                chunk = [t for t in tasks[pos:min(end, pos + step)] if t[2] not in skip]
                with open(hits_f, "a", encoding="utf-8") as f:
                    for h in ex.map(probe, chunk):
                        if h:
                            f.write(json.dumps(h) + "\n")
                pos = min(end, pos + step)
                open(pos_f, "w").write(str(pos))
        print(f"pos={pos}/{end} hits={sum(1 for _ in open(hits_f)) if os.path.exists(hits_f) else 0}", flush=True)
        return
    hits = [json.loads(l) for l in open(hits_f, encoding="utf-8")]
    print("endpoint hits:", len(hits), flush=True)

    def verify(h):
        k, slug, kind, params = h
        if k not in names:
            return None
        real = kind.replace("_eu", "").replace("_com", "")
        try:
            jobs = ADAPTERS[real](slug, dict(params or {}), names[k]["name"])
        except Exception:  # noqa: BLE001
            return None
        if not jobs:
            return None
        seen = names[k]["titles"]
        title_match = any(title_similarity(j.title, t) >= 0.8 for j in jobs for t in seen)
        full = re.sub(r"[^a-z0-9]", "", names[k]["name"].lower())
        specific = len(slug) >= 7 and slug.replace("-", "") in full
        if not (title_match or specific):
            return None
        return {"kind": real, "key": slug, "company": names[k]["name"], "params": params or {},
                "vienna_jobs": len(jobs), "db_jobs": names[k]["n"], "title_match": title_match,
                "sample": [j.title for j in jobs[:3]]}

    found = {}
    with ThreadPoolExecutor(24) as ex:
        for v in ex.map(verify, hits):
            if v:
                kk = company_key(v["company"])
                if kk not in found or v["vienna_jobs"] > found[kk]["vienna_jobs"]:
                    found[kk] = v
    json.dump(sorted(found.values(), key=lambda x: -x["vienna_jobs"]), open(os.path.join(wd, "boards.json"), "w"),
              ensure_ascii=False, indent=1)
    print("verified boards:", len(found))


if __name__ == "__main__":
    main()
