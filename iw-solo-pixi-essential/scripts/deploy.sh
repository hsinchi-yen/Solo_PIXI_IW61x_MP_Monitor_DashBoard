#!/usr/bin/env bash
# Deploy the dashboard: backup → sync → build (system_up.sh) → health check → automatic rollback on failure.
#
#   scripts/deploy.sh                          # to sit@10.20.31.40:/mnt/md127/iw-solo-pixi-essential
#   scripts/deploy.sh user@host:/path          # another server
#   scripts/deploy.sh --local /path/to/dir     # rehearsal on this machine (no SSH); set
#                                              # COMPOSE_PROJECT_NAME so it does not touch the real stack
# Options:  --no-db-backup   skip pg_dump (files are always backed up)
#           --delete         also remove files on the server that are no longer in the repo
#           --dry-run        show what would be synced, change nothing
#
# SSH: key-based login, or export SSHPASS=… (needs `sshpass`).
# The target keeps its own .env and .system_up.env (never overwritten). Backups land in
# <target>/oldbackup/<YYMMDD>/ (one folder per day): files_<HHMMSS>.tgz and pixi_test_<stamp>.dump.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="sit@10.20.31.40:/mnt/md127/iw-solo-pixi-essential"
LOCAL=0
DB_BACKUP=1
DRY_RUN=0
DELETE=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) LOCAL=1; TARGET="$2"; shift 2 ;;
        --no-db-backup) DB_BACKUP=0; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --delete) DELETE=(--delete); shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) TARGET="$1"; shift ;;
    esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
BK="oldbackup/$(date +%y%m%d)"
FILES_TGZ="$BK/files_${STAMP#*_}.tgz"
DB_DUMP="$BK/pixi_test_$STAMP.dump"
PROJECT="${COMPOSE_PROJECT_NAME:-iw-solo-pixi-essential}"
log() { printf '\033[1;34m[deploy %s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[deploy] %s\033[0m\n' "$*" >&2; exit 1; }

if [[ $LOCAL -eq 1 ]]; then
    DIR="$TARGET"
    run() { bash -c "$1"; }
    RSYNC_DEST="$DIR/"
else
    HOST="${TARGET%%:*}"
    DIR="${TARGET#*:}"
    SSH=(ssh -o StrictHostKeyChecking=accept-new)
    RSYNC_RSH="ssh -o StrictHostKeyChecking=accept-new"
    if [[ -n "${SSHPASS:-}" ]]; then
        command -v sshpass >/dev/null || die "SSHPASS is set but sshpass is not installed"
        SSH=(sshpass -e "${SSH[@]}")
        RSYNC_RSH="sshpass -e $RSYNC_RSH"
    fi
    run() { "${SSH[@]}" "$HOST" "$1"; }
    RSYNC_DEST="$HOST:$DIR/"
fi
COMPOSE="docker compose --project-name '$PROJECT'"

# Files that belong to the server, never to the repo.
EXCLUDES=(--exclude '.env' --exclude '.system_up.env' --exclude 'oldbackup/' --exclude 'dbbackup/'
          --exclude '__pycache__/' --exclude '*.pyc' --exclude '.pytest_cache/' --exclude 'docker-compose.dev.yml'
          --exclude 'node_modules/')

log "source  $SRC"
log "target  $TARGET (compose project $PROJECT)"
[[ -f "$SRC/api/app.py" && -f "$SRC/web/index.html" && -f "$SRC/system_up.sh" ]] || die "run from an iw-solo-pixi-essential checkout"

if [[ $DRY_RUN -eq 1 ]]; then
    rsync -an "${DELETE[@]}" --itemize-changes "${EXCLUDES[@]}" ${RSYNC_RSH:+-e "$RSYNC_RSH"} "$SRC/" "$RSYNC_DEST"
    exit 0
fi

# ── 1. Backup ────────────────────────────────────────────────────────────────
run "mkdir -p '$DIR/$BK'"
log "backing up current files → $FILES_TGZ"
run "cd '$DIR' && tar czf '$FILES_TGZ' --exclude=./oldbackup --exclude=./dbbackup --exclude='__pycache__' . 2>/dev/null || true"
run "test -s '$DIR/$FILES_TGZ'" || die "file backup is empty — aborting before any change"
DB_CONTAINER="$(run "cd '$DIR' && $COMPOSE ps -q postgres 2>/dev/null | head -1" || true)"
DB_NOTE="no database backup was taken"
if [[ $DB_BACKUP -eq 1 && -n "$DB_CONTAINER" ]]; then
    log "backing up the database → $DB_DUMP"
    run "docker exec $DB_CONTAINER pg_dump -U pixi -Fc pixi_test > '$DIR/$DB_DUMP'"
    run "test -s '$DIR/$DB_DUMP'" || die "database backup is empty — aborting before any change"
    DB_NOTE="database backup: $DB_DUMP"
else
    log "database backup skipped"
fi
port_of() { run "sed -n 's/^$1=//p' '$DIR/.system_up.env' 2>/dev/null | tail -1" || true; }
API_PORT="$(port_of IW_API_PORT)"; API_PORT="${API_PORT:-8003}"
WEB_PORT="$(port_of IW_WEB_PORT)"; WEB_PORT="${WEB_PORT:-8004}"
version_of() { sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }
PREV_VERSION="$(run "curl -fsS --max-time 5 http://127.0.0.1:$API_PORT/health" 2>/dev/null | version_of || true)"
log "running version: ${PREV_VERSION:-pre-2.0}"

# ── 2. Sync ──────────────────────────────────────────────────────────────────
log "syncing files"
rsync -a "${DELETE[@]}" "${EXCLUDES[@]}" ${RSYNC_RSH:+-e "$RSYNC_RSH"} "$SRC/" "$RSYNC_DEST"

# ── 3. Build + start (system_up.sh keeps the ports in .system_up.env) ────────
log "system_up.sh (build, start, health)"
up() { run "set -o pipefail; cd '$DIR' && COMPOSE_PROJECT_NAME='$PROJECT' bash system_up.sh 2>&1 | tail -6"; }
TRIES=60
up || { log "system_up.sh reported a failure"; TRIES=5; }

# ── 4. Health check: API version, web app, statistics ────────────────────────
WANT="$(run "sed -n 's/^APP_VERSION=//p' '$DIR/.env' 2>/dev/null | tail -1" || true)"   # the server's .env wins
WANT="${WANT:-$(sed -n 's/^APP_VERSION = os.environ.get("APP_VERSION", "\(.*\)")/\1/p' "$SRC/api/config.py")}"
healthy=0
for _ in $(seq 1 "$TRIES"); do
    body="$(run "curl -fsS --max-time 5 http://127.0.0.1:$API_PORT/health" 2>/dev/null || true)"
    if [[ "$body" == *'"status":"ok"'* && "$(printf '%s' "$body" | version_of)" == "${WANT}" ]]; then
        if run "curl -fsS --max-time 5 http://127.0.0.1:$WEB_PORT/ | grep -q 'js/main.js'" \
           && run "curl -fsS --max-time 10 http://127.0.0.1:$WEB_PORT/api/summary | grep -q '\"units\"'"; then
            healthy=1
            break
        fi
    fi
    sleep 3
done

if [[ $healthy -eq 1 ]]; then
    log "healthy: $body"
    log "done — backup kept in $DIR/$BK"
    exit 0
fi

# ── 5. Rollback (files only; the database keeps the new tables, which the old version ignores) ──
log "health check FAILED — restoring $FILES_TGZ"
# Older versions bind-mounted the code, so the container left root-owned __pycache__ folders that
# cannot be removed here; ignore those and extract the backup over whatever is left.
run "cd '$DIR' && { find . -mindepth 1 -maxdepth 1 ! -name oldbackup ! -name dbbackup ! -name .env ! -name .system_up.env -exec rm -rf {} + 2>/dev/null || true; } && tar xzf '$FILES_TGZ'"
up || true
for _ in $(seq 1 40); do
    if run "curl -fsS --max-time 5 http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
        die "deploy failed; previous version restored and healthy (database untouched; $DB_NOTE)"
    fi
    sleep 3
done
die "deploy failed AND the restored version is not healthy — check '$COMPOSE logs api' on the server"
