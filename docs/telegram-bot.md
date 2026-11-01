# The Telegram bot

## A job card

```
82 · Werkstudent:in Controlling & Reporting
🏢 Example Bank AG · 📍 Wien · hybrid
working student · ⏱ 18–24 h/week · 💶 ≈€1,450/month for 20 h
🇬🇧 ad in English · German is a plus · 🗓 start: Jan 2027 · ⏳ until 30.11

✅ Why it fits         your facts against the ad's requirements
⚠️ Risks              gaps stated in the ad
❔ Not stated:         what the ad does not say (hours, pay, German, start…)
📨 How to apply:       exactly what to send and where (e.g. "CV + 3 sentences, no cover letter")
💡 tip                 what to stress in this application
```

The score (0–100) is the AI's fit estimate. Cards with sound are new ads. Silent cards are the backlog of a newly
added source or `/backfill` results.

Notes that may appear under **Risks**:

| Note | Meaning |
|---|---|
| ⏱ more than 20 h — propose 20 h/week to the employer | More hours than `candidate.target_weekly_hours`, but not more than your maximum |
| 💶 below your minimum | Pay below `candidate.min_monthly_salary` |
| ⚠️ The AI suspected: … — but the ad does not say so; check it yourself | The AI suspected a stop factor but could not prove it with a quote |
| ⚠️ The ad is in German … the company's working language is English | A German ad from a company on your `english_workplace` list |
| ⚠️ The source gave only a snippet of the ad | Only a snippet was available (Jooble/Careerjet): check hours and language on the original |

## Buttons

| Button | Effect |
|---|---|
| 🚀 Apply on the company site / on LinkedIn | Opens the best apply link. The bot replaces job-board links with the company's own posting when it finds it. |
| 🔎 Find on the company site | Google search for the posting on the company's site |
| 📄 Original ad | The source page |
| 👥 Alumni · 🎯 Recruiters · 🏢 Employees | LinkedIn people searches at this company (alumni use `referrals.linkedin_school_ids`) |
| ✅ Applied | Adds the job to the tracker and sets a follow-up reminder in 7 days |
| ⭐ Save | Adds it to the tracker as "saved" |
| 🙅 Not interested | Asks for a reason: 🗣 language · ⏱ hours · 🎓 level · 🧭 not my field · 📍 location · 💶 pay · 🏢 company · 🤷 other. The last 20 reactions are shown to the AI on every evaluation. |
| 📋 For Claude: CV / CV + letter / CV + short text | Sends a `.md` file with the full ad and an instruction for your Claude Project ([claude-project/](../claude-project/README.md)) |
| ↩️ Undo / Restore / Back | Reverts the last action |

## Commands

| Command | What it does |
|---|---|
| `/start` | Greeting; the first sender becomes the owner if `TELEGRAM_CHAT_ID` is not set |
| `/status` | Uptime, every source with its last run and errors, AI spend this month, current settings |
| `/stats [days]` | The last 7 (or N) days: jobs checked → sent → applied, replies, sources |
| `/minscore N` | Notification threshold (default 40). Higher means fewer, better cards. |
| `/prefs <text>` | A standing wish for the AI ("no call-centre jobs", "prefer fintech"). `/prefs` lists them; `/prefs del 2` and `/prefs clear` remove them. |
| `/pause`, `/resume` | Stop / restart notifications. Jobs found meanwhile are held and delivered later. |
| `/backfill [days]`, `/more` | Send still-open jobs of the last N days (best first), then the next batch |
| `/tracker` | Link to the Google Sheet |
| `/followups` | Applications due for a follow-up |
| `/addcompany <link>` | Watch a company: give a link to any of its job ads or its career page |
| `/sources` | Which company career sites are watched |
| `/test` | Sends a sample card |

## Automatic messages

| When | Message |
|---|---|
| 10:00 daily | Follow-up reminders (7+ days without a reply), with a "draft the e-mail" button |
| 20:30 daily | Strong jobs (≥ 75) you have not reacted to |
| Sunday 19:00 | Weekly statistics |
| Any time | E-mail events: 📨 confirmation, 📝 test, 🎤 interview (📅 add to calendar), 🏆 offer, ❌ rejection |
| On problems | ⚠️ a source is down / stuck / returns nothing for 8 h · 💸 AI budget at 70% / 90% · 💳 / 🔑 API credit or key problem · 🐕 the watchdog restarted the bot |
