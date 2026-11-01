import sys, os, time, logging, yaml
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root
logging.basicConfig(level=logging.WARNING)
from jobradar.sources.sites import SiteSource
from jobradar.util import detect_lang
cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")))
only = sys.argv[1:] or None
for site in cfg["sources"]["sites"]:
    if only and site["name"] not in only: continue
    p = dict(site); p["max_details"] = 4
    if "keywords" in p: p["keywords"] = p["keywords"][:2]
    p["pages"] = 1
    src = SiteSource(None, p)
    t = time.time()
    try:
        jobs = src.fetch()
    except Exception as e:
        print("=====", site["name"], "ERR", type(e).__name__, e); continue
    print("=====", site["name"], len(jobs), f"{time.time()-t:.0f}s")
    for j in jobs:
        print(f"  {'DROP' if j.meta.get('drop') else '    '} {j.title[:55]!r:58} | {j.company[:28]!r:30} | {j.location[:30]!r} | {j.posted_at} | desc={len(j.description)} {detect_lang(j.description)[0]} | apply={j.apply_url[:50]}")
