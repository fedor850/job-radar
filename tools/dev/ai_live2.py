import asyncio, os, sys, tempfile, time, re, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
os.environ.setdefault("JOBRADAR_DATA", tempfile.mkdtemp())
logging.basicConfig(level=logging.WARNING)
from jobradar.config import load_settings
from jobradar.db import DB
from jobradar.ai import AI
from jobradar.sources.boards import ADAPTERS, DETAILS
from jobradar.sources.zbp import ZbpSource
from jobradar.util import fingerprint
from jobradar.telegram_ui import render_job
async def main():
    s=load_settings(); db=DB(s.db_path); ai=AI(s, db)
    effort=sys.argv[1] if len(sys.argv)>1 else "low"
    s.cfg['ai']['score_effort']=effort
    z=ZbpSource(s,{}); zj=[z.fetch_detail(j) for j in z.fetch()[:2]]
    rbi=[j for j in ADAPTERS['rmk']('rbi',{"host":"jobs.rbinternational.com","locationsearch":"Wien"},'RBI') if 'Agile' in j.title or 'Studentjob' in j.title][:2]
    rbi=[DETAILS['rmk'](j) for j in rbi]
    for raw in zj+rbi:
        jid=db.insert_job(raw, fingerprint(raw.company, raw.title)); job=db.get_job(jid)
        tr=await ai.triage(job); t=time.time(); sc=await ai.score(job, "2026-10-24 (Saturday)"); dt=time.time()-t
        db.update_job(jid, score=sc['score'], analysis=sc); job=db.get_job(jid)
        print(f"\n### triage={tr} | {dt:.1f}s"); print(re.sub(r"<[^>]+>","",render_job(job))[:1400])
    print([dict(r) for r in db.query("select kind,calls,cost from usage")])
asyncio.run(main())
