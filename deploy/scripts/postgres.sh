#!/usr/bin/env bash
# Postgres as uid 1000 on ephemeral /data. initdb on first boot, always ensure the app DB exists,
# then run in foreground.
set -e
# the connection settings live in /app/.env (consumed by the Python app); load them for this shell too,
# else initdb runs with an empty -U and fails its ACL bootstrap.
set -a; [ -f /app/.env ] && . /app/.env; set +a
export PGDATA=/data/pg
PGBIN="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)"

# A prior boot that was interrupted between pg_ctl start and stop leaves a daemonized postmaster and a
# stale postmaster.pid; either blocks this start. Take the cluster down hard before touching it.
"$PGBIN/pg_ctl" -D "$PGDATA" -m immediate -w stop >/dev/null 2>&1 || true
rm -f "$PGDATA/postmaster.pid" 2>/dev/null || true

# initdb only when the cluster doesn't exist yet.
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  mkdir -p "$PGDATA"
  "$PGBIN/initdb" -D "$PGDATA" -U "$POSTGRES_USER" --auth-local=trust --auth-host=trust >/dev/null
fi

# ALWAYS ensure the password + app DB exist (idempotent). Gating this on PG_VERSION is fragile: if a
# previous first boot wrote PG_VERSION but was interrupted before creating the DB, the app DB would
# never be created on any later boot. Bring the cluster up on the socket only, reconcile, take it down.
"$PGBIN/pg_ctl" -D "$PGDATA" -o "-c listen_addresses='' -c unix_socket_directories=/tmp" -w start
"$PGBIN/psql" -h /tmp -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d postgres \
  -c "ALTER USER \"$POSTGRES_USER\" PASSWORD '$POSTGRES_PASSWORD';"
# CREATE DATABASE can't run conditionally in one statement and \gexec is a psql meta-command that only
# works from stdin/-f (not -c), so guard the create in the shell instead.
if ! "$PGBIN/psql" -h /tmp -tAc "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" \
      --username "$POSTGRES_USER" -d postgres | grep -q 1; then
  "$PGBIN/psql" -h /tmp -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d postgres \
    -c "CREATE DATABASE \"$POSTGRES_DB\";"
fi
"$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop

# /var/run/postgresql is root-owned; uid 1000 puts the socket/lock in /tmp. The app connects over TCP anyway.
exec "$PGBIN/postgres" -D "$PGDATA" \
  -c listen_addresses=127.0.0.1 -c port=5432 -c unix_socket_directories=/tmp \
  -c shared_buffers=256MB -c max_connections=80
