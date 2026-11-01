"""Offline replay of the blocker checks on stored analyses (no API calls)."""
import json, sqlite3, sys, copy
from datetime import date
sys.path.insert(0, sys.argv[1])
from jobradar import ai
since = sys.argv[2]
db = sqlite3.connect("file:/var/lib/jobradar/jobradar.sqlite3?mode=ro", uri=True); db.row_factory = sqlite3.Row
rows = db.execute("select * from jobs where analysis is not null and updated>=? and first_seen>=?", (since, since)).fetchall()
chg = {"sent→blocked": [], "blocked→passes": [], "sent: still unverified": []}
n = 0
for r in rows:
    a = json.loads(r["analysis"]); meta = json.loads(r["meta"] or "{}")
    job = {"id": r["id"], "title": r["title"], "company": r["company"], "location": r["location"],
           "description": r["description"] or "", "meta": meta}
    old_bl = set(a.get("blockers") or [])
    claims = (old_bl - {"german_ad"}) | set(a.get("_unverified") or [])
    res = copy.deepcopy(a); res["blockers"] = sorted(claims)
    d = date.fromisoformat((r["updated"] or since)[:10])
    ai.verify_blockers(res, job, d)
    new_bl = set(res["blockers"]) | (old_bl & {"german_ad"})
    n += 1
    tag = f'{r["id"]} [{r["stage"]}] {(r["title"] or "")[:50]} @ {(r["company"] or "")[:25]} | hours={a.get("hours")}'
    if r["stage"] == "sent" and new_bl:
        chg["sent→blocked"].append(f'{tag} → {sorted(new_bl)} proof={res.get("_proof")}')
    elif r["stage"] == "sent" and a.get("_unverified"):
        chg["sent: still unverified"].append(f'{tag} unverified={a.get("_unverified")}')
    if r["stage"] == "blocked" and old_bl and not new_bl:
        chg["blocked→passes"].append(f'{tag} was={sorted(old_bl)} score={r["score"]}')
    if r["stage"] == "blocked" and old_bl - new_bl and new_bl:
        chg.setdefault("blocked, fewer reasons", []).append(f'{tag} {sorted(old_bl)}→{sorted(new_bl)}')
print("analysed", n)
for k, v in chg.items():
    print(f"\n### {k}: {len(v)}"); print("\n".join(v))
