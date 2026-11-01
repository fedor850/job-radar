# The Telegram bot

The interface is in Russian (an English interface is on the roadmap). This page translates every element.

## A job card

```
82 · Werkstudent:in Controlling & Reporting
🏢 Example Bank AG · 📍 Wien (hybrid) · 🕒 18–24 ч/нед · 💶 ≈ €1 450 за 20 ч
🗣 объявление EN · немецкий: плюс · 🗓 старт: янв 2027 · ⏳ до 30.11
✅ Почему подходит      → why it fits (your facts against the ad's requirements)
⚠️ Риски               → risks stated in the ad
❔ Не указано           → what the ad does not say (hours, pay, German, start…)
📨 Как подаваться       → exactly what to send and where (e.g. "CV + 3 sentences, no cover letter")
💡 Совет               → what to stress in this application
```

The score (0–100) is the AI's fit estimate. Cards with sound are new ads. Silent cards are the backlog of a newly
added source or `/backfill` results.

Notes that may appear in *Риски* (risks):

| Note | Meaning |
|---|---|
| ⏱ больше 20 ч — предложи работодателю 20 ч/нед | More hours than you want (but ≤ your maximum): propose your hours |
| 💶 ниже твоего минимума | Pay below `candidate.min_monthly_salary` |
| ⚠️ ИИ заподозрил: … — но в тексте объявления этого нет; проверь сам | The AI suspected a stop factor but could not prove it with a quote: check the ad yourself |
| ⚠️ Объявление на немецком … рабочий язык английский | A German ad from a company on your `english_workplace` list |
| ⚠️ Источник дал только фрагмент описания | Only a snippet was available (Jooble/Careerjet): check hours and language on the original |

## Buttons

| Button | Translation | Effect |
|---|---|---|
| 🚀 Откликнуться на сайте компании / в LinkedIn | Apply on the company site / on LinkedIn | Opens the best apply link. The bot replaces job-board links with the company's own posting when it finds it. |
| 🔎 Найти на сайте компании | Find on the company site | Google search for the posting on the company's site |
| 📄 Оригинал объявления | Original ad | The source page |
| 👥 Выпускники · 🎯 Рекрутеры · 🏢 Сотрудники | Alumni · Recruiters · Employees | LinkedIn people searches at this company (alumni use `referrals.linkedin_school_ids`) |
| ✅ Подался | Applied | Adds the job to the tracker and sets a follow-up reminder in 7 days |
| ⭐ Сохранить | Save | Adds it to the tracker as "saved" |
| 🙅 Не интересно | Not interesting | Asks for a reason: 🇩🇪 German · ⏱ hours · 🎓 level · 🧭 not my field · 📍 place · 💶 money · 🏢 company · 🤷 other. The last 20 reactions are shown to the AI on every evaluation. |
| 📋 Для Claude: CV / CV + письмо / CV + короткий текст | For Claude: CV / CV + letter / CV + short text | Sends a `.md` file with the full ad and an instruction for your Claude Project ([claude-project/](../claude-project/README.md)) |
| ↩️ … | Undo | Reverts the last action |

## Commands

| Command | What it does |
|---|---|
| `/start` | Greeting; the first sender becomes the owner if `TELEGRAM_CHAT_ID` is not set |
| `/status` | Uptime, every source with its last run and errors, AI spend this month, current settings |
| `/stats [days]` | Funnel for the last 7 (or N) days: seen → evaluated → sent → applied, by source |
| `/minscore N` | Notification threshold (default 40). Higher means fewer, better cards. |
| `/prefs <text>` | A standing wish for the AI ("no call-centre jobs", "prefer fintech"); `/prefs` lists them, `/prefs del 2` and `/prefs clear` remove them |
| `/pause`, `/resume` | Stop / restart notifications. Jobs found meanwhile are held and delivered later. |
| `/backfill [days]`, `/more` | Send still-open jobs of the last N days (best first), then the next batch |
| `/tracker` | Link to the Google Sheet |
| `/followups` | Applications that are due for a follow-up |
| `/addcompany <link>` | Watch a company: give a link to any of its job ads or its career page |
| `/sources` | Which company career sites are watched |
| `/test` | Sends a sample card |

## Automatic messages

| When | Message |
|---|---|
| 10:00 daily | Follow-up reminders (7+ days without a reply), with a draft e-mail button |
| 20:30 daily | Strong jobs (≥ 75) you have not reacted to |
| Sunday 19:00 | Weekly statistics |
| Any time | E-mail events: 📨 confirmation, 📝 test, 🎤 interview (📅 add to calendar), 🏆 offer, ❌ rejection |
| On problems | ⚠️ a source is down / stuck / returns nothing for 8 h · 💸 AI budget at 70% / 90% · 💳 / 🔑 API credit or key problem · 🐕 watchdog restarted the bot |
