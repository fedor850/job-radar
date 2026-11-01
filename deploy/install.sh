#!/usr/bin/env bash
# Runs ON THE SERVER as root. Idempotent: safe to run again after every update.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo ">> system packages"
timedatectl set-timezone "${JOBRADAR_TZ:-UTC}" || true
apt-get update -y -q
apt-get install -y -q python3 python3-venv python3-pip sqlite3 ufw unattended-upgrades fail2ban rsync >/dev/null

echo ">> user & folders"
id jobradar >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/jobradar --shell /usr/sbin/nologin jobradar
mkdir -p /opt/jobradar/app /etc/jobradar /var/lib/jobradar/backups
chown -R root:root /opt/jobradar/app && chmod -R u=rwX,go=rX /opt/jobradar /opt/jobradar/app
chown -R jobradar:jobradar /var/lib/jobradar
if [ -f /etc/jobradar/secrets.env ]; then chown root:jobradar /etc/jobradar/secrets.env; chmod 640 /etc/jobradar/secrets.env; fi

echo ">> python venv"
[ -x /opt/jobradar/venv/bin/python ] || python3 -m venv /opt/jobradar/venv
/opt/jobradar/venv/bin/pip install -q --upgrade pip
/opt/jobradar/venv/bin/pip install -q -r /opt/jobradar/app/requirements.txt
# headless Chromium for sites that render their job list in the browser (WU ZBP)
PLAYWRIGHT_BROWSERS_PATH=/opt/jobradar/ms-playwright /opt/jobradar/venv/bin/playwright install --with-deps chromium >/dev/null 2>&1 || echo "playwright install failed"

echo ">> service"
install -m 644 /opt/jobradar/app/deploy/jobradar.service /etc/systemd/system/jobradar.service
systemctl daemon-reload
systemctl enable jobradar >/dev/null 2>&1
systemctl restart jobradar

echo ">> watchdog (every 10 min: restart + Telegram alert if the bot is down, stuck or bloated)"
install -m 644 /opt/jobradar/app/deploy/jobradar-watchdog.service /etc/systemd/system/jobradar-watchdog.service
install -m 644 /opt/jobradar/app/deploy/jobradar-watchdog.timer /etc/systemd/system/jobradar-watchdog.timer
systemctl daemon-reload
systemctl enable --now jobradar-watchdog.timer >/dev/null 2>&1

echo ">> security: firewall, ssh keys only, auto security updates"
ufw allow OpenSSH >/dev/null; ufw --force enable >/dev/null
if grep -qE '^#?PasswordAuthentication' /etc/ssh/sshd_config; then
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
else
  echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config
fi
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
# security updates that need a reboot: reboot at 04:30 (after the 04:00 backup); the bot starts by itself
cat > /etc/apt/apt.conf.d/52jobradar-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF
systemctl enable --now fail2ban >/dev/null 2>&1 || true

sleep 4
systemctl --no-pager --lines=0 status jobradar | head -5
echo ">> done"
