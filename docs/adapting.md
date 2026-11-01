# Adapting Job Radar to your country and preferences

Job Radar ships as the **Vienna / Austria preset** of its first user: a business student who wanted ≤ 20 h/week,
English-friendly jobs. This page lists everything that is specific to that setup and how to change it. Most of it is
`config.yaml` and `profile.md`. A few rules are in code, and they are marked as such below.

> **Fastest route:** open the repository in an AI coding assistant (e.g. Claude Code) and paste the prompt at the
> [end of this page](#a-prompt-for-your-ai-assistant).

## 1. Who you are and what you want: `profile.md`

Write your own profile (see [getting-started.md §4](getting-started.md#4-write-profilemd-the-most-important-step)).
The AI reads it for every job, so this one file adapts the bot to a different field (engineering, design, healthcare
admin…), a different level (graduate, mid-level) or a different goal.

If your field is not business, also update the two field lists in code that the profile cannot override:
- **Triage** (`jobradar/ai.py → TRIAGE_SYSTEM`): titles that are "obviously unrelated" (for example, it rejects
  pure software engineering, which is wrong for a developer).
- **`filters.title_exclude`** in `config.yaml`: regexes that drop titles before any AI call.

## 2. Hours, pay and full-time

```yaml
candidate:
  target_weekly_hours: 20     # what you want
  max_weekly_hours: 25        # above this → hidden (needs proof in the ad)
  min_monthly_salary: 1200    # EUR gross at target hours → 💶 note only
```

- **Looking for full-time work?** Remove `fulltime_only` from `filters.blocking`.
- Salary is computed in EUR. For another currency, the prompt's salary line (`SCORE_SYSTEM_TEMPLATE` in
  `jobradar/ai.py`) is the place to change it.

## 3. Your search area

```yaml
location:
  city: Berlin
  country_code: DE
  home_patterns: ['berlin', 'potsdam', '1[0-4]\d{3} berlin']        # city, suburbs, postcodes (regex)
  country_patterns: ['germany', 'deutschland', 'de']                 # "Remote, Germany" counts
general:
  timezone: Europe/Berlin
```

`home_patterns` decides which locations count as "here" for every source and for the `not_vienna` stop factor (the
name is historical; it means "outside `location.city`"). Remote jobs count when they mention your country.

## 4. Languages

The preset contains the rules of someone who speaks German at B1 in Austria:

| Rule | Where | What to do |
|---|---|---|
| `german_required`: job needs German above B1 | `filters.blocking`, regexes in `ai.py` | Remove from `blocking` if you speak the local language well |
| `german_ad`: ad is written in German and does not say German is optional | `filters.blocking`, `ai.py → german_ad_check` | Remove from `blocking` outside German-speaking countries, or if you are fluent |
| `english_workplace`: companies whose German ads are still OK | `config.yaml` | Your local list of English-speaking employers |

For another local language with the same logic (e.g. Dutch in the Netherlands), you need to adapt the German
regexes in `jobradar/ai.py` (`_GERMAN_*`, `_EITHER_OR`, `german_ad_check`) and the stop-word list in
`jobradar/util.py → detect_lang`. That is a good task for an AI assistant.

**Interface language.** The Telegram interface, the tracker sheet and the AI summaries are in Russian. English is the
top item of the roadmap. Until then, change `Write all free-text fields … in RUSSIAN` in `SCORE_SYSTEM_TEMPLATE` to
get English summaries, and translate the strings in `jobradar/bot.py`, `telegram_ui.py` and `apps_script/Code.gs`.

## 5. Sources

| Works anywhere | Austria only (disable elsewhere) |
|---|---|
| `linkedin`: set `queries[].location` (e.g. `"Berlin, Berlin, Germany"`) and `distance` | `karriere`, `jobsat`, `willhaben`, `derstandard` |
| `indeed`: set `country` (`Germany`, `UK`, `USA`…) and `queries[].location` | `zbp` (WU Vienna career centre) |
| `jooble`, `careerjet`: change `location` / `locales` | `sites`: `iagora`, `workinaustria`, `wujobs`, and `adzuna`/`xing` URLs point to Vienna |
| `boards` + `companies`: any company on a supported ATS | |

To disable a source: `enabled: false`. To add a national job board: if its job pages carry schema.org
`JobPosting` data (most do), add it under `sources.sites` without code. See the existing entries and
[architecture.md → Sources](architecture.md#sources). Otherwise copy one of the small adapters in
`jobradar/sources/` (e.g. `karriere.py`, about 70 lines).

**Company list.** Replace `companies` with employers in your city. The quickest way: run the bot with only
LinkedIn + Indeed for a week (it learns boards automatically), then run `tools/discover_from_db.py` to find
the career sites of everyone who posted in your area ([operations.md](operations.md#finding-more-employers)).

## 6. The rest of the Austrian preset

| Item | Where |
|---|---|
| Summer-internship priority rule | `ai.extra_scoring_rules` in `config.yaml` |
| `eu_only` / work-permit stop factor | `filters.blocking` (remove it if you are an EU citizen and want such jobs anyway) |
| Austrian salary transparency ("auf Vollzeitbasis") handling | `jobradar/ai.py → _FT_SALARY` (harmless elsewhere) |
| Country notes for the Claude Project | `claude-project/country-notes/austria.md`: write one for your country |
| Alumni buttons | `referrals.linkedin_school_ids` |

## A prompt for your AI assistant

```text
I want to run Job Radar (this repository) for my own job search.
Me: <one paragraph: field, level, city, hours, languages with levels, work permit>.
My private folder is ../ (config.yaml, profile.md, secrets.env).
1. Write ../profile.md in the structure of examples/profile.example.md from my CV (attached) — keep every number.
2. Adapt ../config.yaml following docs/adapting.md: candidate.*, location.*, filters.blocking, sources
   (disable Austria-only ones, point LinkedIn/Indeed at my city), and a starting companies list of employers
   in my city that use a supported ATS (verify each board returns jobs).
3. If my local language rules differ from the German preset, adapt the language checks in jobradar/ai.py
   and add tests in tests/.
4. Run the tests (pytest), then deploy with deploy/deploy.sh and check the logs.
Ask me before anything you are not sure about.
```
