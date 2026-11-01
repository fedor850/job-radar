# Security policy

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private reporting instead: *Security → Report a vulnerability*
in this repository. You will get an answer within a few days.

## Keeping your installation safe
- Private files (`secrets.env`, `config.yaml`, `profile.md`, `keys/`) live **outside** the repository and are
  git-ignored. Never commit them, and never paste `secrets.env` into an issue.
- Set `TELEGRAM_CHAT_ID`. Without it, the first person who messages your bot becomes its owner.
- Set a **monthly spend limit** in the Anthropic console and keep `ai.monthly_budget_usd` equal to it.
- Use a long random `APPS_SCRIPT_SECRET` (`openssl rand -hex 24`).
- The installer turns off SSH password login, enables the `ufw` firewall (SSH only), fail2ban and automatic security
  updates, and runs the bot as an unprivileged user with systemd hardening.
- If a key leaks: revoke it (BotFather `/revoke`, Anthropic console, new Apps Script secret), update `secrets.env`,
  and run `deploy.sh` again.
