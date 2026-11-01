# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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
