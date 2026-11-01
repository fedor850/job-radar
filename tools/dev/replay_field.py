"""Re-run triage (+ scoring if it passes) on rejected jobs from a DB copy to see what the wider field filter lets through."""
import asyncio, json, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
logging.basicConfig(level=logging.WARNING)
from jobradar.config import load_settings
from jobradar.db import DB
from jobradar.ai import AI

async def main():
    s = load_settings(); db = DB(s.db_path); ai = AI(s, db)
    rows = db.query("select id from jobs where (stage='triage_reject' or (stage='blocked' and reason like '%wrong_field%')) "
                    "and first_seen>='2026-10-23' order by id desc limit 400")
    blocking = set(s.get("filters.blocking"))
    sem = asyncio.Semaphore(8); out = []
    async def one(jid):
        job = db.get_job(jid)
        async with sem:
            t = await ai.triage(job)
            if t.get("obviously_irrelevant"):
                out.append((jid, job["stage"], "TRIAGE_REJ", None, job["title"][:60], job["company"][:25], t.get("reason", "")[:70])); return
            if not job.get("description"):
                out.append((jid, job["stage"], "PASS_NODESC", None, job["title"][:60], job["company"][:25], "")); return
            sc = await ai.score(job, "2026-10-25 (Sunday)")
        nb = sorted(set(sc.get("blockers") or []) & blocking)
        verdict = "BLOCKED:" + ",".join(nb) if nb else ("SEND" if sc["score"] >= 40 else "below")
        out.append((jid, job["stage"], verdict, sc["score"], job["title"][:60], job["company"][:25], ""))
    await asyncio.gather(*(one(r["id"]) for r in rows))
    from collections import Counter
    print(Counter(o[2].split(":")[0] for o in out))
    for o in sorted(out, key=lambda x: (x[2] != "SEND", x[0])):
        if o[2] in ("SEND", "below", "PASS_NODESC") or o[2].startswith("BLOCKED"):
            print(o)
    print([dict(x) for x in db.query("select kind,calls,round(cost,3) c from usage order by rowid desc limit 3")])
asyncio.run(main())
