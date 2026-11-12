#!/usr/bin/env bash
# Tests backup -> verify -> restore -> offsite copies -> alerts against a real local PostgreSQL 16,
# with Docker replaced by tests/dc-stub.sh. Run as root on a dev machine: bash box/tests/run.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); W="$HERE/../wise"; T=/tmp/wbt; PG=/usr/lib/postgresql/16/bin
pkill -u pgtest postgres 2>/dev/null || true; sleep 1; umount $T/usb 2>/dev/null || true; rm -rf $T; mkdir -p $T/bin $T/usb; id pgtest >/dev/null 2>&1 || useradd -m pgtest; chown pgtest $T
su pgtest -c "$PG/initdb -D $T/pg -U postgres --auth=trust >/dev/null && $PG/pg_ctl -D $T/pg -o '-p 55432 -k $T' -l $T/pg.log start >/dev/null"
export PGHOST=$T PGPORT=55432
POSTGRES_USER=postgres WISE_OWNER_PASSWORD=o WISE_APP_PASSWORD=a bash "$HERE/../initdb/01-roles.sh" >/dev/null
psql -qU wise_owner -d wise -c "create table bills (id serial primary key, tenant_id int, total_paise int); alter table bills enable row level security;
  create policy t on bills using (tenant_id = current_setting('app.tenant_id', true)::int);
  insert into bills (tenant_id, total_paise) select 1, g from generate_series(1,500) g;"
cp "$HERE/dc-stub.sh" $T/dc-stub.sh
printf '#!/bin/bash\nargs=(); for a in "$@"; do case $a in --config) s=1; continue;; esac; [ -n "$s" ] && { s=; continue; }; case $a in --*) ;; *:*) args+=("%s/${a%%%%:*}/${a#*:}");; *) args+=("$a");; esac; done\ncase ${args[0]} in mkdir) mkdir -p "${args[1]}";; copy) mkdir -p "${args[2]}"; cp -r "${args[1]}/." "${args[2]}/";; esac\n' $T > $T/bin/rclone
printf '#!/bin/bash\nmkdir -p "$3"; cp -a "$2." "$3"\n' > $T/bin/rsync; chmod +x $T/bin/*
R=$T/root; mkdir -p $R/config $R/data/uploads/t1 $R/releases/0.6.0; echo 0.6.0 > $R/releases/0.6.0/VERSION; ln -sfn $R/releases/0.6.0 $R/current; touch $R/.env
echo logo > $R/data/uploads/t1/logo.png; age-keygen -o $R/config/backup.key 2>/dev/null; age-keygen -y $R/config/backup.key > $R/config/backup.pub
export PATH=$T/bin:$PATH WISE_ROOT=$R WISE_DC=$T/dc-stub.sh WISE_EXTERNAL_DIR=$T/usb WISE_SKIP_HEALTH=1
mount -t tmpfs tmpfs $T/usb
fail() { echo "FAIL: $*"; exit 1; }
$W offsite add gdrive:WiseBackups >/dev/null; $W offsite add b2:bucket >/dev/null
$W backup >/dev/null
[ -n "$(ls $T/usb/wise/daily)" ] || fail "no USB copy"; [ -n "$(ls $T/gdrive/WiseBackups/daily)" ] || fail "no Drive copy"; [ -n "$(ls $T/b2/bucket/daily)" ] || fail "no 2nd cloud copy"
$W verify-backup | grep -q "500 bills" || fail "verify"
psql -qU postgres -d wise -c "delete from bills where id > 10"; rm -rf $R/data/uploads
$W restore "$(ls $T/gdrive/WiseBackups/daily/* | head -1)" --key $R/config/backup.key >/dev/null
[ "$(psql -U postgres -d wise -tAc 'select count(*) from bills')" = 500 ] || fail "restore count"
[ "$(psql -U wise_app -d wise -tAc 'select count(*) from bills')" = 0 ] || fail "row-level security lost after restore"
[ "$(cat $R/data/uploads/t1/logo.png)" = logo ] || fail "uploads not restored"
age-keygen -o $T/other.key 2>/dev/null; if $W restore "$(ls $R/backups/daily/* | head -1)" --key $T/other.key 2>/dev/null; then fail "wrong key accepted"; fi
umount $T/usb; echo PASS
