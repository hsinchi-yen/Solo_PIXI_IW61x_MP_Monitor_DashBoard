#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly RUNTIME_ENV="${APP_DIR}/.system_up.env"
readonly PROJECT_NAME="${COMPOSE_PROJECT_NAME:-iw-solo-pixi-essential}"
readonly HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-180}"

cd "${APP_DIR}"

log() {
    printf '[system_up] %s\n' "$*"
}

fail() {
    printf '[system_up] ERROR: %s\n' "$*" >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || fail "Docker is not installed."
command -v curl >/dev/null 2>&1 || fail "curl is required for health checks."
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable or this user lacks permission."

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    fail "Docker Compose is not installed."
fi

compose() {
    local env_args=()
    [[ -f "${APP_DIR}/.env" ]] && env_args+=(--env-file "${APP_DIR}/.env")
    env_args+=(--env-file "${RUNTIME_ENV}")
    "${COMPOSE[@]}" --project-name "${PROJECT_NAME}" "${env_args[@]}" "$@"
}

saved_value() {
    local key="$1"
    local fallback="$2"
    local value=""

    if [[ -f "${RUNTIME_ENV}" ]]; then
        value="$(sed -n "s/^${key}=//p" "${RUNTIME_ENV}" | tail -n 1)"
    fi
    printf '%s\n' "${value:-${fallback}}"
}

validate_port() {
    local port="$1"
    [[ "${port}" =~ ^[0-9]+$ ]] &&
        ((port >= 1024 && port <= 65535))
}

port_is_reserved() {
    local port="$1"
    local reserved
    for reserved in "${RESERVED_PORTS[@]:-}"; do
        [[ "${port}" == "${reserved}" ]] && return 0
    done
    return 1
}

port_is_available() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ! ss -H -ltn "sport = :${port}" 2>/dev/null | grep -q .
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "${port}" <<'PY'
import socket
import sys

sock = socket.socket()
try:
    sock.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
    else
        fail "Neither ss nor python3 is available for port detection."
    fi
}

port_belongs_to_existing_service() {
    local port="$1"
    local service="$2"
    local container_port="$3"
    local published=""

    [[ -f "${RUNTIME_ENV}" ]] || return 1
    published="$(compose port "${service}" "${container_port}" 2>/dev/null | tail -n 1 || true)"
    [[ -n "${published}" && "${published##*:}" == "${port}" ]]
}

choose_port() {
    local preferred="$1"
    local service="$2"
    local container_port="$3"
    local candidate
    local last_candidate

    validate_port "${preferred}" || fail "Invalid preferred port: ${preferred}"
    last_candidate=$((preferred + 100))
    ((last_candidate > 65535)) && last_candidate=65535

    for ((candidate = preferred; candidate <= last_candidate; candidate++)); do
        port_is_reserved "${candidate}" && continue
        if port_is_available "${candidate}" ||
            port_belongs_to_existing_service "${candidate}" "${service}" "${container_port}"; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    fail "No free port found in range ${preferred}-${last_candidate}."
}

RESERVED_PORTS=()
preferred_api="${IW_API_PORT:-$(saved_value IW_API_PORT 8003)}"
IW_API_PORT="$(choose_port "${preferred_api}" api 8003)"
RESERVED_PORTS+=("${IW_API_PORT}")

preferred_web="${IW_WEB_PORT:-$(saved_value IW_WEB_PORT 8004)}"
IW_WEB_PORT="$(choose_port "${preferred_web}" nginx 80)"
RESERVED_PORTS+=("${IW_WEB_PORT}")

preferred_db="${IW_DB_PORT:-$(saved_value IW_DB_PORT 5434)}"
IW_DB_PORT="$(choose_port "${preferred_db}" postgres 5432)"
RESERVED_PORTS+=("${IW_DB_PORT}")

export IW_API_PORT IW_WEB_PORT IW_DB_PORT
if [[ "${SYSTEM_UP_PORT_CHECK_ONLY:-0}" == "1" ]]; then
    log "Port check only: API=${IW_API_PORT}, Web=${IW_WEB_PORT}, PostgreSQL=${IW_DB_PORT}"
    exit 0
fi

umask 077
runtime_tmp="${RUNTIME_ENV}.tmp.$$"
trap 'rm -f -- "${runtime_tmp:-}"' EXIT
printf 'IW_API_PORT=%s\nIW_WEB_PORT=%s\nIW_DB_PORT=%s\n' \
    "${IW_API_PORT}" "${IW_WEB_PORT}" "${IW_DB_PORT}" >"${runtime_tmp}"
mv -f -- "${runtime_tmp}" "${RUNTIME_ENV}"

on_error() {
    local exit_code=$?
    printf '[system_up] Deployment failed (exit %s).\n' "${exit_code}" >&2
    compose ps >&2 || true
    compose logs --tail 80 >&2 || true
    exit "${exit_code}"
}
trap on_error ERR

log "Project directory: ${APP_DIR}"
log "Ports: API=${IW_API_PORT}, Web=${IW_WEB_PORT}, PostgreSQL=${IW_DB_PORT}"
log "Building and starting Docker Compose services..."
compose up -d --build --remove-orphans
compose exec -T nginx nginx -s reload >/dev/null

wait_for_health() {
    local name="$1"
    local url="$2"
    local deadline=$((SECONDS + HEALTH_TIMEOUT_SEC))

    while ((SECONDS < deadline)); do
        if curl --fail --silent --show-error --max-time 5 "${url}" >/dev/null 2>&1; then
            log "${name} health check passed: ${url}"
            return 0
        fi
        sleep 2
    done
    fail "${name} health check timed out: ${url}"
}

wait_for_health "API" "http://127.0.0.1:${IW_API_PORT}/health"
wait_for_health "Web" "http://127.0.0.1:${IW_WEB_PORT}/health"

compose ps
host_address="${IW_PUBLIC_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
host_address="${host_address:-127.0.0.1}"

log "Deployment is healthy."
log "Dashboard: http://${host_address}:${IW_WEB_PORT}/"
log "API:       http://${host_address}:${IW_API_PORT}/"
log "API docs:  http://${host_address}:${IW_API_PORT}/docs"
log "Postgres host port: ${host_address}:${IW_DB_PORT}"
