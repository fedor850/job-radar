"""Re-score already scored jobs from a COPY of the production DB with the current code and compare.

usage: JOBRADAR_DATA=<tmpdir with jobradar.sqlite3 copy> python tests/replay.py [ids|stages] [limit]
"""
import asyncio, json, os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
logging.basicConfig(level=logging.WARNING)
from jobradar.config import load_settings
from jobradar.db import DB
from jobradar.ai import AI
from jobradar.telegram_ui import docs_label

async def main():
    s = load_settings(); db = DB(s.db_path); ai = AI(s, db)
    sel = sys.argv[1] if len(sys.argv) > 1 else "sent,below_threshold,blocked"
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    if sel[0].isdigit():
        rows = db.query(f"select id from jobs where id in ({sel})")
    else:
        st = ",".join(f"'{x}'" for x in sel.split(","))
        rows = db.query(f"select id from jobs where stage in ({st}) and analysis is not null order by id desc limit {lim}")
    blocking = set(s.get("filters.blocking"))
    sem = asyncio.Semaphore(6)
    out = []
    async def one(jid):
        job = db.get_job(jid)
        old = job.get("analysis") or {}
        async with sem:
            try:
                new = await ai.score(job, "2026-10-25 (Sunday)")
            except Exception as e:
                return print("ERR", jid, e)
        ob = sorted(set(old.get("blockers") or []) & blocking); nb = sorted(set(new.get("blockers") or []) & blocking)
        o_stage = job["stage"]
        n_stage = "blocked" if nb else ("below" if new["score"] < 40 else "SEND")
        out.append(dict(id=jid, stage=o_stage, title=job["title"][:70], company=(job["company"] or "")[:30],
                        old=old.get("score"), new=new["score"], ob=ob, nb=nb, n_stage=n_stage, lang=new.get("_lang"),
                        adl=new.get("ad_language"), german=new.get("german"), ev=new.get("german_evidence"),
                        cl=new.get("cover_letter"), docs=new.get("apply_docs"), how=new.get("how_to_apply"),
                        hours=new.get("hours"), risks=new.get("risks"), tip=new.get("tip"),
                        label=docs_label({"analysis": new})))
    await asyncio.gather(*(one(r["id"]) for r in rows))
    out.sort(key=lambda r: r["id"])
    json.dump(out, open(os.path.join(os.environ["JOBRADAR_DATA"], "replay.json"), "w"), ensure_ascii=False, indent=1)
    for r in out:
        flag = ""
        was_sent = r["stage"] == "sent"
        if was_sent and r["n_stage"] != "SEND": flag = "  <<< NOW NOT SENT"
        if not was_sent and r["n_stage"] == "SEND": flag = "  >>> NOW SENT"
        print(f"{r['id']:5} {r['stage'][:9]:9} {r['old']}->{r['new']:3} {','.join(r['nb']) or '-':22} {r['adl']} {r['lang']:18} "
              f"{r['german']:10} | {r['title']} @ {r['company']}{flag}")
    print([dict(x) for x in db.query("select kind,calls,cost from usage")])

asyncio.run(main())
