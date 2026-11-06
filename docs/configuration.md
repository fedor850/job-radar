# Configuration reference

Job Radar reads three private files. `deploy/deploy.sh` looks for them in the parent folder of the repository
(`JOBRADAR_HOME`) and falls back to the repository root, where they are git-ignored.

| File | What it holds | Template |
|---|---|---|
| `profile.md` | You: constraints, education, experience, what fits. The AI reads it on every evaluation. | [examples/profile.example.md](../examples/profile.example.md) |
| `config.yaml` | Behaviour: candidate settings, region, filters, AI, sources, company list. | [examples/config.example.yaml](../examples/config.example.yaml) |
| `secrets.env` | Tokens, API keys, server IP. | [examples/secrets.example.env](../examples/secrets.example.env) |

On the server they are stored as `/opt/jobradar/app/config.yaml`, `/opt/jobradar/app/profile.md` and
`/etc/jobradar/secrets.env`. To use other locations, set `JOBRADAR_CONFIG`, `JOBRADAR_PROFILE`, `JOBRADAR_SECRETS`
and `JOBRADAR_DATA`.

After any change: `bash app/deploy/deploy.sh`. Only `/minscore`, `/prefs` and `/pause` take effect without a deploy.

---

## `profile.md`

Free Markdown text, appended to the scoring and triage prompts. Write it in English. What matters:

- **Hard constraints as explicit rules.** "Jobs that require German above B1 are NOT possible", "Full-time only is
  NOT possible unless part-time is allowed". The model applies them literally.
- **Numbers.** "met a EUR 50k monthly target", "team of 6", "GPA 1.5". The *why it fits* lines quote them.
- **Adjacent fields.** Without them, good-but-unexpected jobs score low.

The deterministic checks (hours, German level, seniority, location) do not read the profile. They use `config.yaml`.

## `secrets.env`

| Key | Required | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Token from @BotFather |
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `SERVER_IP` | yes (deploy) | Server address used by `deploy.sh` |
| `TELEGRAM_CHAT_ID` | recommended | Your chat ID. If empty, the first person who sends `/start` becomes the owner. |
| `APPS_SCRIPT_URL`, `APPS_SCRIPT_SECRET` | optional | Google tracker / Gmail bridge ([google-bridge.md](google-bridge.md)) |
| `JOOBLE_API_KEY`, `CAREERJET_API_KEY` | optional | Aggregator APIs. A source runs only when its key is set. |
| `SSH_USER`, `SSH_KEY` | optional | For `deploy.sh` only (default `root` and `<JOBRADAR_HOME>/keys/jobradar_ed25519`). Not copied to the server. |

## `config.yaml`

### `candidate`
| Key | Default | Meaning |
|---|---|---|
| `name` | "the candidate" | Used in the e-mail classifier, follow-up signature and AI calibration lines |
| `pitch` | "" | 1–2 lines of your strongest facts for follow-up e-mail drafts |
| `claude_project` | "Claude" | Name of your Claude Project, shown in the bot's "send this file to …" hint |
| `target_weekly_hours` | 20 | Hours you want. Salary is pro-rated to these hours; more hours (up to `max_weekly_hours`) adds a "propose N h" note. |
| `max_weekly_hours` | 25 | More than this = `fulltime_only` stop factor (needs proof in the ad, see below) |
| `summer_fulltime` | off | `enabled`, `year`, `window_from`, `window_to`, `note`. Fixed-term summer roles (Sommerpraktikum, Ferialjob, summer internship …) pass the `fulltime_only` stop factor even when full-time; permanent / 6+ month full-time roles stay blocked. For students whose permit allows full-time only in lecture-free periods. |
| `min_monthly_salary` | 1200 | EUR gross for `target_weekly_hours`. Below it the card shows 💶. It never hides a job. |

### `location`
| Key | Default | Meaning |
|---|---|---|
| `city` | Vienna | Used in prompts, the `not_vienna` check and web search |
| `country_code` | AT | For web search |
| `home_patterns` | built-in Vienna list | Regexes that mean "in my area" in a location string: city, districts, suburbs, postcodes |
| `country_patterns` | austria / österreich / at | "Remote, <country>" counts as in the area |

### `general`
`timezone` (scheduler, server clock), `log_level`, and the age windows:
`max_age_days` (older ads are never sent), `bootstrap_hours` / `new_source_days` (a newly added source sends its
recent backlog silently), `backfill_days` / `backfill_batch` (`/backfill`), `keep_text_days` (database pruning).

### `ai`
| Key | Default | Meaning |
|---|---|---|
| `triage` | true | Cheap title-only pre-filter. It rejects only on evidence in the title (senior title, unrelated field, apprenticeship). |
| `triage_model`, `score_model`, `email_model`, `text_model` | `claude-haiku-5-5` | Any Claude model; Haiku is the cost-effective default |
| `score_effort`, `score_thinking`, `score_max_tokens` | low / true / 3000 | Scoring call parameters |
| `max_description_chars` | 9000 | Ad text sent to the model |
| `concurrency` | 4 | Parallel evaluations |
| `feedback_examples` | 20 | Your latest reactions shown to the model |
| `monthly_budget_usd` | 10 | Keep equal to your console limit. The bot warns at 70% (or a forecast above it) and at 90%. |
| `why_example` | generic | Style example for the "why it fits" bullets |
| `extra_scoring_rules` | [] | Extra lines added to the scoring prompt as-is (e.g. "Summer internships 2027 are a priority") |

### `filters`
| Key | Meaning |
|---|---|
| `min_score` | Notification threshold (0–100). Change it live with `/minscore`. |
| `blocking` | Stop factors that hide a job: `fulltime_only`, `german_required`, `german_ad`, `senior`, `wrong_field`, `not_vienna` (= outside `location.city`), `eu_only`, `not_eligible`, `expired`. Remove one to turn that rule off. |
| `title_exclude` | Regexes on the title, dropped before any AI call |
| `company_exclude` | Substrings of company names to ignore |
| `english_workplace` | Regexes on company names whose working language is English. Their German-language ads without German requirements arrive with a note instead of being hidden. |

**How stop factors work.** The model must quote the ad word for word for every stop factor. Code then checks that
the quote is really in the ad and that it says what is claimed: "Vollzeit / 40 h" with no part-time option of
≤ `max_weekly_hours`, German above B1, 3+ years, a past deadline, and so on. A claim without proof is asked again
once. If it is still unproven, the job is **delivered** with a ⚠️ note. It is never hidden on a guess. Details:
[architecture.md](architecture.md#ai-scoring-with-evidence).

### `links`, `snippet`
- `links.resolve`: if a job came from LinkedIn or a job board, find the same ad on the company's own site.
  `search_min_score` and `search_daily_cap` limit the paid web-search fallback (≈ $0.02 each).
- `snippet`: Jooble and Careerjet return only a snippet, so the bot looks for the original ad.
  `search_daily_cap` caps the paid search.

### `referrals`
`linkedin_school_ids`: LinkedIn IDs of your universities. They power the "alumni at this company" button. Find an
ID in the URL of a LinkedIn people search filtered by school (`schoolFilter=…`).

### `tracker`, `nudge`, `mail`, `health`
- `tracker.followup_days` (7): when to remind you to follow up. `no_response_days` (35): when a status becomes *no answer*.
- `nudge`: an evening reminder about strong jobs (≥ `min_score`) you did not react to.
- `mail`: how often to read the inbox, and how many days back on the first run.
- `health.alert_after_fails`: how many failed runs in a row before the "source is down" alert, per source.

### `sources`
Every source has `enabled` and `interval_sec`. The rest depends on the source:

| Source | Region | Notes |
|---|---|---|
| `linkedin` | any | Public guest search, no login. `queries` (keywords, location, distance in miles), a 3 h window, a night-time keyword sweep, automatic back-off on HTTP 429. |
| `indeed` | any (via JobSpy) | `country`, `queries`, `hours_old` |
| `karriere`, `jobsat`, `willhaben`, `derstandard` | Austria | National job boards; `location_slug` / `location` |
| `zbp` | Vienna (WU) | University career centre, rendered with headless Chromium |
| `jooble`, `careerjet` | many countries | APIs. They run only when the key is in `secrets.env`. |
| `sites` | any | Generic adapter: list pages → job pages with schema.org `JobPosting` (Adzuna, XING, iAgora, …) |
| `boards` | any | Company career sites from `companies` (below), checked in parallel (`workers`) |

### `companies`
The seed list of company career sites. Each entry is one ATS board:

```yaml
- {kind: greenhouse, key: bitpanda, company: Bitpanda}
- {kind: workday, key: accenture/AccentureCareers, company: Accenture, host: accenture.wd103.myworkdayjobs.com,
   tenant: accenture, site: AccentureCareers}
- {kind: smartrecruiters, key: deloitteat, company: "Deloitte Österreich"}
```

Supported `kind`s: `workday`, `rmk` (SAP SuccessFactors career sites), `greenhouse`, `lever`, `ashby`, `smartrecruiters`,
`personio`, `recruitee`, `workable`, `teamtailor`, `bamboohr`, `breezy`, `pinpoint`, `join`, `amazon`, `taleo`,
`lanteria`, `osce`, `unjobs`, `htmllist`. Common options: `no_remote: true` (global companies with hundreds of
remote US jobs) and `min_interval_sec` (slow portals).

You rarely need to edit this by hand:
- The bot **learns** new boards when it sees ATS links on Indeed, karriere.at or LinkedIn.
- In Telegram: `/addcompany <link to any job of that company>`.
- In bulk: `tools/discover_*.py` ([operations.md](operations.md#finding-more-employers)).
