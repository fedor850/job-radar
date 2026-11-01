# tools

| Script | Purpose |
|---|---|
| `render_apps_script.py` | Prints `apps_script/Code.gs` with your `APPS_SCRIPT_SECRET` (and time zone) filled in, for pasting into script.google.com |
| `discover_from_db.py` | Finds and verifies the ATS career sites of employers that posted in your area (names from the bot's database). Run in portions; see [docs/operations.md](../docs/operations.md#finding-more-employers). |
| `discover_boards.py` | Probes a list of company names for Greenhouse / Lever / Ashby / SmartRecruiters / Recruitee / Personio / Workable boards |
| `discover_workday.py`, `discover_rmk.py` | Probes for Workday and SAP SuccessFactors (RMK) career sites |
| `data/companies_vienna*.txt` | Name lists of Vienna employers used for discovery |
| `dev/` | Developer scripts: live AI tests (cost money), dry runs, and **replays** of the decision logic on a copy of a real database |
