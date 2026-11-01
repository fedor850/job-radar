# Google tracker, Gmail and Calendar (Apps Script bridge)

This part is optional, but it makes the bot much more useful:

- a **Google Sheet** with your application funnel, every application and a feed of all jobs sent;
- **inbox reading:** confirmations, rejections, online tests, interview invitations and offers update the tracker
  automatically and appear in Telegram (interviews with an "add to calendar" button);
- **LinkedIn job-alert e-mails** as a backup source when LinkedIn throttles the server.

## How it works and what it can access

The bot never gets your Google password or an OAuth token. Instead, a small **Google Apps Script** web app runs
inside your own Google account. The bot calls it over HTTPS with a shared secret. The script can:

| Action | Access |
|---|---|
| `setup`, `log_job`, `upsert_tracker`, `get_tracker` | One spreadsheet it creates, "Job Radar — Application Tracker" (sheets *Dashboard*, *Applications*, *Feed*) |
| `get_emails` | Reads (never sends or deletes) e-mails of the last days, excluding Sent, Spam, Promotions, Social and Forums |
| `get_job_alerts` | Reads LinkedIn job-alert e-mails |
| `create_event` | Creates an event in your default calendar when you press "📅 add to calendar" |

Every request without the right secret is rejected. The code is in [`apps_script/Code.gs`](../apps_script/Code.gs),
about 300 lines, so you can read all of it.

> E-mail subjects and the first part of e-mail bodies go to the Claude API for classification. Use the bridge only
> with a mailbox where that is acceptable to you.

## Setup (10 minutes)

1. **Make a secret:** on your computer run `openssl rand -hex 24` and put the result into `secrets.env`:
   ```ini
   APPS_SCRIPT_SECRET=3f6c…(48 characters)
   ```
2. **Prepare the code with your secret filled in:**
   ```bash
   python3 app/tools/render_apps_script.py secrets.env Europe/Vienna > Code.gs.local
   ```
   (The second argument is your time zone; the file stays out of git.)
3. Open **[script.google.com](https://script.google.com)** while signed in with the Google account you apply from
   → **New project** → name it "Job Radar Bridge" → replace everything in `Code.gs` with the content of
   `Code.gs.local` → **Save**.
4. Select the function **`setup`** in the toolbar → **Run** → *Review permissions* → choose your account →
   *Advanced* → *Go to Job Radar Bridge (unsafe)* → **Allow**. Google calls every personal script "unsafe" because
   Google did not review it. The script and the account are both yours.
   The run creates the spreadsheet.
5. **Deploy → New deployment** → gear icon → **Web app** → *Execute as:* **Me**, *Who has access:* **Anyone** →
   **Deploy** → copy the **Web app URL** (`https://script.google.com/macros/s/…/exec`).
   ("Anyone" only means the URL is reachable. Without the secret every call fails.)
6. Put it into `secrets.env`:
   ```ini
   APPS_SCRIPT_URL=https://script.google.com/macros/s/AKfy…/exec
   ```
7. Deploy the bot again: `bash app/deploy/deploy.sh`. In Telegram, `/tracker` now returns the sheet's link.

## LinkedIn job alerts (optional backup source)

On linkedin.com, run your usual searches (city, keywords, "past 24 hours") and switch on **Job alert → e-mail,
daily or as soon as possible**. The bot reads these e-mails every 5 minutes and evaluates every job in them. A job
it already found by itself is skipped, so you never get duplicates.

## Updating the script

When `apps_script/Code.gs` changes (see the changelog), repeat steps 2–3 and then **Deploy → Manage deployments →
edit (pencil) → Version: New version → Deploy**. The URL stays the same.

**Migrating an older sheet.** If your spreadsheet was created by a version with other sheet names or status labels,
fill in `LEGACY` at the top of `Code.gs` before pasting it, for example
`{title: 'My old title', sheets: {'Old applications sheet': 'Applications', 'Old feed': 'Feed', 'Old summary': 'Dashboard'},
statuses: {'Old applied label': '✅ Applied'}}`. On the next request the script renames the sheets, rewrites the headers,
maps the statuses and rebuilds the dashboard, once.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/tracker` says the bridge is not configured | `APPS_SCRIPT_URL` / `APPS_SCRIPT_SECRET` missing in `secrets.env`, or you did not redeploy |
| "bad secret" in the logs | The secret in the script differs from `secrets.env`. Re-render and paste again, then deploy a new version. |
| Sheet doesn't update | Deploy → Manage deployments: the web app must be active, and "Execute as: Me" |
| Authorization error after editing the script | Run `setup` once more and allow the new permissions |
