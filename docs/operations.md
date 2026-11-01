# Operations

All commands are run from your `my-job-radar` folder, the parent of the repository.

## Everyday commands

| Task | Command |
|---|---|
| Deploy / update after changing code, `config.yaml` or `profile.md` | `bash app/deploy/deploy.sh` |
| Last log lines | `bash app/deploy/deploy.sh logs 200` |
| Service status / restart / stop | `bash app/deploy/deploy.sh status` · `restart` · `stop` |
| Output of the last install | `bash app/deploy/deploy.sh install-log` |
| Get the latest version | `git -C app pull && bash app/deploy/deploy.sh` |

`deploy.sh` is idempotent. It copies the code (without tests, docs and examples), your `config.yaml` and
`profile.md`, and `secrets.env` (to `/etc/jobradar`, mode 640), then runs `deploy/install.sh` on the server. The
install script sets up packages, a virtualenv, the systemd service, the watchdog timer, the firewall and automatic
updates.

## Where things are on the server

| Path | Content |
|---|---|
| `/opt/jobradar/app` | Code + your `config.yaml` and `profile.md` |
| `/opt/jobradar/venv` | Python virtualenv |
| `/etc/jobradar/secrets.env` | Secrets |
| `/var/lib/jobradar/jobradar.sqlite3` | Database |
| `/var/lib/jobradar/backups/` | Nightly database backups (04:00, 7 kept) |
| `jobradar.service`, `jobradar-watchdog.timer` | systemd units |

## Monitoring: what the bot tells you by itself

- **A source fails** several runs in a row (`health.alert_after_fails`), **hangs**, or **returns no jobs for 8 h**
  (a site changed its layout): ⚠️ message, and ✅ when it recovers.
- **AI spend** at 70% of `ai.monthly_budget_usd` (or a forecast above it) and at 90%: 💸. **Credit exhausted / invalid
  key**: 💳 / 🔑. Jobs found meanwhile are kept for 4 days and evaluated when the API works again.
- **Watchdog** (separate timer, every 10 min): restarts the bot if the service is down, if no source finished for
  30 min, or if memory exceeds 2.5 GB, and tells you (🐕). It also warns when disk space is below 3 GB.
- **Not covered:** the whole server is down. If the bot is silent and `/status` does not answer, power-cycle the
  server in your provider's console.

## Updating

```bash
git -C app pull
bash app/deploy/deploy.sh
```

Read [CHANGELOG.md](../CHANGELOG.md) first. New settings always come with defaults that keep the old behaviour.
If `apps_script/Code.gs` changed, also update the Apps Script ([google-bridge.md](google-bridge.md#updating-the-script)).

Your own changes to the code: keep them in a fork or a branch, and run the tests before deploying:

```bash
cd app && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
pytest && ruff check .
```

## Backups and moving to another server

The database is the only state on the server; the tracker also lives in the Google Sheet. To move:

```bash
ssh -i keys/jobradar_ed25519 root@OLD_IP "systemctl stop jobradar && sqlite3 /var/lib/jobradar/jobradar.sqlite3 '.backup /tmp/jr.sqlite3'"
scp -i keys/jobradar_ed25519 root@OLD_IP:/tmp/jr.sqlite3 .
# set SERVER_IP=NEW_IP in secrets.env, then:
bash app/deploy/deploy.sh
scp -i keys/jobradar_ed25519 jr.sqlite3 root@NEW_IP:/var/lib/jobradar/jobradar.sqlite3
ssh -i keys/jobradar_ed25519 root@NEW_IP "chown jobradar:jobradar /var/lib/jobradar/jobradar.sqlite3 && systemctl restart jobradar"
```

## Finding more employers

The bot learns new ATS boards by itself. To add many at once:

- `tools/discover_from_db.py`: finds the career systems of every employer that posted in your area in the last N
  days (names come from the bot's own database), then verifies them with the real adapters. It makes a lot of
  requests: run it in portions (`probe … 150` seconds per call; progress is kept in `pos.txt`), and on the
  server only with `nice -n 19`. Review the result by hand before adding it to `companies`.
- `tools/discover_boards.py`, `discover_workday.py`, `discover_rmk.py`: probe a list of company names for
  Greenhouse/Lever/…, Workday and SuccessFactors sites.

## Measuring a rule change before deploying it

`tools/dev/replay_hours.py`, `replay_evidence.py` and `replay_field.py` re-run the decision logic on stored
evaluations from a **copy** of the database, which costs nothing or very little. Example: after changing a check in
`jobradar/ai.py`:

```bash
ssh root@SERVER "rsync -a /opt/jobradar/app/ /tmp/jr_test/"      # plus your changed files
ssh root@SERVER "cd /tmp && /opt/jobradar/venv/bin/python /tmp/jr_test/tools/dev/replay_hours.py /tmp/jr_test 2026-10-30"
```

It prints which sent jobs would now be hidden and which hidden jobs would now be sent.

## Troubleshooting

| Symptom | Where to look / what to do |
|---|---|
| Deploy: `Permission denied (publickey)` | The public key was not added when the server was created. Add `keys/jobradar_ed25519.pub` to `/root/.ssh/authorized_keys` via the provider's web console. |
| Deploy: `SERVER_IP is empty` / `config.yaml not found` | Private files must be in the parent folder of `app/` (or set `JOBRADAR_HOME`) |
| Bot does not answer `/start` | `deploy.sh status`, then `logs 100`: a wrong `TELEGRAM_BOT_TOKEN` shows up as `Unauthorized` |
| Bot answers someone else / nobody | Set `TELEGRAM_CHAT_ID` in `secrets.env` and deploy |
| No cards for hours | `/status`: are sources running? `/stats`: are jobs seen but `below_threshold` or `blocked`? Lower `/minscore` or relax `filters.blocking`. |
| Too many irrelevant cards | Press 🙅 with a reason (the bot learns), `/prefs …`, `/minscore 50`, refine `profile.md` |
| `LinkedIn throttled (429)` in the logs | Normal from data-centre IPs, handled by automatic pauses. Keep LinkedIn `interval_sec` ≥ 300. |
| A board-type source returns `HTTP 403` for days | The site blocks server IPs. Disable it (`enabled: false`). |
| `💳` message | Add credit at platform.claude.com. Missed jobs are evaluated automatically. |
| Sheet / e-mail not updating | [google-bridge.md → Troubleshooting](google-bridge.md#troubleshooting) |
