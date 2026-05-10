#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${FULL_DEEPSEARCH_ENV_FILE:-$PROJECT_ROOT/.env.deepseek.local}"
BASE_ENV_FILE="${FULL_DEEPSEARCH_BASE_ENV_FILE:-$PROJECT_ROOT/.env}"
COMPOSE_PROJECT_NAME="${FULL_DEEPSEARCH_COMPOSE_PROJECT:-deepsearch-full}"
WAIT_SECONDS="${FULL_DEEPSEARCH_WAIT_SECONDS:-180}"
DOCKERD_LOG="${FULL_DEEPSEARCH_DOCKERD_LOG:-$PROJECT_ROOT/.logs/dockerd.log}"
DOCKERD_PID_FILE="${FULL_DEEPSEARCH_DOCKERD_PID_FILE:-$PROJECT_ROOT/.run/dockerd.pid}"
CONTAINERD_LOG="${FULL_DEEPSEARCH_CONTAINERD_LOG:-$PROJECT_ROOT/.logs/containerd.log}"
CONTAINERD_PID_FILE="${FULL_DEEPSEARCH_CONTAINERD_PID_FILE:-$PROJECT_ROOT/.run/containerd.pid}"
HOST_SEARXNG_LOG="${FULL_DEEPSEARCH_HOST_SEARXNG_LOG:-$PROJECT_ROOT/.logs/host-searxng.log}"
HOST_SEARXNG_PID_FILE="${FULL_DEEPSEARCH_HOST_SEARXNG_PID_FILE:-$PROJECT_ROOT/.run/host-searxng.pid}"
HOST_OPENSEARCH_LOG="${FULL_DEEPSEARCH_HOST_OPENSEARCH_LOG:-$PROJECT_ROOT/.logs/host-opensearch.log}"
HOST_OPENSEARCH_PID_FILE="${FULL_DEEPSEARCH_HOST_OPENSEARCH_PID_FILE:-$PROJECT_ROOT/.run/host-opensearch.pid}"
HOST_OPENSEARCH_VERSION="${FULL_DEEPSEARCH_OPENSEARCH_VERSION:-2.19.0}"
HOST_OPENSEARCH_ROOT="${FULL_DEEPSEARCH_OPENSEARCH_ROOT:-/share/zhuzy/services}"
HOST_OPENSEARCH_HOME="${FULL_DEEPSEARCH_OPENSEARCH_HOME:-$HOST_OPENSEARCH_ROOT/opensearch-$HOST_OPENSEARCH_VERSION}"
HOST_OPENSEARCH_DATA="${FULL_DEEPSEARCH_OPENSEARCH_DATA:-$HOST_OPENSEARCH_ROOT/opensearch-data}"
HOST_OPENSEARCH_USER="${FULL_DEEPSEARCH_OPENSEARCH_USER:-deepsearchos}"
HOST_OPENSEARCH_HEAP="${FULL_DEEPSEARCH_OPENSEARCH_HEAP:-512m}"
HOST_SEARXNG_BIN="${FULL_DEEPSEARCH_SEARXNG_BIN:-/root/anaconda3/envs/searxng311/bin/searxng-run}"
HOST_SEARXNG_SRC="${FULL_DEEPSEARCH_SEARXNG_SRC:-/share/zhuzy/services/searxng-src}"
HOST_SEARXNG_SETTINGS="${FULL_DEEPSEARCH_SEARXNG_SETTINGS:-/share/zhuzy/services/searxng-config/settings.yml}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

declare -A ORIGINAL_ENV=()

log() { printf "%b[INFO]%b %s\n" "$GREEN" "$NC" "$*"; }
warn() { printf "%b[WARN]%b %s\n" "$YELLOW" "$NC" "$*" >&2; }
note() { printf "%b[NOTE]%b %s\n" "$BLUE" "$NC" "$*"; }
fail() { printf "%b[ERROR]%b %s\n" "$RED" "$NC" "$*" >&2; exit 1; }

capture_original_env() {
    local entry key
    while IFS= read -r entry; do
        key="${entry%%=*}"
        [ -n "$key" ] && ORIGINAL_ENV["$key"]=1
    done < <(env)
}

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

load_env_file() {
    local file="$1"
    local override_previous="${2:-false}"
    local preserve_original="${3:-true}"
    local line raw key value

    [ -f "$file" ] || return 0
    log "Loading env file without shell evaluation: $file"
    while IFS= read -r raw || [ -n "$raw" ]; do
        line="${raw%$'\r'}"
        line="$(trim "$line")"
        [ -z "$line" ] && continue
        [[ "$line" == \#* ]] && continue
        if [[ "$line" == export[[:space:]]* ]]; then
            line="$(trim "${line#export}")"
        fi
        if [[ "$line" != *=* ]]; then
            warn "Ignoring malformed env line in $file: $raw"
            continue
        fi

        key="$(trim "${line%%=*}")"
        value="$(trim "${line#*=}")"
        if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            warn "Ignoring env line with invalid key in $file: $key"
            continue
        fi
        if [[ ( "$value" == \"*\" && "$value" == *\" ) || ( "$value" == \'*\' && "$value" == *\' ) ]]; then
            value="${value:1:${#value}-2}"
        fi

        if is_truthy "$preserve_original" && [ -n "${ORIGINAL_ENV[$key]+x}" ]; then
            continue
        fi
        if is_truthy "$override_previous" || [ -z "${!key+x}" ]; then
            export "$key=$value"
        fi
    done < "$file"
}

write_default_env_file() {
    if [ -f "$ENV_FILE" ]; then
        return 0
    fi

    if [ -z "${LLM_API_KEY:-}" ]; then
        fail "Missing $ENV_FILE and LLM_API_KEY is not exported. Run: LLM_API_KEY=<your-deepseek-key> $0 restart"
    fi

    log "Creating full DeepSearch env file: $ENV_FILE"
    umask 077
    cat > "$ENV_FILE" <<EOF
# Full local DeepSearch profile. This file is ignored by git.
SEARCH_PROVIDER=searxng
SEARXNG_BASE_URL=http://127.0.0.1:8888

INDEX_BACKEND=opensearch
OPENSEARCH_BASE_URL=http://127.0.0.1:9200
OPENSEARCH_INDEX_NAME=source-chunks-v1
OPENSEARCH_USERNAME=
OPENSEARCH_PASSWORD=
OPENSEARCH_VERIFY_TLS=false
OPENSEARCH_CA_BUNDLE_PATH=

SNAPSHOT_STORAGE_BACKEND=filesystem
SNAPSHOT_STORAGE_ROOT=./data/snapshots

LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_API_KEY=$LLM_API_KEY
RESEARCH_PLANNER_ENABLED=true
LLM_REPORT_WRITER_ENABLED=true
LLM_CLAIM_REVIEWER_ENABLED=true
EOF
}

require_command() {
    local command_name="$1"
    local hint="$2"
    command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required. $hint"
}

start_detached() {
    local pid_file="$1"
    local log_file="$2"
    shift 2

    mkdir -p "$(dirname "$log_file")" "$(dirname "$pid_file")"
    : > "$log_file"
    if command -v setsid >/dev/null 2>&1; then
        nohup setsid "$@" > "$log_file" 2>&1 &
    else
        warn "setsid is unavailable; managed dependencies may not survive parent-shell cleanup."
        nohup "$@" > "$log_file" 2>&1 &
    fi

    local pid
    pid=$!
    printf "%s\n" "$pid" > "$pid_file"
    printf "%s\n" "$*" > "$pid_file.command"
    sleep 1
    if ! kill -0 "$pid" >/dev/null 2>&1; then
        warn "Managed dependency exited immediately. Inspect: $log_file"
        return 1
    fi
}

compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose "$@"
    else
        fail "Docker Compose is required. Install docker compose or docker-compose."
    fi
}

wait_for_docker() {
    local start now
    start="$(date +%s)"
    while true; do
        if docker info >/dev/null 2>&1; then
            log "Docker daemon is reachable."
            return 0
        fi
        now="$(date +%s)"
        if [ $((now - start)) -ge "${FULL_DEEPSEARCH_DOCKER_WAIT_SECONDS:-45}" ]; then
            return 1
        fi
        sleep 1
    done
}

containerd_is_reachable() {
    command -v ctr >/dev/null 2>&1 && ctr version >/dev/null 2>&1
}

wait_for_containerd() {
    local start now
    start="$(date +%s)"
    while true; do
        if containerd_is_reachable; then
            log "containerd is reachable."
            return 0
        fi
        now="$(date +%s)"
        if [ $((now - start)) -ge "${FULL_DEEPSEARCH_CONTAINERD_WAIT_SECONDS:-30}" ]; then
            return 1
        fi
        sleep 1
    done
}

systemd_is_available() {
    [ -d /run/systemd/system ] && [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d '[:space:]')" = "systemd" ]
}

direct_dockerd_is_possible() {
    command -v capsh >/dev/null 2>&1 || return 0
    local caps
    caps="$(capsh --print 2>/dev/null || true)"
    if printf "%s\n" "$caps" | grep -q '!cap_sys_admin'; then
        return 1
    fi
    if printf "%s\n" "$caps" | grep -q '!cap_net_admin'; then
        return 1
    fi
    return 0
}

try_start_docker_with_service() {
    command -v service >/dev/null 2>&1 || return 1
    warn "Docker daemon is not reachable; trying service docker start."
    service docker start >/dev/null 2>&1 || return 1
    wait_for_docker
}

try_start_docker_with_systemctl() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemd_is_available || return 1
    warn "Docker daemon is not reachable; trying systemctl start docker."
    systemctl start docker >/dev/null 2>&1 || return 1
    wait_for_docker
}

try_start_docker_with_dockerd() {
    command -v dockerd >/dev/null 2>&1 || return 1
    if ! direct_dockerd_is_possible; then
        warn "Skipping direct dockerd startup because this environment lacks Docker daemon capabilities such as CAP_SYS_ADMIN/CAP_NET_ADMIN."
        return 1
    fi
    if ! containerd_is_reachable; then
        command -v containerd >/dev/null 2>&1 || return 1
        warn "containerd is not reachable; trying direct containerd startup."
        mkdir -p "$(dirname "$CONTAINERD_LOG")" "$(dirname "$CONTAINERD_PID_FILE")"
        nohup containerd ${FULL_DEEPSEARCH_CONTAINERD_ARGS:-} > "$CONTAINERD_LOG" 2>&1 &
        printf "%s\n" "$!" > "$CONTAINERD_PID_FILE"
        wait_for_containerd || return 1
    fi

    warn "Docker daemon is not reachable; trying direct dockerd startup."
    mkdir -p "$(dirname "$DOCKERD_LOG")" "$(dirname "$DOCKERD_PID_FILE")"
    nohup dockerd ${FULL_DEEPSEARCH_DOCKERD_ARGS:-} > "$DOCKERD_LOG" 2>&1 &
    printf "%s\n" "$!" > "$DOCKERD_PID_FILE"
    wait_for_docker
}

start_docker_if_needed() {
    require_command docker "Install Docker, or start OpenSearch and SearXNG yourself and run with FULL_DEEPSEARCH_SKIP_DEPS=true."
    if docker info >/dev/null 2>&1; then
        return 0
    fi

    if is_falsey "${FULL_DEEPSEARCH_START_DOCKER:-true}"; then
        fail "Docker daemon is not reachable."
    fi

    if try_start_docker_with_service; then
        return 0
    fi
    if try_start_docker_with_systemctl; then
        return 0
    fi
    if try_start_docker_with_dockerd; then
        return 0
    fi

    warn "Docker daemon is still unreachable."
    warn "If containerd was attempted, inspect: $CONTAINERD_LOG"
    warn "If dockerd was attempted, inspect: $DOCKERD_LOG"
    if ! direct_dockerd_is_possible; then
        fail "Could not start Docker daemon in this non-privileged environment. Run this script on the host/privileged container, or expose OpenSearch and SearXNG from outside and rerun with FULL_DEEPSEARCH_SKIP_DEPS=true."
    fi
    fail "Could not start Docker daemon. Start Docker manually, or run with FULL_DEEPSEARCH_SKIP_DEPS=true after OpenSearch and SearXNG are already running."
}

url_port() {
    local url="$1"
    local fallback="$2"
    local rest host_port port
    rest="${url#*://}"
    rest="${rest%%/*}"
    rest="${rest##*@}"
    host_port="$rest"
    if [[ "$host_port" == \[*\]* ]]; then
        port="${host_port##*]:}"
        [ "$port" != "$host_port" ] && printf "%s" "$port" || printf "%s" "$fallback"
        return 0
    fi
    if [[ "$host_port" == *:* ]]; then
        port="${host_port##*:}"
        [[ "$port" =~ ^[0-9]+$ ]] && printf "%s" "$port" || printf "%s" "$fallback"
        return 0
    fi
    printf "%s" "$fallback"
}

curl_auth_args() {
    if [ -n "${OPENSEARCH_USERNAME:-}" ] || [ -n "${OPENSEARCH_PASSWORD:-}" ]; then
        printf "%s\n" "-u"
        printf "%s\n" "${OPENSEARCH_USERNAME:-}:${OPENSEARCH_PASSWORD:-}"
    fi
}

wait_for_url() {
    local name="$1"
    local url="$2"
    shift 2
    local start now
    start="$(date +%s)"
    log "Waiting for $name at $url ..."
    while true; do
        if curl -fsSk --max-time 5 "$@" "$url" >/dev/null 2>&1; then
            log "$name is reachable."
            return 0
        fi
        now="$(date +%s)"
        if [ $((now - start)) -ge "$WAIT_SECONDS" ]; then
            fail "$name did not become reachable within ${WAIT_SECONDS}s: $url"
        fi
        sleep 2
    done
}

wait_for_url_result() {
    local name="$1"
    local url="$2"
    shift 2
    local start now
    start="$(date +%s)"
    log "Waiting for $name at $url ..."
    while true; do
        if curl -fsSk --max-time 5 "$@" "$url" >/dev/null 2>&1; then
            log "$name is reachable."
            return 0
        fi
        now="$(date +%s)"
        if [ $((now - start)) -ge "$WAIT_SECONDS" ]; then
            warn "$name did not become reachable within ${WAIT_SECONDS}s: $url"
            return 1
        fi
        sleep 2
    done
}

url_is_reachable() {
    local url="$1"
    shift
    curl -fsSk --max-time 5 "$@" "$url" >/dev/null 2>&1
}

wait_for_searxng() {
    local url="${SEARXNG_BASE_URL%/}/search?q=deepsearch&format=json"
    wait_for_url "SearXNG JSON search" "$url"
}

wait_for_searxng_result() {
    local url="${SEARXNG_BASE_URL%/}/search?q=deepsearch&format=json"
    wait_for_url_result "SearXNG JSON search" "$url"
}

wait_for_opensearch() {
    local auth_args=()
    local arg
    while IFS= read -r arg; do
        [ -n "$arg" ] && auth_args+=("$arg")
    done < <(curl_auth_args)
    wait_for_url "OpenSearch" "${OPENSEARCH_BASE_URL%/}/" "${auth_args[@]}"
}

wait_for_opensearch_result() {
    local auth_args=()
    local arg
    while IFS= read -r arg; do
        [ -n "$arg" ] && auth_args+=("$arg")
    done < <(curl_auth_args)
    wait_for_url_result "OpenSearch" "${OPENSEARCH_BASE_URL%/}/" "${auth_args[@]}"
}

opensearch_is_reachable() {
    local auth_args=()
    local arg
    while IFS= read -r arg; do
        [ -n "$arg" ] && auth_args+=("$arg")
    done < <(curl_auth_args)
    url_is_reachable "${OPENSEARCH_BASE_URL%/}/" "${auth_args[@]}"
}

searxng_is_reachable() {
    url_is_reachable "${SEARXNG_BASE_URL%/}/search?q=deepsearch&format=json"
}

ensure_full_llm_config() {
    local missing=()
    is_truthy "${LLM_ENABLED:-false}" || missing+=("LLM_ENABLED=true")
    is_truthy "${RESEARCH_PLANNER_ENABLED:-false}" || missing+=("RESEARCH_PLANNER_ENABLED=true")
    is_truthy "${LLM_REPORT_WRITER_ENABLED:-false}" || missing+=("LLM_REPORT_WRITER_ENABLED=true")
    [ -n "${LLM_PROVIDER:-}" ] && [ "${LLM_PROVIDER:-noop}" != "noop" ] || missing+=("LLM_PROVIDER=openai_compatible")
    [ -n "${LLM_BASE_URL:-}" ] || missing+=("LLM_BASE_URL")
    [ -n "${LLM_MODEL:-}" ] || missing+=("LLM_MODEL")
    [ -n "${LLM_API_KEY:-}" ] || missing+=("LLM_API_KEY")
    [ "${SEARCH_PROVIDER:-searxng}" = "searxng" ] || missing+=("SEARCH_PROVIDER=searxng")
    [ "${INDEX_BACKEND:-opensearch}" = "opensearch" ] || missing+=("INDEX_BACKEND=opensearch")

    if [ "${#missing[@]}" -gt 0 ]; then
        printf "%b[ERROR]%b Full DeepSearch config is incomplete:\n" "$RED" "$NC" >&2
        printf "  - %s\n" "${missing[@]}" >&2
        exit 1
    fi
}

derive_compose_ports() {
    local searxng_port opensearch_port
    searxng_port="$(url_port "${SEARXNG_BASE_URL:-http://127.0.0.1:8888}" "8888")"
    opensearch_port="$(url_port "${OPENSEARCH_BASE_URL:-http://127.0.0.1:9200}" "9200")"
    export SEARXNG_PORT="${SEARXNG_PORT:-$searxng_port}"
    export OPENSEARCH_PORT="${OPENSEARCH_PORT:-$opensearch_port}"
    export COMPOSE_PROJECT_NAME
}

start_docker_dependencies() {
    start_docker_if_needed
    derive_compose_ports
    log "Starting Docker dependencies: OpenSearch on $OPENSEARCH_PORT, SearXNG on $SEARXNG_PORT."
    (
        cd "$PROJECT_ROOT"
        compose_cmd \
            -f docker-compose.yml \
            -f docker-compose.dev.yml \
            --profile search \
            up -d opensearch searxng
    )
    wait_for_opensearch
    wait_for_searxng
}

start_host_searxng() {
    if searxng_is_reachable; then
        log "Host-local SearXNG is already reachable at $SEARXNG_BASE_URL."
        return 0
    fi
    [ -x "$HOST_SEARXNG_BIN" ] || {
        warn "Host-local SearXNG binary not found: $HOST_SEARXNG_BIN"
        return 1
    }
    [ -f "$HOST_SEARXNG_SETTINGS" ] || {
        warn "Host-local SearXNG settings not found: $HOST_SEARXNG_SETTINGS"
        return 1
    }

    log "Starting host-local SearXNG from $HOST_SEARXNG_BIN."
    (
        cd "$HOST_SEARXNG_SRC" 2>/dev/null || cd "$PROJECT_ROOT"
        start_detached "$HOST_SEARXNG_PID_FILE" "$HOST_SEARXNG_LOG" \
            env SEARXNG_SETTINGS_PATH="$HOST_SEARXNG_SETTINGS" "$HOST_SEARXNG_BIN"
    ) || return 1
    wait_for_searxng_result || {
        warn "Host-local SearXNG failed to become ready. Inspect: $HOST_SEARXNG_LOG"
        return 1
    }
}

opensearch_archive_url() {
    local arch platform
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) platform="linux-x64" ;;
        aarch64|arm64) platform="linux-arm64" ;;
        *) fail "Unsupported OpenSearch host architecture: $arch" ;;
    esac
    printf "https://artifacts.opensearch.org/releases/bundle/opensearch/%s/opensearch-%s-%s.tar.gz" \
        "$HOST_OPENSEARCH_VERSION" "$HOST_OPENSEARCH_VERSION" "$platform"
}

ensure_host_opensearch_installed() {
    if [ -x "$HOST_OPENSEARCH_HOME/bin/opensearch" ]; then
        return 0
    fi
    if is_falsey "${FULL_DEEPSEARCH_DOWNLOAD_HOST_DEPS:-true}"; then
        warn "Host-local OpenSearch is missing and FULL_DEEPSEARCH_DOWNLOAD_HOST_DEPS=false."
        return 1
    fi

    require_command curl "curl is required to download OpenSearch."
    require_command tar "tar is required to unpack OpenSearch."

    local downloads archive url
    downloads="$HOST_OPENSEARCH_ROOT/downloads"
    archive="$downloads/opensearch-$HOST_OPENSEARCH_VERSION.tar.gz"
    url="$(opensearch_archive_url)"
    mkdir -p "$downloads" "$HOST_OPENSEARCH_ROOT"
    if [ ! -f "$archive" ]; then
        log "Downloading OpenSearch $HOST_OPENSEARCH_VERSION from $url"
        curl -fL --retry 3 --connect-timeout 20 -o "$archive.part" "$url" || return 1
        mv "$archive.part" "$archive"
    else
        note "Using existing OpenSearch archive: $archive"
    fi
    log "Extracting OpenSearch to $HOST_OPENSEARCH_ROOT"
    tar -xzf "$archive" -C "$HOST_OPENSEARCH_ROOT" || return 1
    [ -x "$HOST_OPENSEARCH_HOME/bin/opensearch" ]
}

ensure_host_opensearch_user() {
    [ "$(id -u)" = "0" ] || return 0
    if ! id "$HOST_OPENSEARCH_USER" >/dev/null 2>&1; then
        require_command useradd "useradd is required to create the OpenSearch runtime user."
        log "Creating OpenSearch runtime user: $HOST_OPENSEARCH_USER"
        useradd --system --home "$HOST_OPENSEARCH_ROOT" --shell /usr/sbin/nologin \
            "$HOST_OPENSEARCH_USER"
    fi
    chown -R "$HOST_OPENSEARCH_USER":"$HOST_OPENSEARCH_USER" \
        "$HOST_OPENSEARCH_HOME" "$HOST_OPENSEARCH_DATA"
}

write_host_opensearch_config() {
    local opensearch_port
    opensearch_port="$(url_port "${OPENSEARCH_BASE_URL:-http://127.0.0.1:9200}" "9200")"
    mkdir -p "$HOST_OPENSEARCH_HOME/config" "$HOST_OPENSEARCH_DATA/data" "$HOST_OPENSEARCH_DATA/logs"
    cat > "$HOST_OPENSEARCH_HOME/config/opensearch.yml" <<EOF
cluster.name: deepsearch-host-local
node.name: deepsearch-host-local-1
discovery.type: single-node
network.host: 127.0.0.1
http.port: $opensearch_port
path.data: $HOST_OPENSEARCH_DATA/data
path.logs: $HOST_OPENSEARCH_DATA/logs
plugins.security.disabled: true
EOF
}

start_host_opensearch() {
    if opensearch_is_reachable; then
        log "Host-local OpenSearch is already reachable at $OPENSEARCH_BASE_URL."
        return 0
    fi
    ensure_host_opensearch_installed || return 1
    write_host_opensearch_config
    ensure_host_opensearch_user || return 1

    export OPENSEARCH_USERNAME=""
    export OPENSEARCH_PASSWORD=""
    export OPENSEARCH_VERIFY_TLS=false
    export OPENSEARCH_CA_BUNDLE_PATH=""

    log "Starting host-local OpenSearch from $HOST_OPENSEARCH_HOME."
    if [ "$(id -u)" = "0" ]; then
        require_command runuser "runuser is required to start OpenSearch as a non-root user."
        start_detached "$HOST_OPENSEARCH_PID_FILE" "$HOST_OPENSEARCH_LOG" \
            runuser -u "$HOST_OPENSEARCH_USER" -- \
            env "OPENSEARCH_JAVA_OPTS=-Xms$HOST_OPENSEARCH_HEAP -Xmx$HOST_OPENSEARCH_HEAP" \
            "$HOST_OPENSEARCH_HOME/bin/opensearch" || return 1
    else
        start_detached "$HOST_OPENSEARCH_PID_FILE" "$HOST_OPENSEARCH_LOG" \
            env "OPENSEARCH_JAVA_OPTS=-Xms$HOST_OPENSEARCH_HEAP -Xmx$HOST_OPENSEARCH_HEAP" \
            "$HOST_OPENSEARCH_HOME/bin/opensearch" || return 1
    fi
    wait_for_opensearch_result || {
        warn "Host-local OpenSearch failed to become ready. Inspect: $HOST_OPENSEARCH_LOG"
        return 1
    }
    local stability_seconds
    stability_seconds="${FULL_DEEPSEARCH_OPENSEARCH_STABILITY_SECONDS:-8}"
    if [[ "$stability_seconds" =~ ^[0-9]+$ ]] && [ "$stability_seconds" -gt 0 ]; then
        log "Confirming OpenSearch stays reachable for ${stability_seconds}s."
        sleep "$stability_seconds"
        if ! opensearch_is_reachable; then
            warn "Host-local OpenSearch became ready but did not stay reachable. Inspect: $HOST_OPENSEARCH_LOG"
            return 1
        fi
    fi
}

fallback_to_local_index() {
    if is_truthy "${FULL_DEEPSEARCH_ALLOW_LOCAL_INDEX_FALLBACK:-true}"; then
        warn "Falling back to INDEX_BACKEND=local so the UI, real SearXNG, planner, worker, and LLM report path can still run."
        export INDEX_BACKEND=local
        return 0
    fi
    return 1
}

start_host_dependencies() {
    start_host_searxng || return 1
    if start_host_opensearch; then
        export INDEX_BACKEND=opensearch
        return 0
    fi
    fallback_to_local_index
}

start_dependencies() {
    if is_truthy "${FULL_DEEPSEARCH_SKIP_DEPS:-false}"; then
        note "Skipping dependency startup because FULL_DEEPSEARCH_SKIP_DEPS=true."
        return 0
    fi

    case "${FULL_DEEPSEARCH_DEPS_MODE:-auto}" in
        host)
            start_host_dependencies || fail "Host-local dependency startup failed."
            ;;
        docker)
            start_docker_dependencies
            ;;
        auto)
            if start_host_dependencies; then
                return 0
            fi
            warn "Host-local dependency startup did not complete; trying Docker dependency path."
            start_docker_dependencies
            ;;
        none)
            note "Skipping dependency startup because FULL_DEEPSEARCH_DEPS_MODE=none."
            ;;
        *)
            fail "Unsupported FULL_DEEPSEARCH_DEPS_MODE=${FULL_DEEPSEARCH_DEPS_MODE:-auto}; use auto, host, docker, or none."
            ;;
    esac
}

start_deepsearch() {
    log "Starting DeepSearch backend, worker, and frontend through dev.sh."
    (
        cd "$PROJECT_ROOT"
        DEV_ENV_FILE="$ENV_FILE" \
        DEV_BACKEND_RELOAD="${DEV_BACKEND_RELOAD:-false}" \
        ./dev.sh start
    )
}

stop_deepsearch() {
    (
        cd "$PROJECT_ROOT"
        DEV_ENV_FILE="$ENV_FILE" ./dev.sh stop
    )
    if is_truthy "${FULL_DEEPSEARCH_STOP_DEPS:-false}"; then
        log "Stopping host-local dependencies started by this helper when PID files exist."
        stop_pid_file "$HOST_SEARXNG_PID_FILE" "host-local SearXNG"
        stop_pid_file "$HOST_OPENSEARCH_PID_FILE" "host-local OpenSearch"
        if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
            derive_compose_ports
            log "Stopping Docker dependencies."
            (
                cd "$PROJECT_ROOT"
                compose_cmd \
                    -f docker-compose.yml \
                    -f docker-compose.dev.yml \
                    --profile search \
                    stop opensearch searxng
            )
        fi
    fi
}

stop_pid_file() {
    local pid_file="$1"
    local name="$2"
    local pid
    [ -f "$pid_file" ] || return 0
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 0
    if kill -0 "$pid" >/dev/null 2>&1; then
        log "Stopping $name pid=$pid"
        kill -- "-$pid" >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
        local attempt
        for attempt in $(seq 1 20); do
            if ! kill -0 "$pid" >/dev/null 2>&1; then
                rm -f "$pid_file" "$pid_file.command"
                return 0
            fi
            sleep 0.5
        done
        warn "$name did not stop after SIGTERM; sending SIGKILL."
        kill -KILL -- "-$pid" >/dev/null 2>&1 || kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pid_file" "$pid_file.command"
}

status_deepsearch() {
    load_env_file "$BASE_ENV_FILE" false true
    load_env_file "$ENV_FILE" true "${FULL_DEEPSEARCH_PRESERVE_SHELL_ENV:-false}"
    derive_compose_ports
    (
        cd "$PROJECT_ROOT"
        DEV_ENV_FILE="$ENV_FILE" ./dev.sh status
    )
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        (
            cd "$PROJECT_ROOT"
            compose_cmd \
                -f docker-compose.yml \
                -f docker-compose.dev.yml \
                --profile search \
                ps opensearch searxng
        )
    fi
    echo ""
    echo "Host-local dependency probes:"
    if searxng_is_reachable; then
        echo "  searxng:    reachable at $SEARXNG_BASE_URL"
    else
        echo "  searxng:    not reachable at $SEARXNG_BASE_URL"
    fi
    if opensearch_is_reachable; then
        echo "  opensearch: reachable at $OPENSEARCH_BASE_URL"
    else
        echo "  opensearch: not reachable at $OPENSEARCH_BASE_URL"
    fi
}

doctor_deepsearch() {
    write_default_env_file
    load_env_file "$BASE_ENV_FILE" false true
    load_env_file "$ENV_FILE" true "${FULL_DEEPSEARCH_PRESERVE_SHELL_ENV:-false}"
    ensure_full_llm_config
    derive_compose_ports
    echo ""
    echo "Full DeepSearch profile:"
    echo "  env file:        $ENV_FILE"
    echo "  deps mode:       ${FULL_DEEPSEARCH_DEPS_MODE:-auto}"
    echo "  search:          ${SEARCH_PROVIDER:-} at ${SEARXNG_BASE_URL:-}"
    echo "  index:           ${INDEX_BACKEND:-} at ${OPENSEARCH_BASE_URL:-}"
    echo "  LLM provider:    ${LLM_PROVIDER:-}"
    echo "  LLM model:       ${LLM_MODEL:-}"
    echo "  planner:         ${RESEARCH_PLANNER_ENABLED:-false}"
    echo "  report writer:   ${LLM_REPORT_WRITER_ENABLED:-false}"
    echo "  claim reviewer:  ${LLM_CLAIM_REVIEWER_ENABLED:-true}"
    echo "  frontend URL:    http://127.0.0.1:${DEV_FRONTEND_PORT:-5173}"
    echo "  backend URL:     http://127.0.0.1:${DEV_BACKEND_PORT:-8000}"
    echo "  host SearXNG:    $([ -x "$HOST_SEARXNG_BIN" ] && printf "installed" || printf "missing") / $([ -f "$HOST_SEARXNG_SETTINGS" ] && printf "configured" || printf "no-settings")"
    echo "  host OpenSearch: $([ -x "$HOST_OPENSEARCH_HOME/bin/opensearch" ] && printf "installed" || printf "missing")"
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        echo "  Docker daemon:   reachable (optional)"
    else
        echo "  Docker daemon:   not reachable (optional)"
    fi
    echo ""
    log "Doctor completed. No API keys were printed."
}

start_command() {
    write_default_env_file
    load_env_file "$BASE_ENV_FILE" false true
    load_env_file "$ENV_FILE" true "${FULL_DEEPSEARCH_PRESERVE_SHELL_ENV:-false}"
    ensure_full_llm_config
    start_dependencies
    start_deepsearch
    echo ""
    echo "DeepSearch is starting with expected mode:"
    if [ "${INDEX_BACKEND:-opensearch}" = "local" ]; then
        echo "  real-search+deterministic-local+planner+report-LLM"
    else
        echo "  real-search+opensearch+planner+report-LLM"
    fi
    echo ""
    echo "Open:"
    echo "  frontend: http://127.0.0.1:${DEV_FRONTEND_PORT:-5173}"
    echo "  backend:  http://127.0.0.1:${DEV_BACKEND_PORT:-8000}"
}

show_help() {
    cat <<'EOF'
Usage: ./scripts/run_full_deepsearch.sh [COMMAND]

Commands:
  start|restart   Create/load the full env profile, start dependencies, then start backend, worker, and frontend.
  stop            Stop DeepSearch processes. Set FULL_DEEPSEARCH_STOP_DEPS=true to also stop managed dependencies.
  status          Show dev.sh status plus host/Docker dependency status.
  doctor          Validate the full profile without starting services.
  help            Show this help.

First-run one-liner when .env.deepseek.local does not exist:
  LLM_API_KEY=<your-deepseek-key> ./scripts/run_full_deepsearch.sh restart

Useful controls:
  FULL_DEEPSEARCH_ENV_FILE=/path/.env.deepseek.local
  FULL_DEEPSEARCH_DEPS_MODE=auto       Dependency mode: auto, host, docker, or none.
  FULL_DEEPSEARCH_SKIP_DEPS=true       Use already-running OpenSearch/SearXNG.
  FULL_DEEPSEARCH_DOWNLOAD_HOST_DEPS=true
                                      Download host-local OpenSearch tarball if missing.
  FULL_DEEPSEARCH_ALLOW_LOCAL_INDEX_FALLBACK=true
                                      If OpenSearch cannot start, keep real SearXNG and LLM with local index.
  FULL_DEEPSEARCH_PRESERVE_SHELL_ENV=true
                                      Let pre-exported shell values override the full env file.
  FULL_DEEPSEARCH_WAIT_SECONDS=240     Increase dependency wait timeout.
  FULL_DEEPSEARCH_OPENSEARCH_STABILITY_SECONDS=8
                                      Re-probe OpenSearch after initial readiness.
  DEV_SKIP_FRONTEND=true               Start backend and worker only.
  DEV_BACKEND_RELOAD=false             Default for this script.
EOF
}

main() {
    local command="${1:-restart}"
    shift || true
    capture_original_env

    case "$command" in
        start|restart)
            start_command
            ;;
        stop)
            load_env_file "$BASE_ENV_FILE" false true
            load_env_file "$ENV_FILE" true "${FULL_DEEPSEARCH_PRESERVE_SHELL_ENV:-false}"
            stop_deepsearch
            ;;
        status)
            status_deepsearch
            ;;
        doctor)
            doctor_deepsearch
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
