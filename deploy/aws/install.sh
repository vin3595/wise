#!/usr/bin/env bash
# Puts Wise online on an AWS EC2 server (Ubuntu 24.04). Run it once on the server:
#
#   unzip wise-v1.1.0.zip && sudo bash wise/deploy/aws/install.sh
#
# It asks for your domain and database, then builds and starts Wise with HTTPS.
# Safe to run again: it keeps the passwords and backup key it already made.
set -euo pipefail

ROOT=/opt/wise
SRC_IN=$(cd "$(dirname "$0")/../.." && pwd)
SRC=$ROOT/src
ENV_FILE=$ROOT/.env
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf 'install: %s\n' "$*" >&2; exit 1; }
ask() { local q=$1 def=${2:-} a; read -r -p "$q${def:+ [$def]}: " a; echo "${a:-$def}"; }
secret() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-32}"; }

[ "$(id -u)" -eq 0 ] || die "run it with sudo: sudo bash $0"
. /etc/os-release; [ "${ID:-}" = ubuntu ] || die "this installer is for Ubuntu (24.04 recommended)"

step "1/7 Installing Docker and tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq docker.io docker-compose-v2 age rsync curl unzip ufw chrony unattended-upgrades dnsutils >/dev/null
systemctl enable --now docker chrony >/dev/null
timedatectl set-timezone Asia/Kolkata

step "2/7 Firewall: only SSH, HTTP and HTTPS"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443 >/dev/null
ufw --force enable >/dev/null
# A small server needs swap so a build never runs out of memory.
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "3/7 Copying Wise to $SRC"
mkdir -p "$ROOT"/{data/uploads,data/caddy,backups,config}
if [ "$SRC_IN" != "$SRC" ]; then rsync -a --delete --exclude node_modules --exclude __pycache__ "$SRC_IN"/ "$SRC"/; fi
install -m 755 "$SRC/deploy/aws/wise-cloud" /usr/local/bin/wise-cloud

step "4/7 Settings"
touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
put() { if grep -qE "^$1=" "$ENV_FILE"; then sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi; }

DOMAIN=$(get WISE_DOMAIN); [ -n "$DOMAIN" ] || DOMAIN=$(ask "Your Wise address (for example app.wisepos.in)")
[ -n "$DOMAIN" ] || die "a domain is needed for HTTPS"
EMAIL=$(get WISE_ADMIN_EMAIL); [ -n "$EMAIL" ] || EMAIL=$(ask "Email for certificate notices")
put WISE_DOMAIN "$DOMAIN"; put WISE_ADMIN_EMAIL "$EMAIL"
put WISE_VERSION "$(cat "$SRC/VERSION")"
[ -n "$(get WISE_JWT_SECRET)" ] || put WISE_JWT_SECRET "$(secret 48)"
[ -n "$(get WISE_OWNER_PASSWORD)" ] || put WISE_OWNER_PASSWORD "$(secret)"
[ -n "$(get WISE_APP_PASSWORD)" ] || put WISE_APP_PASSWORD "$(secret)"
[ -n "$(get WISE_OTP_PROVIDER)" ] || put WISE_OTP_PROVIDER box
[ -n "$(get WISE_SIGNUP_MODE)" ] || put WISE_SIGNUP_MODE first

MODE=$(get WISE_DB_MODE)
if [ -z "$MODE" ]; then
  echo "Database: 1) Amazon RDS (recommended)   2) on this server (cheapest, you look after backups)"
  [ "$(ask "Choose 1 or 2" 1)" = 2 ] && MODE=local || MODE=rds
  put WISE_DB_MODE "$MODE"
fi

step "5/7 Database"
if [ "$MODE" = local ]; then
  put WISE_DB_HOST db; put WISE_DB_SSL disable
  [ -n "$(get POSTGRES_PASSWORD)" ] || put POSTGRES_PASSWORD "$(secret)"
  mkdir -p "$ROOT/data/postgres"
else
  HOST=$(get WISE_DB_HOST)
  if [ -z "$HOST" ] || [ "$HOST" = db ]; then
    HOST=$(ask "RDS endpoint (from the RDS console, ends in .rds.amazonaws.com)")
    MUSER=$(ask "RDS master username" postgres)
    read -r -s -p "RDS master password: " MPASS; echo
    put WISE_DB_HOST "$HOST"; put WISE_DB_SSL require
    echo "  Creating the two Wise database users on RDS..."
    docker run --rm -i -e PGPASSWORD="$MPASS" -e PGSSLMODE=require postgres:16 \
      psql -v ON_ERROR_STOP=1 -h "$HOST" -U "$MUSER" -d postgres \
      -v owner_pw="$(get WISE_OWNER_PASSWORD)" -v app_pw="$(get WISE_APP_PASSWORD)" -v master="$MUSER" \
      < "$SRC/deploy/aws/rds-init.sql"
    docker run --rm -i -e PGPASSWORD="$MPASS" -e PGSSLMODE=require postgres:16 \
      psql -v ON_ERROR_STOP=1 -h "$HOST" -U "$MUSER" -d wise < "$SRC/deploy/aws/rds-grants.sql"
    unset MPASS
  fi
fi

step "6/7 Backup key"
if [ ! -f "$ROOT/config/backup.key" ]; then
  age-keygen -o "$ROOT/config/backup.key" 2>/dev/null; chmod 600 "$ROOT/config/backup.key"
  NEWKEY=1
fi
cat > /etc/systemd/system/wise-backup.service <<UNIT
[Unit]
Description=Wise nightly database backup
[Service]
Type=oneshot
ExecStart=/usr/local/bin/wise-cloud backup
UNIT
cat > /etc/systemd/system/wise-backup.timer <<UNIT
[Unit]
Description=Wise nightly database backup
[Timer]
OnCalendar=*-*-* 04:20:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload; systemctl enable --now wise-backup.timer >/dev/null

step "7/7 Building and starting Wise (5-15 minutes the first time)"
IP=$(curl -s --max-time 5 https://checkip.amazonaws.com || true)
DNS=$(dig +short "$DOMAIN" A | tail -1 || true)
if [ -n "$IP" ] && [ "$DNS" != "$IP" ]; then
  echo "  Warning: $DOMAIN points to '${DNS:-nothing}', but this server is $IP."
  echo "  Add an A record for $DOMAIN -> $IP at your domain provider. HTTPS starts working once it does."
fi
/usr/local/bin/wise-cloud start-fresh

printf '\n\033[1mWise is online at https://%s\033[0m\n' "$DOMAIN"
echo "  Sign in as the owner there. Until SMS is set up, get the sign-in code with: sudo wise-cloud login-code"
echo "  Then install it as an app on each device (Setup > Screens shows how)."
if [ "${NEWKEY:-}" = 1 ]; then
  echo; echo "BACKUP KEY - save it in two safe places. Without it no backup can be opened:"
  grep -v '^#' "$ROOT/config/backup.key"; echo
fi
