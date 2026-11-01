#!/usr/bin/env bash
# Deploy / update Job Radar on your server and manage it.
#
#   bash deploy/deploy.sh                 full install or update (safe to repeat)
#   bash deploy/deploy.sh logs [N]        last N log lines (default 80)
#   bash deploy/deploy.sh status|restart|stop|install-log
#
# Your private files are kept OUTSIDE the repository, in its parent folder (or set JOBRADAR_HOME):
#
#   <JOBRADAR_HOME>/
#   ├── app/                    ← this repository (git clone)
#   ├── config.yaml             ← your settings        (template: examples/config.example.yaml)
#   ├── profile.md              ← your profile for the AI (template: examples/profile.example.md)
#   ├── secrets.env             ← API keys, SERVER_IP  (template: examples/secrets.example.env)
#   └── keys/jobradar_ed25519   ← SSH key for the server (or SSH_KEY=... in secrets.env)
#
# Files with the same names in the repository root are used as a fallback (they are git-ignored).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${JOBRADAR_HOME:-$(cd "$HERE/.." && pwd)}"

private() {  # private <file> → path of the first existing copy
  for d in "$HOME_DIR" "$HERE"; do [ -f "$d/$1" ] && { echo "$d/$1"; return 0; }; done
  echo "ERROR: $1 not found in $HOME_DIR or $HERE (copy it from examples/)" >&2; return 1
}
SECRETS="$(private secrets.env)"
envval() { grep -E "^$1=" "$SECRETS" | tail -1 | cut -d= -f2- | tr -d '\r"' | sed "s/^'//; s/'\$//; s/^ *//; s/ *\$//"; }
IP="$(envval SERVER_IP)"
[ -n "$IP" ] || { echo "ERROR: SERVER_IP is empty in $SECRETS"; exit 1; }
SSH_USER="$(envval SSH_USER)"; SSH_USER="${SSH_USER:-root}"
KEY_SRC="$(envval SSH_KEY)"; KEY_SRC="${KEY_SRC:-$HOME_DIR/keys/jobradar_ed25519}"
[ -f "$KEY_SRC" ] || { echo "ERROR: SSH key $KEY_SRC not found"; exit 1; }
# a copy with strict permissions (Windows / mounted folders often cannot chmod 600)
mkdir -p ~/.ssh && cp "$KEY_SRC" ~/.ssh/jobradar && chmod 600 ~/.ssh/jobradar
SSH_OPTS="-i $HOME/.ssh/jobradar -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
SSH="ssh $SSH_OPTS $SSH_USER@$IP"

case "${1:-deploy}" in
  logs)        $SSH "journalctl -u jobradar -n ${2:-80} --no-pager"; exit 0 ;;
  restart)     $SSH "systemctl restart jobradar && sleep 3 && systemctl --no-pager status jobradar | head -5"; exit 0 ;;
  stop)        $SSH "systemctl stop jobradar"; exit 0 ;;
  status)      $SSH "systemctl --no-pager status jobradar | head -12"; exit 0 ;;
  install-log) $SSH "tail -n 40 /var/log/jobradar-install.log"; exit 0 ;;
  deploy)      ;;
  *)           echo "unknown command: $1"; exit 2 ;;
esac

CONFIG="$(private config.yaml)"
PROFILE="$(private profile.md)"
TZ_NAME="$(grep -E '^\s*timezone:' "$CONFIG" | head -1 | sed -E 's/^\s*timezone:\s*//; s/\s*#.*$//; s/["'"'"']//g')"

echo ">> copying the app to $IP"
$SSH "mkdir -p /opt/jobradar/app /etc/jobradar"
rsync -rlptz --delete --no-owner --no-group --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r -e "ssh $SSH_OPTS" \
  --exclude '.git/' --exclude '.github/' --exclude 'data/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude 'tests/' --exclude 'docs/' --exclude 'examples/' --exclude 'cv_builder/' --exclude 'claude-project/' \
  --exclude '/config.yaml' --exclude '/profile.md' --exclude '/secrets.env' --exclude '/keys/' \
  "$HERE/" "$SSH_USER@$IP:/opt/jobradar/app/"
echo ">> config.yaml + profile.md"
rsync -tz --chmod=Fu=rw,Fgo=r -e "ssh $SSH_OPTS" "$CONFIG" "$SSH_USER@$IP:/opt/jobradar/app/config.yaml"
rsync -tz --chmod=Fu=rw,Fgo=r -e "ssh $SSH_OPTS" "$PROFILE" "$SSH_USER@$IP:/opt/jobradar/app/profile.md"
echo ">> secrets"
grep -E '^[A-Z_]+=' "$SECRETS" | grep -vE '^(SSH_KEY|SSH_USER)=' | tr -d '\r' \
  | $SSH "mkdir -p /etc/jobradar && cat > /etc/jobradar/secrets.env && chmod 640 /etc/jobradar/secrets.env"
echo ">> install (runs on the server in the background; log: /var/log/jobradar-install.log)"
$SSH "JOBRADAR_TZ='${TZ_NAME:-UTC}' nohup bash /opt/jobradar/app/deploy/install.sh > /var/log/jobradar-install.log 2>&1 &"
for _ in $(seq 1 36); do
  sleep 5
  if $SSH "grep -q '>> done' /var/log/jobradar-install.log"; then break; fi
done
$SSH "tail -n 15 /var/log/jobradar-install.log"
