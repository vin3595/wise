#!/usr/bin/env bash
# Sets up a Wise Box on a fresh Ubuntu Server 24.04 computer. Needs internet while it runs (10–20 min).
#
#   unzip wise-0.6.0.zip && sudo bash wise/box/install.sh
#
# Safe to run again: it keeps the data, passwords and backup key it already made.
set -euo pipefail

ROOT=/opt/wise
SRC=$(cd "$(dirname "$0")/.." && pwd)
VER=$(cat "$SRC/VERSION")
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run it with sudo: sudo bash $0"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || die "this installer is for Ubuntu Server (24.04 recommended)"

step "1/8 Installing what the box needs"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq docker.io docker-compose-v2 age rclone rsync curl unzip smartmontools chrony \
  unattended-upgrades avahi-daemon ufw python3 >/dev/null
systemctl enable --now docker chrony >/dev/null

step "2/8 Naming the box and setting the clock"
hostnamectl set-hostname wise-box
timedatectl set-timezone Asia/Kolkata
timedatectl set-ntp true || true

step "3/8 Security updates and firewall"
cat > /etc/apt/apt.conf.d/52wise-box <<'EOF'
Unattended-Upgrade::Allowed-Origins { "${distro_id}:${distro_codename}-security"; };
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:30";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
# Only devices on the shop network can reach the box; the public address comes in through the tunnel.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do ufw allow from "$net" to any port 22 proto tcp >/dev/null; done
ufw --force enable >/dev/null

# A server never sleeps. On a laptop: keep running with the lid closed, and stop charging at 80% so a
# battery that's plugged in day and night doesn't swell.
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
if ls /sys/class/power_supply/BAT* >/dev/null 2>&1; then
  echo "  Laptop found: it will keep running with the lid closed."
  mkdir -p /etc/systemd/logind.conf.d
  printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n' \
    > /etc/systemd/logind.conf.d/wise-box.conf
  systemctl restart systemd-logind || true
  if ls /sys/class/power_supply/BAT*/charge_control_end_threshold >/dev/null 2>&1; then
    echo "  Battery charging will stop at 80%."
  else
    echo "  This laptop doesn't let Linux limit charging. Check the battery every few months for swelling."
  fi
fi

step "4/8 Copying Wise $VER into $ROOT"
mkdir -p "$ROOT"/{releases,data/postgres,data/uploads,backups,config,state}
chmod 700 "$ROOT/config" "$ROOT/backups"
if [ "$(readlink -f "$ROOT/current" 2>/dev/null)" != "$ROOT/releases/$VER" ]; then
  rm -rf "$ROOT/releases/$VER"; cp -a "$SRC" "$ROOT/releases/$VER"
  ln -sfn "$ROOT/releases/$VER" "$ROOT/current"
fi
chmod +x "$ROOT/current/box/wise" "$ROOT/current/box/initdb/"*.sh
ln -sf "$ROOT/current/box/wise" /usr/local/bin/wise

step "5/8 Making passwords (kept in $ROOT/.env, readable by root only)"
if [ ! -f "$ROOT/.env" ]; then
  r() { openssl rand -hex 24; }
  umask 077
  cat > "$ROOT/.env" <<EOF
POSTGRES_PASSWORD=$(r)
WISE_OWNER_PASSWORD=$(r)
WISE_APP_PASSWORD=$(r)
WISE_JWT_SECRET=$(openssl rand -hex 32)
WISE_OTP_PROVIDER=box
WISE_CUSTOMER_URL=
CLOUDFLARE_TUNNEL_TOKEN=
WISE_PRINT_KEY=
EOF
  umask 022
fi

step "6/8 USB backup drive"
mkdir -p /media/wise-backup
grep -q 'LABEL=WISEBACKUP' /etc/fstab || \
  echo 'LABEL=WISEBACKUP /media/wise-backup ext4 nofail,x-systemd.automount,x-systemd.device-timeout=5s 0 2' >> /etc/fstab
systemctl daemon-reload
if ls /dev/disk/by-label/WISEBACKUP >/dev/null 2>&1; then echo "  Found the USB backup drive."
else echo "  No USB backup drive yet. Plug one in later and run: sudo wise usb-prepare"; fi

step "7/8 Building and starting Wise (the slow part)"
cp "$ROOT"/current/box/systemd/*.service "$ROOT"/current/box/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
wise start
systemctl enable wise.service wise-backup.timer wise-health.timer wise-verify.timer >/dev/null
if ls /sys/class/power_supply/BAT* >/dev/null 2>&1; then systemctl enable --now wise-battery.service >/dev/null || true; fi
systemctl start wise-backup.timer wise-health.timer wise-verify.timer

step "8/8 Your backup key"
if [ ! -f "$ROOT/config/backup.key" ]; then
  age-keygen -o "$ROOT/config/backup.key" 2>/dev/null
  age-keygen -y "$ROOT/config/backup.key" > "$ROOT/config/backup.pub"
  chmod 600 "$ROOT/config/backup.key"
  KEY=$(grep -v '^#' "$ROOT/config/backup.key")
  cat <<EOF

  Every backup (USB drive, Google Drive, cloud) is locked with this key. Without it, nobody can open
  them: not Google, not the cloud company, and not you. Save it now in TWO places, e.g. your
  password manager and a printed copy kept at home:

      $KEY

EOF
  while true; do
    read -rp "  Type the last 6 characters of the key to confirm you saved it: " last
    [ "$last" = "${KEY: -6}" ] && break
    echo "  That doesn't match. Look at the key again."
  done
fi
wise backup >/dev/null 2>&1 || true

IP=$(hostname -I | tr ' ' '\n' | grep -E '^(10|172|192)\.' | head -1)
cat <<EOF

  Wise Box is running.

  1. On any phone or PC on the shop Wi-Fi, open  http://$IP  (or http://wise-box.local)
     and create the restaurant account. The sign-in code: sudo wise login-code
  2. In your router, reserve the address $IP for this box so it never changes.
  3. Printers: Kitchen setup > Printing > Copy print key, then  sudo wise print-key <key>
  4. QR orders and access from home:  sudo wise tunnel <token> https://pos.yourdomain.in
  5. Google Drive and a second cloud copy:  sudo wise offsite add gdrive:WiseBackups
  6. Alerts to your email:  sudo wise alerts email you@example.com

  Check everything any time with:  sudo wise status
EOF
