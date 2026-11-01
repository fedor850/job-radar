# Getting started

This guide takes you from nothing to a running bot in about an hour. You do not need to program. You will
copy a few commands into a terminal and edit three text files.

> **Shortcut:** an AI coding assistant such as [Claude Code](https://claude.com/claude-code) can do most of these
> steps for you. Open it in your `my-job-radar` folder and say *"Set up Job Radar for me by following
> app/docs/getting-started.md. Ask me for anything you need."* It can't make the accounts in step 1 for you, but it
> can write your profile, fill in the config, run the deploy and check the logs.

## What you need

- **A computer with a bash terminal and `rsync`.** macOS and Linux have both. On Windows, use
  [WSL](https://learn.microsoft.com/windows/wsl/install) (Ubuntu): open the "Ubuntu" app and run every command there.
- **A Telegram account.**
- **A credit/debit card** for the Claude API (pay as you go, about $8–16 per month) and the server (about €5–6 per month).

## 1. Create the accounts

### 1.1 Telegram bot (free)
1. In Telegram, open **@BotFather** → `/newbot` → choose a name and a username ending in `bot`.
2. BotFather replies with a **token** like `123456789:AA…`. Keep it; it goes into `secrets.env`.
3. Open **@userinfobot** and send it any message. It replies with your numeric **ID**. This is your
   `TELEGRAM_CHAT_ID`. With it, only you can control the bot.

### 1.2 Claude API key
1. Sign up at **[platform.claude.com](https://platform.claude.com)** → *Billing*: add a card and a few dollars of credit.
2. *Settings → Limits*: set a **monthly spend limit** (for example $20). The bot warns you before it is reached.
3. *API keys → Create key* → copy the key (`sk-ant-…`).

### 1.3 A small Linux server
Any VPS with **Ubuntu 24.04**, 2 vCPU and 4 GB RAM works. These steps use Hetzner (CX23, about €5/month):

1. First create an SSH key on your computer (step 2 below does this). You will paste the **public** half here.
2. **[console.hetzner.com](https://console.hetzner.com)** → *New project* → *Add server*: location near you,
   image **Ubuntu 24.04**, type **CX23**, *SSH keys → Add SSH key* → paste the contents of
   `keys/jobradar_ed25519.pub` → *Create & Buy now*.
3. Copy the server's **IPv4 address**.

Everything the bot needs (Python, firewall, automatic security updates, the service) is installed by the deploy
script. You never need to log in to the server yourself.

## 2. Get the code and create your private folder

```bash
mkdir my-job-radar && cd my-job-radar
git clone https://github.com/fedor850/job-radar.git app
cp app/examples/config.example.yaml config.yaml
cp app/examples/profile.example.md  profile.md
cp app/examples/secrets.example.env secrets.env
mkdir keys && ssh-keygen -t ed25519 -f keys/jobradar_ed25519 -N "" -C job-radar
cat keys/jobradar_ed25519.pub        # ← paste this into Hetzner (step 1.3)
```

You now have:

```
my-job-radar/
├── app/                    the code (this repository) — update it with `git pull`
├── config.yaml             your settings
├── profile.md              who you are and what you want — the AI reads it for every job
├── secrets.env             tokens and keys — never share this file
└── keys/jobradar_ed25519   SSH key for the server
```

Your private files are outside `app/`, so `git` never sees them.

## 3. Fill in `secrets.env`

Open it in any text editor and set at least:

```ini
TELEGRAM_BOT_TOKEN=123456789:AA...        # from @BotFather
TELEGRAM_CHAT_ID=987654321                # from @userinfobot
ANTHROPIC_API_KEY=sk-ant-...              # from platform.claude.com
SERVER_IP=203.0.113.10                    # your server
```

Leave the Google and job-API lines empty for now.

## 4. Write `profile.md`: the most important step

The AI decides on every job from this file alone. Replace the example with your own data. Keep the four sections:

1. **Hard constraints**: city, hours per week, start date, level, languages with levels, minimum pay, work permit.
   Write them as rules ("Jobs that require German above B1 are NOT possible").
2. **Education**: degrees, grades, relevant courses, certificates.
3. **Experience**: roles with dates and numbers (targets, results, team sizes, tools).
4. **What fits best**: strong-fit fields, adjacent fields you would also take, and fields that do not fit.

Tip: paste your CV and a description of what you are looking for into Claude and ask it to *"rewrite this into
the four sections of profile.example.md, keeping every number"*. Check the result, because every fact counts.

## 5. Adjust `config.yaml`

For a first run, change only the top two sections:

```yaml
candidate:
  name: Your Name
  pitch: "One line with your strongest facts — used in follow-up e-mail drafts."
  claude_project: job search
  target_weekly_hours: 20      # what you want
  max_weekly_hours: 25         # more than this hides the job
  min_monthly_salary: 1200     # EUR gross for target_weekly_hours (only a note, never hides a job)

location:
  city: Vienna
  country_code: AT
```

**Not in Vienna?** Read [adapting.md](adapting.md) first. It explains which sources to switch off, how to set your
search area, and how to remove the German-language rules.

**Looking for full-time work?** Remove `fulltime_only` from `filters.blocking`.

## 6. Deploy

```bash
bash app/deploy/deploy.sh
```

The first run takes 3–5 minutes. It installs Python, a firewall, automatic security updates, a headless browser
and the bot service. The last lines should show `active (running)` and `>> done`.

Then, in Telegram:

1. Send **`/start`** to your bot. It greets you and offers to send the jobs that are already open.
2. Send **`/test`** to see a sample card, and **`/status`** to see every source and the AI spend.

Within the first hour the first real cards arrive. Mark jobs that don't fit with **🙅 Not interested**
and pick a reason. The bot uses your reasons in the next evaluations.

## 7. Optional extras

- **Application tracker and inbox watcher** (Google Sheet + Gmail + Calendar): [google-bridge.md](google-bridge.md)
- **Tailored CV and cover letter for each job** with a Claude Project: [../claude-project/README.md](../claude-project/README.md)
- **Your own CV design**: [../cv_builder/README.md](../cv_builder/README.md)

## 8. Day to day

- React to cards within an hour or two. Early applicants are read first.
- Press **✅ Applied** after applying. The bot adds the job to the tracker and reminds you to follow up after 7 days.
- Change things without a deploy: `/minscore 55` (stricter), `/prefs no call-centre jobs` (a wish for the AI),
  `/pause` and `/resume`.
- After editing `profile.md` or `config.yaml`, run `bash app/deploy/deploy.sh` again.

## Updating

```bash
cd my-job-radar/app && git pull && cd .. && bash app/deploy/deploy.sh
```

Read [CHANGELOG.md](../CHANGELOG.md) for new settings. New keys always have safe defaults, so an old
`config.yaml` keeps working.

Something not working? See [operations.md → Troubleshooting](operations.md#troubleshooting).
