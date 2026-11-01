# Contributing

Thanks for helping. Job Radar is a small, practical project: changes should keep it simple to run on a €5 server
and safe for the person who relies on it every day.

## Good first contributions
- **English interface** (`general.language`): move the Russian strings of `jobradar/bot.py`, `telegram_ui.py`,
  `tracker.py` and `apps_script/Code.gs` into a message catalogue. This is the top roadmap item.
- **City presets**: a `config.yaml` + notes for your city (sources that work, a verified `companies` list).
- **Source adapters**: a national job board or an ATS not yet supported (`jobradar/sources/`).
- **Language rules** for another local language (see [docs/adapting.md](docs/adapting.md#4-languages)).

## Development

```bash
git clone https://github.com/<you>/job-radar.git && cd job-radar
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest && ruff check .
```

To run the bot locally, put `config.yaml`, `profile.md` and `secrets.env` (with a separate test bot token) in the
repository root (they are git-ignored) and run `python -m jobradar`. Data goes to `./data/`.

## Rules for changes
- **Never hide a job on a guess.** Any new rule that can hide a job must be backed by evidence from the ad and checked
  by code, with tests for both what it must catch and what it must let through. See [architecture.md](docs/architecture.md#ai-scoring-with-evidence).
- **Measure on history** for changes to filters or prompts: use `tools/dev/replay_*.py` on a copy of a real database
  and include the numbers in the pull request (how many sent jobs change, how many hidden jobs change).
- **Defaults keep old behaviour.** A new setting must default to what the code did before.
- **No personal data or secrets** in code, examples, tests or fixtures. Use "Alex Example" and `example.com`.
- Be polite to websites: low request rates, back off on 429/403, no logins with personal accounts.
- Keep pull requests focused. Update `CHANGELOG.md` and the docs in the same pull request.

## Commit messages
Imperative, short subject (`Fix full-time check for "40-hour week"`), with a body that explains *why* when it is not obvious.
