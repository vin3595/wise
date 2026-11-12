#!/bin/bash
# Stands in for "docker compose" in tests: "exec -T db <cmd>" runs <cmd> against the local test cluster.
export PGHOST=/tmp/wbt PGPORT=55432
case $1 in
  exec) shift; [ "$1" = -T ] && shift; shift; exec "$@" ;;
  ps) exit 0 ;;
  *) exit 0 ;;
esac
