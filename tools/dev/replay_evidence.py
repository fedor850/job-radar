"""Replay recent decisions with the evidence-based triage/scoring (30.10.2026) on a COPY of the DB.

usage: JOBRADAR_DATA=<tmpdir with jobradar.sqlite3 copy> python tests/replay_evidence.py [per_group]
Prints old vs new decision for: triage rejects, blocked, below threshold, sent.
"""
import asyncio, json, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
logging.basicConfig(level=logging.WARNING)
from jobradar.config import load_settings
from jobradar.db import DB
from jobradar.ai import AI, triage_verdict

N = int(sys.argv[1]) if len(sys.argv) > 1 else 25


async def main():
    s = load_settings(); db = DB(s.db_path); ai = AI(s, db)
    blocking = set(s.get("filters.blocking"))
    groups = {
        "triage_reject": f"stage='triage_reject' order by id desc limit {N}",
        "blocked": f"stage='blocked' and reason not like 'snippet%' order by id desc limit {N}",
        "below": f"stage='below_threshold' order by id desc limit {max(5, N // 3)}",
        "sent": f"stage='sent' order by id desc limit {max(5, N // 2)}",
    }
    sem = asyncio.Semaphore(6)
    rows_out = []

    async def one(g, jid):
        job = db.get_job(jid)
        async with sem:
            try:
                tri = None
                if g == "triage_reject":
                    t = await ai.triage(job)
                    rej, why = triage_verdict(t, job)
                    tri = (rej, why)
                    if rej or not job.get("description"):
                        rows_out.append((g, jid, job["title"][:60], job.get("reason", "")[:60], "TRIAGE_REJECT" if rej else "→score(no text)", why[:90], ""))
                        return
                a = await ai.score(job, "2026-10-30 (Friday)")
            except Exception as e:
                rows_out.append((g, jid, job["title"][:60], "ERR", str(e)[:80], "", "")); return
        nb = sorted(set(a.get("blockers") or []) & blocking)
        new = "BLOCK " + ",".join(nb) if nb else ("SEND" if a["score"] >= 40 or a.get("_unverified") else f"below({a['score']})")
        extra = ""
        if a.get("_reask"): extra += f" reask:{a['_reask']['blockers_claimed']}"
        if a.get("_unverified"): extra += f" UNVERIFIED:{a['_unverified']}"
        if a.get("_german_ad_exempt"): extra += " en-exempt"
        proof = "; ".join(f"{b}=«{q[:60]}»" for b, q in (a.get("_proof") or {}).items())
        rows_out.append((g, jid, job["title"][:60], (job.get("reason") or str(job.get("score")))[:50], new, proof[:150], extra))

    tasks = []
    for g, where in groups.items():
        for r in db.query(f"select id from jobs where first_seen>='2026-10-26' and {where}"):
            tasks.append(one(g, r["id"]))
    await asyncio.gather(*tasks)
    for r in sorted(rows_out):
        print(" | ".join(str(x) for x in r))
    print([dict(x) for x in db.query("select kind,calls,round(cost,4) cost from usage")])

asyncio.run(main())
