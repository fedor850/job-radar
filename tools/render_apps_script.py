#!/usr/bin/env python3
"""Print apps_script/Code.gs with your APPS_SCRIPT_SECRET (and time zone) filled in, ready to paste into
script.google.com. The secret is read from secrets.env; nothing is written to the repository.

usage: python3 tools/render_apps_script.py [path/to/secrets.env] [Europe/Vienna] > Code.gs.local
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
secrets = Path(sys.argv[1]) if len(sys.argv) > 1 else next(
    (p for p in (ROOT.parent / "secrets.env", ROOT / "secrets.env") if p.exists()), None)
if not secrets or not secrets.exists():
    sys.exit("secrets.env not found — pass its path as the first argument")
env = dict(re.findall(r"^([A-Z_]+)=(.*)$", secrets.read_text(encoding="utf-8-sig"), re.M))
secret = env.get("APPS_SCRIPT_SECRET", "").strip().strip("'\"")
if len(secret) < 16:
    sys.exit("APPS_SCRIPT_SECRET is missing or shorter than 16 characters (use e.g. `openssl rand -hex 24`)")
code = (ROOT / "apps_script" / "Code.gs").read_text(encoding="utf-8").replace("__APPS_SCRIPT_SECRET__", secret)
if len(sys.argv) > 2:
    code = code.replace("var TZ = 'Europe/Vienna';", f"var TZ = '{sys.argv[2]}';")
sys.stdout.write(code)
