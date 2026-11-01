"""Live test of triage + scoring on real postings (needs ANTHROPIC_API_KEY)."""
import asyncio, os, sys, tempfile, time, json, logging
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
import re

async def main():
    s=load_settings(); db=DB(s.db_path); ai=AI(s, db)
    raws=[]
    # a mix: Deloitte AT (SR), RBI (rmk), ZBP, Accenture (workday), Bitpanda (GH)
    for kind,key,params,comp,n in [("smartrecruiters","deloitteat",{},"Deloitte Österreich",4),("rmk","rbi",{"host":"jobs.rbinternational.com","locationsearch":"Wien"},"RBI",3),
                                   ("workday","accenture/AccentureCareers",{"host":"accenture.wd103.myworkdayjobs.com","tenant":"accenture","site":"AccentureCareers"},"Accenture",3),
                                   ("greenhouse","bitpanda",{},"Bitpanda",2)]:
        js=ADAPTERS[kind](key,params,comp)
        # prefer student-ish titles
        js=sorted(js, key=lambda j: 0 if re.search(r'prakti|intern|student|junior|trainee|werkstud', j.title, re.I) else 1)[:n]
        for j in js:
            if j.needs_detail: j=DETAILS[kind](j)
            raws.append(j)
    z=ZbpSource(s,{}); zj=z.fetch()[:3]
    raws += [z.fetch_detail(j) for j in zj]
    total_t=time.time()
    for raw in raws:
        jid=db.insert_job(raw, fingerprint(raw.company, raw.title))
        job=db.get_job(jid)
        t=time.time(); tr=await ai.triage(job); t1=time.time()-t
        t=time.time(); sc=await ai.score(job, "2026-10-24 (Saturday)"); t2=time.time()-t
        db.update_job(jid, score=sc['score'], analysis=sc)
        job=db.get_job(jid)
        print(f"\n### {raw.title[:70]} | {raw.company} | triage={tr} ({t1:.1f}s) | score={sc['score']} {sc['blockers']} ({t2:.1f}s)")
        print(re.sub(r"<[^>]+>","",render_job(job))[:1500])
    print("\nTOTAL", round(time.time()-total_t,1), "s; usage:", [dict(r) for r in db.query("select * from usage")])

asyncio.run(main())
