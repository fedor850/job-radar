"""Find which public ATS a company uses and how many Austria/Vienna jobs it has.

usage: python tools/discover_boards.py names.txt out.json
"""
import json
import sys
import re
import requests
from concurrent.futures import ThreadPoolExecutor

AT = re.compile(r"\b(austria|österreich|oesterreich|vienna|wien)\b", re.I)
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (JobRadar board discovery)"
A = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64, max_retries=0)
S.mount("https://", A)

def slugs(name):
    n = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("&", " ")).split()
    n = [t for t in n if t not in ("gmbh", "ag", "se", "the")]
    out = []
    for c in ["".join(n), "-".join(n), n[0] if n and (len(n) == 1 or len(n[0]) >= 5) else ""]:
        if c and len(c) >= 3 and c not in out:
            out.append(c)
    return out

def j(url):
    try:
        r = S.get(url, timeout=12)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None

def gh(s, eu):
    d = j(f"https://boards-api{'.eu' if eu else ''}.greenhouse.io/v1/boards/{s}/jobs")
    if not d or "jobs" not in d: return None
    jobs = d["jobs"]; return len(jobs), sum(1 for x in jobs if AT.search((x.get("location") or {}).get("name", "") or "")), {"eu": True} if eu else {}

def lever(s, eu):
    d = j(f"https://api{'.eu' if eu else ''}.lever.co/v0/postings/{s}?mode=json")
    if not isinstance(d, list): return None
    return len(d), sum(1 for x in d if AT.search(json.dumps(x.get("categories") or {}))), {"eu": True} if eu else {}

def ashby(s):
    d = j(f"https://api.ashbyhq.com/posting-api/job-board/{s}")
    if not d or "jobs" not in d: return None
    return len(d["jobs"]), sum(1 for x in d["jobs"] if AT.search(json.dumps([x.get("location"), x.get("address"), x.get("secondaryLocations")]))), {}

def sr(s):
    d = j(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1")
    if not d or not d.get("totalFound"): return None
    a = j(f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1&country=at") or {}
    return d["totalFound"], a.get("totalFound", 0), {}

def recruitee(s):
    d = j(f"https://{s}.recruitee.com/api/offers/")
    if not d or "offers" not in d: return None
    return len(d["offers"]), sum(1 for x in d["offers"] if AT.search(f"{x.get('location','')} {x.get('country','')} {x.get('city','')}")), {}

def personio(s, tld):
    try:
        r = S.get(f"https://{s}.jobs.personio.{tld}/xml", timeout=12, allow_redirects=False)
    except requests.RequestException:
        return None
    if r.status_code != 200 or "<workzag-jobs" not in r.text: return None
    n = r.text.count("<position>"); a = len(AT.findall(" ".join(re.findall(r"<office>(.*?)</office>", r.text))))
    return n, a, {"tld": tld}

def workable(s):
    d = j(f"https://apply.workable.com/api/v1/widget/accounts/{s}")
    if not d or "jobs" not in d: return None
    return len(d["jobs"]), sum(1 for x in d["jobs"] if AT.search(f"{x.get('country','')} {x.get('city','')}")), {}

def teamtailor(s):
    try:
        r = S.get(f"https://{s}.teamtailor.com/jobs", timeout=12, allow_redirects=False)
    except requests.RequestException:
        return None
    if r.status_code != 200 or "teamtailor" not in r.text.lower():
        return None
    n = len(set(re.findall(r'/jobs/(\d+)-', r.text)))
    return n, len(AT.findall(r.text)), {}

def join(s):
    try:
        r = S.get(f"https://join.com/companies/{s}", timeout=12, allow_redirects=False)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    n = len(set(re.findall(r'/companies/' + re.escape(s) + r'/(\d+)-', r.text)))
    return (n, len(AT.findall(r.text)), {}) if n else None

PROBES = [("greenhouse", lambda s: gh(s, False)), ("greenhouse", lambda s: gh(s, True)), ("lever", lambda s: lever(s, False)),
          ("lever", lambda s: lever(s, True)), ("ashby", ashby), ("smartrecruiters", sr), ("recruitee", recruitee),
          ("personio", lambda s: personio(s, "de")), ("personio", lambda s: personio(s, "com")), ("workable", workable),
          ("teamtailor", teamtailor), ("join", join)]

def run(t):
    name, slug, kind, fn = t
    try:
        r = fn(slug)
    except Exception:
        return None
    return (name, kind, slug) + r if r else None

names = [l.strip() for l in open(sys.argv[1], encoding="utf-8") if l.strip() and not l.startswith("#")]
tasks = [(n, s, k, f) for n in names for s in slugs(n) for k, f in PROBES]
print("tasks", len(tasks), flush=True)
found = []
with ThreadPoolExecutor(32) as ex:
    for res in ex.map(run, tasks):
        if res:
            found.append(res); print("HIT", res, flush=True)
json.dump(found, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
print("done", len(found))
