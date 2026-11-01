#!/usr/bin/env python3
"""Job Radar watchdog — runs every 10 min as root via jobradar-watchdog.timer, independently of the bot.

Catches what systemd's Restart=always does not: a bot that is alive but stuck (no source finished a run for
30+ min), a crash loop at start-up, memory bloat and a full disk — restarts the service where that helps and
tells the owner in Telegram directly (Bot API), so a silent bot never goes unnoticed. Stdlib only."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SECRETS = "/etc/jobradar/secrets.env"
DB = "/var/lib/jobradar/jobradar.sqlite3"
STATE = "/var/lib/jobradar-watchdog.json"
STALE_MIN = 30          # no source finished a run for this long → stuck
GRACE_MIN = 15          # do not judge a service that started less than 15 min ago
RSS_MAX_MB = 2500       # the bot normally uses 0.7–0.9 GB
DISK_MIN_GB = 3
REALERT_H = 6


def secrets() -> dict:
    out = {}
    for line in open(SECRETS, encoding="utf-8"):
        line = line.strip().replace("\r", "")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()


def send(sec: dict, text: str) -> None:
    token, chat = sec.get("TELEGRAM_BOT_TOKEN"), sec.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": "🐕 Сторож Job Radar: " + text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20).read()
    except Exception as e:  # noqa: BLE001
        print("telegram send failed:", e)


def service_age_min() -> float:
    mono = sh("systemctl", "show", "jobradar", "-p", "ActiveEnterTimestampMonotonic", "--value")
    try:
        up = float(open("/proc/uptime").read().split()[0])
        return (up - int(mono) / 1e6) / 60 if mono and int(mono) else 0.0
    except ValueError:
        return 0.0


def last_run_age_min() -> float | None:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
        row = con.execute("SELECT MAX(last_run) FROM source_health").fetchone()
        con.close()
    except sqlite3.Error as e:
        print("db read failed:", e)
        return None
    if not row or not row[0]:
        return None
    last = datetime.fromisoformat(row[0])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 60


def rss_mb() -> float:
    pid = sh("systemctl", "show", "jobradar", "-p", "MainPID", "--value")
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return 0.0


def main() -> None:
    sec = secrets()
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {}
    now = time.time()
    problems: dict[str, str] = {}
    restart = False

    state = sh("systemctl", "is-active", "jobradar")
    if state == "activating":  # crash loop: systemd keeps restarting it
        st["activating"] = st.get("activating", 0) + 1
        if st["activating"] >= 2:
            tail = sh("journalctl", "-u", "jobradar", "-n", "6", "--no-pager", "-o", "cat")[-600:]
            problems["crashloop"] = f"бот падает при запуске (systemd перезапускает его по кругу). Последние строки лога:\n{tail}"
    else:
        st["activating"] = 0
        if state != "active":
            problems["down"] = f"служба была в состоянии «{state}» — перезапустил."
            restart = True
        elif service_age_min() > GRACE_MIN:
            age = last_run_age_min()
            if age is not None and age > STALE_MIN:
                problems["stuck"] = f"бот завис: {int(age)} мин ни один источник не завершил проверку — перезапустил."
                restart = True
            mem = rss_mb()
            if mem > RSS_MAX_MB:
                problems["memory"] = f"бот занял {int(mem)} МБ памяти (норма 700–900) — перезапустил."
                restart = True
    free_gb = shutil.disk_usage("/").free / 1e9
    if free_gb < DISK_MIN_GB:
        problems["disk"] = f"на сервере осталось {free_gb:.1f} ГБ диска."

    if restart:
        restarts = [t for t in st.get("restarts", []) if now - t < 3600] + [now]
        st["restarts"] = restarts
        sh("systemctl", "restart", "jobradar")
        if len(restarts) >= 3:
            problems["loop"] = "3 перезапуска за час не помогли — нужна проверка (логи: deploy.sh logs 200)."

    alerted = st.get("alerted", {})
    for kind, text in problems.items():
        if now - alerted.get(kind, 0) > REALERT_H * 3600 or kind in ("down", "stuck", "memory"):
            send(sec, text)
            alerted[kind] = now
    if not problems and st.get("had_problem") and state == "active" and service_age_min() > GRACE_MIN:
        send(sec, "✅ всё снова в порядке.")
        st["had_problem"] = False
    elif problems:
        st["had_problem"] = True
    st["alerted"] = alerted
    st["last_check"] = now
    json.dump(st, open(STATE, "w"))
    print("ok" if not problems else "; ".join(problems))


if __name__ == "__main__":
    main()
