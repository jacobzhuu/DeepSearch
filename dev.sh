#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

APP_MODULE="services.orchestrator.app.main:app"
LOG_DIR="${DEV_LOG_DIR:-$PROJECT_ROOT/.logs}"
RUN_DIR="${DEV_RUN_DIR:-$PROJECT_ROOT/.run}"
ENV_FILE="${DEV_ENV_FILE:-$PROJECT_ROOT/.env}"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
MOCK_SEARCH_PID_FILE="$RUN_DIR/mock-searxng.pid"

BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
MOCK_SEARCH_LOG="$LOG_DIR/mock-searxng.log"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

STARTUP_CLEANUP_ON_ERROR=false

log() { printf "%b[INFO]%b %s\n" "$GREEN" "$NC" "$*"; }
warn() { printf "%b[WARN]%b %s\n" "$YELLOW" "$NC" "$*" >&2; }
note() { printf "%b[NOTE]%b %s\n" "$BLUE" "$NC" "$*"; }
fail() { printf "%b[ERROR]%b %s\n" "$RED" "$NC" "$*" >&2; return 1; }

cleanup_after_startup_error() {
    local exit_code=$?
    if [ "$STARTUP_CLEANUP_ON_ERROR" = true ]; then
        STARTUP_CLEANUP_ON_ERROR=false
        warn "Startup failed; stopping processes started by this script."
        stop_services || true
    fi
    exit "$exit_code"
}
trap cleanup_after_startup_error ERR

is_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

is_falsey() {
    case "${1:-}" in
        0|false|FALSE|no|NO|n|N|off|OFF) return 0 ;;
        *) return 1 ;;
    esac
}

trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf "%s" "$value"
}

setup_dirs() {
    mkdir -p "$LOG_DIR" "$RUN_DIR" "$PROJECT_ROOT/data/logs"
}

load_env_file() {
    if is_falsey "${DEV_LOAD_ENV:-true}"; then
        note "Skipping env file load because DEV_LOAD_ENV=false."
        return 0
    fi

    if [ ! -f "$ENV_FILE" ]; then
        warn "Env file not found: $ENV_FILE"
        warn "Continuing with the current shell environment."
        return 0
    fi

    log "Loading env file without shell evaluation: $ENV_FILE"
    local line raw key value
    while IFS= read -r raw || [ -n "$raw" ]; do
        line="${raw%$'\r'}"
        line="$(trim "$line")"
        [ -z "$line" ] && continue
        [[ "$line" == \#* ]] && continue
        if [[ "$line" == export[[:space:]]* ]]; then
            line="$(trim "${line#export}")"
        fi
        if [[ "$line" != *=* ]]; then
            warn "Ignoring malformed env line: $raw"
            continue
        fi

        key="$(trim "${line%%=*}")"
        value="$(trim "${line#*=}")"
        if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            warn "Ignoring env line with invalid key: $key"
            continue
        fi

        if [[ ( "$value" == \"*\" && "$value" == *\" ) || ( "$value" == \'*\' && "$value" == *\' ) ]]; then
            value="${value:1:${#value}-2}"
        fi

        if [ -z "${!key+x}" ]; then
            export "$key=$value"
        fi
    done < "$ENV_FILE"
}

resolve_config() {
    PYTHON_BIN="${PYTHON:-python3}"
    NPM_BIN="${NPM:-npm}"

    BACKEND_HOST="${DEV_BACKEND_HOST:-127.0.0.1}"
    BACKEND_PORT="${DEV_BACKEND_PORT:-${APP_PORT:-8000}}"
    FRONTEND_HOST="${DEV_FRONTEND_HOST:-127.0.0.1}"
    FRONTEND_PORT="${DEV_FRONTEND_PORT:-5173}"
    MOCK_SEARCH_HOST="${DEV_MOCK_SEARXNG_HOST:-127.0.0.1}"
    MOCK_SEARCH_PORT="${DEV_MOCK_SEARXNG_PORT:-18080}"
    MOCK_SEARCH_URL="http://$MOCK_SEARCH_HOST:$MOCK_SEARCH_PORT"

    BACKEND_PROBE_HOST="$BACKEND_HOST"
    FRONTEND_PROBE_HOST="$FRONTEND_HOST"
    [ "$BACKEND_PROBE_HOST" = "0.0.0.0" ] && BACKEND_PROBE_HOST="127.0.0.1"
    [ "$FRONTEND_PROBE_HOST" = "0.0.0.0" ] && FRONTEND_PROBE_HOST="127.0.0.1"

    BACKEND_URL="${DEV_BACKEND_URL:-http://$BACKEND_PROBE_HOST:$BACKEND_PORT}"
    FRONTEND_URL="${DEV_FRONTEND_URL:-http://$FRONTEND_PROBE_HOST:$FRONTEND_PORT}"

    export APP_HOST="$BACKEND_HOST"
    export APP_PORT="$BACKEND_PORT"
    export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
    export VITE_API_BASE_URL="${VITE_API_BASE_URL:-$BACKEND_URL}"
}

print_config_summary() {
    echo ""
    echo "Runtime configuration:"
    echo "  project:          $PROJECT_ROOT"
    echo "  env file:         $ENV_FILE"
    echo "  run dir:          $RUN_DIR"
    echo "  log dir:          $LOG_DIR"
    echo "  backend bind:     $BACKEND_HOST:$BACKEND_PORT"
    echo "  backend probe:    $BACKEND_URL/healthz"
    echo "  frontend bind:    $FRONTEND_HOST:$FRONTEND_PORT"
    echo "  frontend probe:   $FRONTEND_URL"
    echo "  frontend API URL: $VITE_API_BASE_URL"
    echo "  search provider:  ${SEARCH_PROVIDER:-searxng}"
    echo "  index backend:    ${INDEX_BACKEND:-opensearch}"
    echo "  snapshot backend: ${SNAPSHOT_STORAGE_BACKEND:-filesystem}"
    echo ""
}

require_command() {
    local command_name="$1"
    local hint="$2"
    command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required. $hint"
}

check_dependencies() {
    require_command "$PYTHON_BIN" "Set PYTHON=/path/to/python if needed."
    require_command curl "Install curl for readiness checks."

    if ! is_truthy "${DEV_SKIP_FRONTEND:-false}"; then
        require_command "$NPM_BIN" "Install Node.js/npm or run with DEV_SKIP_FRONTEND=true."
    fi

    if ! command -v setsid >/dev/null 2>&1; then
        warn "setsid is unavailable; child process cleanup may be less complete."
    fi

    if ! command -v lsof >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
        warn "Neither lsof nor ss is available; port diagnostics will be limited."
    fi
}

pid_from_file() {
    local pid_file="$1"
    [ -f "$pid_file" ] || return 1
    local pid
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    printf "%s" "$pid"
}

process_args() {
    local pid="$1"
    ps -p "$pid" -o args= 2>/dev/null || true
}

process_matches() {
    local pid="$1"
    local pattern="$2"
    local args
    args="$(process_args "$pid")"
    [ -n "$args" ] && [[ "$args" =~ $pattern ]]
}

descendant_pids() {
    local root_pid="$1"
    command -v pgrep >/dev/null 2>&1 || return 0

    local child
    while IFS= read -r child; do
        [ -n "$child" ] || continue
        printf "%s\n" "$child"
        descendant_pids "$child"
    done < <(pgrep -P "$root_pid" 2>/dev/null || true)
}

show_port_listeners() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
        ss -ltnp "sport = :$port" 2>/dev/null || true
    else
        warn "No listener tool available for port $port."
    fi
}

port_has_listener() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1
    elif command -v ss >/dev/null 2>&1; then
        ss -ltn "sport = :$port" 2>/dev/null | awk 'NR > 1 { found=1 } END { exit found ? 0 : 1 }'
    else
        return 1
    fi
}

require_port_free() {
    local port="$1"
    local service="$2"
    if port_has_listener "$port"; then
        warn "$service port $port is already in use:"
        show_port_listeners "$port"
        fail "Refusing to start $service on an occupied port. Stop the other process or choose DEV_${service^^}_PORT."
    fi
}

recent_log_tail() {
    local log_file="$1"
    if [ -f "$log_file" ]; then
        echo ""
        echo "Last ${DEV_LOG_LINES:-80} lines from $log_file:"
        tail -n "${DEV_LOG_LINES:-80}" "$log_file" || true
        echo ""
    else
        warn "Log file does not exist yet: $log_file"
    fi
}

wait_for_http() {
    local url="$1"
    local name="$2"
    local log_file="$3"
    local max_attempts="${DEV_READY_ATTEMPTS:-60}"
    local attempt=1
    local curl_output

    log "Waiting for $name at $url ..."
    while [ "$attempt" -le "$max_attempts" ]; do
        if curl_output="$(curl -fsS --max-time 2 "$url" 2>&1 >/dev/null)"; then
            log "$name is ready."
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done

    warn "$name did not become ready at $url."
    [ -n "${curl_output:-}" ] && warn "Last curl error: $curl_output"
    recent_log_tail "$log_file"
    fail "$name readiness check failed."
}

start_background() {
    local name="$1"
    local pid_file="$2"
    local log_file="$3"
    shift 3

    : > "$log_file"
    if command -v setsid >/dev/null 2>&1; then
        nohup setsid "$@" > "$log_file" 2>&1 &
    else
        nohup "$@" > "$log_file" 2>&1 &
    fi

    local pid=$!
    printf "%s\n" "$pid" > "$pid_file"
    printf "%s\n" "$*" > "$pid_file.command"
    log "Started $name with PID $pid. Log: $log_file"

    sleep 1
    if ! kill -0 "$pid" >/dev/null 2>&1; then
        recent_log_tail "$log_file"
        fail "$name exited immediately."
    fi
}

stop_process_gracefully() {
    local pid_file="$1"
    local name="$2"
    local expected_pattern="$3"
    local pid

    if ! pid="$(pid_from_file "$pid_file")"; then
        rm -f "$pid_file" "$pid_file.command"
        log "$name is not running (no usable PID file)."
        return 0
    fi

    if ! kill -0 "$pid" >/dev/null 2>&1; then
        warn "$name PID file was stale: $pid"
        rm -f "$pid_file" "$pid_file.command"
        return 0
    fi

    if ! process_matches "$pid" "$expected_pattern"; then
        warn "$name PID $pid does not match the expected command; leaving it running."
        warn "Actual command: $(process_args "$pid")"
        rm -f "$pid_file" "$pid_file.command"
        return 0
    fi

    local descendants=()
    local child
    while IFS= read -r child; do
        [ -n "$child" ] && descendants+=("$child")
    done < <(descendant_pids "$pid")

    log "Stopping $name process group rooted at PID $pid ..."
    if ! kill -- "-$pid" >/dev/null 2>&1; then
        kill "$pid" "${descendants[@]}" >/dev/null 2>&1 || true
    fi

    local attempt
    for attempt in $(seq 1 30); do
        if ! kill -0 "$pid" >/dev/null 2>&1; then
            rm -f "$pid_file" "$pid_file.command"
            log "$name stopped."
            return 0
        fi
        sleep 0.5
    done

    warn "$name did not stop after SIGTERM; sending SIGKILL."
    if ! kill -KILL -- "-$pid" >/dev/null 2>&1; then
        kill -KILL "$pid" "${descendants[@]}" >/dev/null 2>&1 || true
    fi
    rm -f "$pid_file" "$pid_file.command"
    log "$name stopped."
}

stop_services() {
    stop_process_gracefully "$FRONTEND_PID_FILE" "Frontend" "vite|node|npm"
    stop_process_gracefully "$BACKEND_PID_FILE" "Backend" "uvicorn|services\.orchestrator\.app\.main:app"
    stop_process_gracefully "$MOCK_SEARCH_PID_FILE" "Mock SearXNG" "mock_searxng\.py"
}

run_step() {
    local description="$1"
    shift
    log "$description"
    if ! (cd "$PROJECT_ROOT" && "$@"); then
        fail "$description failed."
    fi
}

run_init_steps() {
    if is_falsey "${DEV_RUN_INIT:-true}"; then
        note "Skipping init steps because DEV_RUN_INIT=false."
        return 0
    fi

    if ! is_falsey "${DEV_RUN_MIGRATIONS:-true}"; then
        run_step "Running database migrations" ./scripts/migrate.sh upgrade head
    else
        note "Skipping migrations because DEV_RUN_MIGRATIONS=false."
    fi

    if [ "${SNAPSHOT_STORAGE_BACKEND:-filesystem}" = "minio" ]; then
        if is_falsey "${DEV_INIT_BUCKETS:-auto}"; then
            note "Skipping MinIO bucket init because DEV_INIT_BUCKETS=false."
        elif [ -n "${MINIO_ENDPOINT:-}" ]; then
            run_step "Initializing MinIO buckets" "$PYTHON_BIN" scripts/init_buckets.py
        else
            warn "SNAPSHOT_STORAGE_BACKEND=minio but MINIO_ENDPOINT is empty; bucket init skipped."
        fi
    else
        note "Skipping bucket init for SNAPSHOT_STORAGE_BACKEND=${SNAPSHOT_STORAGE_BACKEND:-filesystem}."
    fi

    if [ "${INDEX_BACKEND:-opensearch}" = "opensearch" ]; then
        if is_falsey "${DEV_INIT_INDEX:-auto}"; then
            note "Skipping OpenSearch index init because DEV_INIT_INDEX=false."
        elif [ -n "${OPENSEARCH_BASE_URL:-}" ]; then
            run_step "Initializing OpenSearch index" "$PYTHON_BIN" scripts/init_index.py
        else
            warn "INDEX_BACKEND=opensearch but OPENSEARCH_BASE_URL is empty; index init skipped."
        fi
    else
        note "Skipping index init for INDEX_BACKEND=${INDEX_BACKEND:-opensearch}."
    fi
}

start_mock_search_if_requested() {
    if ! is_truthy "${DEV_START_MOCK_SEARXNG:-false}"; then
        return 0
    fi

    require_port_free "$MOCK_SEARCH_PORT" "MOCK_SEARCH"
    export SEARXNG_BASE_URL="$MOCK_SEARCH_URL"
    start_background \
        "Mock SearXNG" \
        "$MOCK_SEARCH_PID_FILE" \
        "$MOCK_SEARCH_LOG" \
        "$PYTHON_BIN" "$PROJECT_ROOT/scripts/mock_searxng.py" \
        --host "$MOCK_SEARCH_HOST" \
        --port "$MOCK_SEARCH_PORT"
    wait_for_http "$MOCK_SEARCH_URL/search?q=deepsearch&format=json" "Mock SearXNG" "$MOCK_SEARCH_LOG"
}

start_backend() {
    if is_truthy "${DEV_SKIP_BACKEND:-false}"; then
        note "Skipping backend start because DEV_SKIP_BACKEND=true."
        return 0
    fi

    require_port_free "$BACKEND_PORT" "BACKEND"

    local backend_args=(
        "$PYTHON_BIN" -m uvicorn "$APP_MODULE"
        --host "$BACKEND_HOST"
        --port "$BACKEND_PORT"
    )
    if ! is_falsey "${DEV_BACKEND_RELOAD:-true}"; then
        backend_args+=(--reload)
    fi

    (cd "$PROJECT_ROOT" && start_background "Backend" "$BACKEND_PID_FILE" "$BACKEND_LOG" "${backend_args[@]}")
    wait_for_http "$BACKEND_URL/healthz" "Backend" "$BACKEND_LOG"
}

frontend_install_command() {
    if [ -f "$PROJECT_ROOT/apps/web/package-lock.json" ]; then
        printf "%s" "ci"
    else
        printf "%s" "install"
    fi
}

ensure_frontend_deps() {
    if is_falsey "${DEV_NPM_INSTALL:-auto}"; then
        note "Skipping frontend dependency install because DEV_NPM_INSTALL=false."
        return 0
    fi

    local install_cmd
    install_cmd="$(frontend_install_command)"
    if is_truthy "${DEV_NPM_INSTALL:-auto}" || [ ! -d "$PROJECT_ROOT/apps/web/node_modules" ]; then
        log "Installing frontend dependencies with npm $install_cmd."
        (cd "$PROJECT_ROOT/apps/web" && "$NPM_BIN" "$install_cmd")
    else
        note "Frontend node_modules exists; skipping npm install."
    fi
}

start_frontend() {
    if is_truthy "${DEV_SKIP_FRONTEND:-false}"; then
        note "Skipping frontend start because DEV_SKIP_FRONTEND=true."
        return 0
    fi

    require_port_free "$FRONTEND_PORT" "FRONTEND"
    ensure_frontend_deps

    local vite_bin="$PROJECT_ROOT/apps/web/node_modules/.bin/vite"
    if [ ! -x "$vite_bin" ]; then
        fail "Vite binary not found at $vite_bin. Run npm install or set DEV_NPM_INSTALL=true."
    fi

    (
        cd "$PROJECT_ROOT/apps/web"
        start_background \
            "Frontend" \
            "$FRONTEND_PID_FILE" \
            "$FRONTEND_LOG" \
            "$vite_bin" \
            --host "$FRONTEND_HOST" \
            --port "$FRONTEND_PORT" \
            --strictPort
    )
    wait_for_http "$FRONTEND_URL" "Frontend" "$FRONTEND_LOG"
}

start_all() {
    setup_dirs
    load_env_file
    resolve_config
    check_dependencies
    print_config_summary

    STARTUP_CLEANUP_ON_ERROR=true
    stop_services
    start_mock_search_if_requested
    run_init_steps
    start_backend
    start_frontend
    STARTUP_CLEANUP_ON_ERROR=false

    print_status
}

print_process_status() {
    local name="$1"
    local pid_file="$2"
    local pattern="$3"
    local pid args

    if ! pid="$(pid_from_file "$pid_file")"; then
        printf "  %-13s stopped\n" "$name"
        return 0
    fi

    if kill -0 "$pid" >/dev/null 2>&1; then
        args="$(process_args "$pid")"
        if process_matches "$pid" "$pattern"; then
            printf "  %-13s running pid=%s\n" "$name" "$pid"
        else
            printf "  %-13s unowned pid=%s\n" "$name" "$pid"
        fi
        printf "  %-13s command=%s\n" "" "$args"
    else
        printf "  %-13s stale pid=%s\n" "$name" "$pid"
    fi
}

print_status() {
    echo ""
    echo "DeepSearch local services"
    echo "-------------------------"
    print_process_status "backend" "$BACKEND_PID_FILE" "uvicorn|services\.orchestrator\.app\.main:app"
    print_process_status "frontend" "$FRONTEND_PID_FILE" "vite|node|npm"
    print_process_status "mock-searxng" "$MOCK_SEARCH_PID_FILE" "mock_searxng\.py"
    echo ""
    echo "URLs:"
    echo "  backend:  $BACKEND_URL"
    echo "  frontend: $FRONTEND_URL"
    if is_truthy "${DEV_START_MOCK_SEARXNG:-false}"; then
        echo "  search:   $MOCK_SEARCH_URL"
    fi
    echo ""
    echo "Logs:"
    echo "  backend:  $BACKEND_LOG"
    echo "  frontend: $FRONTEND_LOG"
    echo "  search:   $MOCK_SEARCH_LOG"
    echo ""
    echo "Commands:"
    echo "  ./dev.sh status"
    echo "  ./dev.sh logs backend"
    echo "  ./dev.sh stop"
    echo ""
}

status_command() {
    setup_dirs
    load_env_file
    resolve_config
    print_config_summary
    print_status

    echo "Port listeners:"
    echo "  backend port $BACKEND_PORT"
    show_port_listeners "$BACKEND_PORT"
    echo "  frontend port $FRONTEND_PORT"
    show_port_listeners "$FRONTEND_PORT"
    if is_truthy "${DEV_START_MOCK_SEARXNG:-false}"; then
        echo "  mock search port $MOCK_SEARCH_PORT"
        show_port_listeners "$MOCK_SEARCH_PORT"
    fi
}

doctor_command() {
    setup_dirs
    load_env_file
    resolve_config
    print_config_summary
    check_dependencies

    log "Checking configured ports."
    if port_has_listener "$BACKEND_PORT"; then
        warn "Backend port $BACKEND_PORT has a listener:"
        show_port_listeners "$BACKEND_PORT"
    else
        log "Backend port $BACKEND_PORT is free."
    fi
    if ! is_truthy "${DEV_SKIP_FRONTEND:-false}"; then
        if port_has_listener "$FRONTEND_PORT"; then
            warn "Frontend port $FRONTEND_PORT has a listener:"
            show_port_listeners "$FRONTEND_PORT"
        else
            log "Frontend port $FRONTEND_PORT is free."
        fi
    fi

    if [ "${INDEX_BACKEND:-opensearch}" = "opensearch" ] && [ -n "${OPENSEARCH_BASE_URL:-}" ]; then
        if curl -sS --max-time 3 -o /dev/null "$OPENSEARCH_BASE_URL"; then
            log "OpenSearch endpoint is reachable: $OPENSEARCH_BASE_URL"
        else
            warn "OpenSearch endpoint is not reachable: $OPENSEARCH_BASE_URL"
        fi
    fi

    if [ "${SNAPSHOT_STORAGE_BACKEND:-filesystem}" = "minio" ] && [ -n "${MINIO_ENDPOINT:-}" ]; then
        note "MinIO is configured. Bucket initialization will be handled by scripts/init_buckets.py."
    fi

    log "Doctor completed. Warnings above are diagnostic; start/restart will enforce required checks."
}

logs_command() {
    setup_dirs
    local target="${1:-all}"
    local files=()
    case "$target" in
        backend) files=("$BACKEND_LOG") ;;
        frontend) files=("$FRONTEND_LOG") ;;
        search|mock|mock-searxng) files=("$MOCK_SEARCH_LOG") ;;
        all) files=("$BACKEND_LOG" "$FRONTEND_LOG" "$MOCK_SEARCH_LOG") ;;
        *) fail "Unknown log target: $target" ;;
    esac

    local existing=()
    local file
    for file in "${files[@]}"; do
        if [ -f "$file" ]; then
            existing+=("$file")
        else
            warn "Log file does not exist: $file"
        fi
    done

    [ "${#existing[@]}" -gt 0 ] || fail "No log files are available for target: $target"
    tail -n "${DEV_LOG_LINES:-120}" -f "${existing[@]}"
}

args_include() {
    local needle="$1"
    shift
    local arg
    for arg in "$@"; do
        [ "$arg" = "$needle" ] && return 0
    done
    return 1
}

smoke_command() {
    setup_dirs
    load_env_file
    resolve_config

    local base_url="$BACKEND_URL"
    if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
        base_url="$1"
        shift
    fi

    local smoke_args=("$@")
    if [ "${SEARCH_PROVIDER:-searxng}" = "smoke" ]; then
        if ! args_include "--domain-allow" "${smoke_args[@]}"; then
            smoke_args+=(--domain-allow deepsearch-smoke.local)
        fi
        if ! args_include "--claim-query" "${smoke_args[@]}"; then
            smoke_args+=(--claim-query smoke)
        fi
    fi

    run_step "Running smoke test against $base_url" \
        "$PYTHON_BIN" scripts/smoke_test.py --base-url "$base_url" "${smoke_args[@]}"
}

init_command() {
    setup_dirs
    load_env_file
    resolve_config
    check_dependencies
    run_init_steps
}

show_help() {
    cat <<'EOF'
Usage: ./dev.sh [COMMAND] [ARGS]

Commands:
  start       Start or converge local services. This stops only processes from this script first.
  restart     Same as start; provided for operator clarity.
  stop        Stop backend, frontend, and optional mock search processes started by this script.
  status      Show process, URL, log, and port diagnostics.
  doctor      Check dependencies, config, and common service reachability.
  init        Run migration, bucket, and index initialization without starting the UI/API.
  smoke [URL] [ARGS]
              Run scripts/smoke_test.py against URL, defaulting to the configured backend.
              Extra ARGS are passed through to scripts/smoke_test.py.
  logs [name] Tail logs: backend, frontend, search, or all.
  help        Show this help.

Useful environment controls:
  DEV_ENV_FILE=/path/.env             Load a specific env file; default is ./.env.
  DEV_LOAD_ENV=false                  Do not read an env file.
  DEV_BACKEND_PORT=18000              Override backend port.
  DEV_FRONTEND_PORT=15173             Override frontend port.
  DEV_BACKEND_HOST=0.0.0.0            Bind backend beyond loopback intentionally.
  DEV_FRONTEND_HOST=0.0.0.0           Bind frontend beyond loopback intentionally.
  DEV_RUN_INIT=false                  Skip migrations, bucket init, and index init.
  DEV_SKIP_FRONTEND=true              Start backend only.
  DEV_BACKEND_RELOAD=false            Start uvicorn without --reload.
  DEV_START_MOCK_SEARXNG=true         Start scripts/mock_searxng.py and point SEARXNG_BASE_URL at it.
  SEARCH_PROVIDER=smoke INDEX_BACKEND=local
                                      Run deterministic dev mode without real search/OpenSearch.
EOF
}

main() {
    local command="${1:-start}"
    shift || true

    case "$command" in
        start|restart)
            start_all
            ;;
        stop)
            setup_dirs
            stop_services
            ;;
        status)
            status_command
            ;;
        doctor)
            doctor_command
            ;;
        init)
            init_command
            ;;
        smoke)
            smoke_command "$@"
            ;;
        logs)
            logs_command "$@"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            show_help >&2
            fail "Unknown command: $command"
            ;;
    esac
}

main "$@"
