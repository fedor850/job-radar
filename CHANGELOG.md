# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.1.2] — 2026-11-02

Source resilience: a one-off network error, a random 403 or an overloaded API no longer loses jobs.

### Fixed
- **A timeout on one ad page made generic sites (XING, Adzuna, iAgora, Work in Austria, WU Jobs …) forget the ad
  for good** (it was stored as "dropped"). Only 404/410 and non-Vienna ads are dropped now; transient errors are
  retried on the next run, and after 5 in a row the run stops hammering the site.
- **One failed page lost the whole run** (karriere.at, jobs.at, DER STANDARD, willhaben, Jooble, Careerjet, Indeed,
  generic sites): results fetched before the error are kept (`Source.begin/note_error/finish`); a run only fails
  when it produced nothing.
- **Jobs whose page could not be opened were written off after 3 tries in ~15 minutes.** Now up to 8 tries with
  growing gaps (5, 5, 10, 20, 40, 80, 160, 180 min), so an ad survives a site outage of several hours.
- **WU ZBP Career Center** (Playwright): `eval_on_selector` crashed while the list was being redrawn, `networkidle`
  never fires on a Blazor page, and one crash lost the run. Now `domcontentloaded`, a safe link probe, pages are kept
  as they are read, and the browser is started a second time after a crash.
- **JOIN**: the company id moved from `pageProps.initialState.company` to `pageProps.pageData.company`; two company
  boards had been failing for days. The id is cached, pages are 0.4–1 s apart.

### Added
- `http.request`: one retry after a timeout / dropped connection / half-read body (`retry_net`), after 5xx incl.
  Cloudflare 52x (`retry_5xx`), after a random 403 / 202 with a brand-new session and User-Agent (`retry_block`, used by
  willhaben, karriere.at, jobs.at, DER STANDARD, SuccessFactors, Jooble and the generic sites), and a second request
  when a JSON endpoint returns an empty or HTML body (Workday did this for two hours on 31.10.2026).
- Telegram alert when a company career site has failed for ≈ 3 h in a row (`health.board_alert_fails`, default 30) and
  a message when it works again. Before, one dead site was only visible in the log.

## [1.1.1] — 2026-11-02

### Fixed
- **LinkedIn lost most of its results.** A guest search page holds 10 cards but `start` advanced by 25, so cards
  10–24 of every block were never seen. Pages now advance by 10 (`page_size`).
- **LinkedIn was paused almost all day.** The limit is per request and random (measured from a data-centre IP: ≈ 20–30 %
  of requests got a 429, the next one usually passed), but one 429 paused the whole source for 15 → 60 min (14 pauses
  in a row on 01–02.11.2026). Now every request goes through one global queue and a 429 is retried after a pause
  (25–50 s, then 50–100 s); only a request that fails on every retry pauses LinkedIn (10 → 20 → 30 min).
- Job pages used to be requested in parallel (one thread per new job); they are now opened one at a time,
  3–5.5 s apart, like the searches.
- A give-up was counted as two strikes (in the request and again in `fetch`).

### Changed
- Other sources: a 429 is repeated once after a short pause (`Retry-After` when it is ≤ 40 s, else 8–15 s) instead of
  failing the whole run (`http.request(..., retry_429=1)`; LinkedIn uses its own queue and passes 0).
- Example config: LinkedIn window 2 h (was 3 h), `max_pages` 6, `delay_sec` [3.0, 5.5], new `page_size`, `retries`,
  `retry_wait_sec`, shorter `cooldown_sec` (600) / `cooldown_max_sec` (1800); `detail_delay_sec` is gone.

### Upgrading from 1.1.0
- Update `sources.linkedin` in your `config.yaml` as in `examples/config.example.yaml` (the old keys still work, but
  `cooldown_sec: 900` / `window_sec: 10800` keep the old behaviour).

## [1.1.0] — 2026-11-01

### Changed
- The whole interface is in English: Telegram cards, buttons, commands and messages, the AI's summaries
  ("why it fits", risks, how to apply), e-mail summaries, watchdog alerts and the Google Sheet
  (*Dashboard*, *Applications*, *Feed*, English statuses).
- E-mail classification fields are now `summary` / `action` (older stored e-mails still display).
- Unknown status labels typed into the sheet no longer overwrite the bot's status.

### Upgrading from 1.0.0
- Update the Apps Script (see [docs/google-bridge.md](docs/google-bridge.md#updating-the-script)). An existing sheet with
  other sheet names or status labels can be migrated once by filling in `LEGACY` at the top of `Code.gs`.
- If you set `ai.why_example` or `referrals.alumni_label` in your `config.yaml`, write them in English.

## [1.0.0] — 2026-11-01

First public release, after daily production use since October 2026.

### Added
- Real-time sources: LinkedIn (guest), Indeed, karriere.at, jobs.at, willhaben, DER STANDARD, WU ZBP, Jooble,
  Careerjet, generic schema.org sites, and 20 ATS adapters with automatic discovery of new company boards.
- Claude scoring with verbatim evidence for every stop factor, a one-time re-ask, and delivery with a warning instead
  of silently hiding a job when the evidence is missing.
- Telegram bot with job cards, referral searches, feedback learning, commands and scheduled reminders.
- Google Sheet tracker, Gmail classification, Calendar events and LinkedIn job-alert e-mails through Apps Script.
- Watchdog, source-health and AI-budget alerts, nightly backups, hardened systemd install.
- `cv_builder` (one-page CV and cover letter from JSON) and a Claude Project workflow for tailored applications.
- Candidate and region settings in `config.yaml` (`candidate.*`, `location.*`, `ai.extra_scoring_rules`,
  `ai.why_example`, `referrals.alumni_label`); private files kept outside the repository.

### Fixed
- The full-time check accepted only some ways of writing hours. It now reads "40-hour week" and "37.5-hour", checks
  part-time options only for this job (not "part-time MBA" or "24h support"), and treats salary "auf
  Vollzeitbasis" as proof only for regular jobs. Before, such jobs were delivered with "⚠️ the AI suspected…".
- Seniority wording "7 plus years" and German "fundierte / solide / sichere Deutschkenntnisse" are recognised.

[1.1.0]: https://github.com/fedor850/job-radar/releases/tag/v1.1.0
[1.0.0]: https://github.com/fedor850/job-radar/releases/tag/v1.0.0
