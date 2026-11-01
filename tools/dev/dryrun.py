"""End-to-end dry run with a fake AI + fake Telegram (no API keys needed)."""
import asyncio, logging, os, sys, tempfile, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
os.environ["JOBRADAR_DATA"] = tempfile.mkdtemp()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
for n in ("httpx","urllib3","JobSpy"): logging.getLogger(n).setLevel(logging.WARNING)

from jobradar.config import load_settings
from jobradar.db import DB
from jobradar.pipeline import Pipeline
from jobradar.sheets import Sheets
from jobradar.telegram_ui import render_job, job_keyboard, source_label
from jobradar.bot import JobRadar

class FakeAI:
    def __init__(self): self.calls={"triage":0,"score":0}
    async def triage(self, job):
        self.calls["triage"]+=1
        t=(job["title"]+" "+(job.get("description") or "")[:300]).lower()
        ok = bool(re.search(r"intern|prakti|trainee|student|werkstud|junior|assistant|assistenz|analyst|sales|business|marketing|finance|account", t))
        return {"pass": ok, "reason": "fake"}
    async def score(self, job, today):
        self.calls["score"]+=1
        t=job["title"].lower()
        s = 85 if re.search(r"intern|prakti|student|werkstud|trainee", t) else 55
        return {"score": s, "verdict":"good","blockers":[],"title_clean":job["title"],"company_clean":job["company"],
                "role_type":"internship","hours":"20 h/week","hours_ok":"yes","german":"plus","ad_language":"en",
                "salary":"€2,600 (38.5h)","salary_20h_eur":1351,"start":"January 2027","deadline":"",
                "workplace":"hybrid","location":"Wien","summary":"Test summary of the job.",
                "why":["B2B sales: met a EUR 50k monthly target — matches the requirement","Excel/VBA pricing models — matches the reporting tasks"],
                "risks":["hours not stated — ask about 20 h"],"tip":"Stress B2B sales and Excel."}

class FakeNotifier:
    ready=True
    def __init__(self, settings): self.settings=settings; self.sent=[]
    @staticmethod
    def source_label(job): return source_label(job)
    async def send_job(self, job, silent=False):
        txt=render_job(job); kb=job_keyboard(self.settings, job)
        self.sent.append(job["id"])
        if len(self.sent)<=2:
            print("\n----- CARD -----\n"+re.sub(r"<[^>]+>","",txt)); print("buttons:", [[b.text for b in row] for row in kb.inline_keyboard])
        return 1000+len(self.sent)
    async def refresh_job(self, job_id): print("refresh", job_id)
    async def text(self, t, silent=False, kb=None): print("TEXT:", t[:200])
    async def source_health(self, name, h): pass

async def main():
    s=load_settings()
    radar=JobRadar.__new__(JobRadar)
    radar.settings=s; radar.db=DB(s.db_path)
    radar.sources=JobRadar._build_sources(radar)
    radar.seed_boards()
    ai=FakeAI(); notif=FakeNotifier(s)
    p=Pipeline(s, radar.db, ai, Sheets(s), notif, radar.sources)
    s.cfg["links"]["resolve"]=False
    for name in radar.sources:
        r=await p.run_source(name)
        print(f"== {name}: {r}")
    print("AI calls:", ai.calls, "sent:", len(notif.sent))
    print("stages:", radar.db.stage_counts(__import__('datetime').datetime(2000,1,1,tzinfo=__import__('datetime').timezone.utc)))
    print("boards:", len(radar.db.boards()), "learned:", sum(1 for b in radar.db.boards() if b['origin']=='learned'))
    # second run → should be all duplicates/known
    r=await p.run_source("karriere"); print("karriere 2nd run:", r)

asyncio.run(main())
