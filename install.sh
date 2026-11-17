#!/usr/bin/env bash
# Interactive Ubuntu 24.04/26.04 ARM64 installation. No local game or FEX compilation.
set -Eeuo pipefail
set +x

SOURCE_URL="${VALHEIM_SOURCE_URL:-https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main}"
INSTALL_DIR="${VALHEIM_INSTALL_DIR:-$PWD/valheim-arm}"
ROOT=()
DOCKER=(docker)
DOCKER_SUITE=''

die() { printf '\nError: %s\n' "$*" >&2; exit 1; }
log() { printf '\n%s\n' "$*"; }

valid_ipv4() {
    local ip="$1" part
    local -a parts
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    IFS=. read -r -a parts <<< "$ip"
    for part in "${parts[@]}"; do
        (( 10#$part <= 255 )) || return 1
    done
    [[ "$ip" != 0.0.0.0 && "$ip" != 127.* ]]
}

env_value() {
    # Quote for Compose dotenv, escaping interpolation without evaluating input.
    local value="${2//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\$/\$\$}"
    printf '%s="%s"\n' "$1" "$value"
}

ubuntu_suite() {
    case "$1:$2" in
        ubuntu:24.04) printf 'noble\n' ;;
        ubuntu:26.04) printf 'resolute\n' ;;
        *) die 'Automatic installation supports Ubuntu 24.04 and 26.04 LTS.' ;;
    esac
}

prepare_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        log 'Installing Docker and Docker Compose from the official repository…'
        local package
        for package in docker.io podman-docker containerd runc; do
            if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
                die "$package is already installed. Install Docker Compose using https://docs.docker.com/engine/install/ubuntu/ and run the installer again."
            fi
        done
        "${ROOT[@]}" apt-get update
        "${ROOT[@]}" apt-get install -y ca-certificates curl
        "${ROOT[@]}" install -m 0755 -d /etc/apt/keyrings
        "${ROOT[@]}" curl --fail --silent --show-error --location --retry 3 \
            https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        "${ROOT[@]}" chmod a+r /etc/apt/keyrings/docker.asc
        printf '%s\n' 'Types: deb' 'URIs: https://download.docker.com/linux/ubuntu' \
            "Suites: $DOCKER_SUITE" 'Components: stable' 'Architectures: arm64' \
            'Signed-By: /etc/apt/keyrings/docker.asc' |
            "${ROOT[@]}" tee /etc/apt/sources.list.d/docker.sources >/dev/null
        "${ROOT[@]}" apt-get update
        "${ROOT[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
        "${ROOT[@]}" systemctl enable --now docker
    fi
    if ! docker info >/dev/null 2>&1; then
        DOCKER=("${ROOT[@]}" docker)
    fi
    "${DOCKER[@]}" info >/dev/null || die 'Could not access Docker.'
    if ! "${DOCKER[@]}" compose version >/dev/null 2>&1; then
        "${ROOT[@]}" apt-get update
        "${ROOT[@]}" apt-get install -y docker-compose-plugin
    fi
    "${DOCKER[@]}" compose version >/dev/null || die 'Docker Compose unavailable.'
}

prepare_firewall() {
    local status
    if ! command -v ufw >/dev/null 2>&1; then
        log 'UFW is not installed. Check host and provider firewalls; Docker will publish the ports.'
        return
    fi
    status="$(LC_ALL=C "${ROOT[@]}" ufw status)" || die 'Could not query UFW.'
    if [[ "$status" == *'Status: active'* ]]; then
        log 'Allowing UDP 2456–2457 and TCP 443 in active UFW…'
        "${ROOT[@]}" ufw allow 2456:2457/udp
        "${ROOT[@]}" ufw allow 443/tcp
    else
        log 'UFW inactive; no host rules changed.'
    fi
}

fetch_if_missing() {
    local remote="$1" target="$2" temporary
    [[ ! -e "$target" ]] || return 0
    temporary="$(mktemp "${target}.download.XXXXXX")"
    if ! curl --fail --silent --show-error --location --retry 3 "$SOURCE_URL/$remote" -o "$temporary"; then
        rm -f -- "$temporary"
        die "Could not download $remote. Try again."
    fi
    mv -- "$temporary" "$target"
}


prepare_update_agent() {
    local stage script failed=0
    command -v python3 >/dev/null 2>&1 || "${ROOT[@]}" apt-get install -y python3
    log 'Preparing image updates from the panel…'
    stage="$(mktemp -d)"
    for script in host-agent.py update-server.py; do
        if ! curl --fail --silent --show-error --location --retry 3 \
            "$SOURCE_URL/$script" -o "$stage/$script"; then
            failed=1
            break
        fi
    done
    if (( failed == 0 )); then
        if ! "${ROOT[@]}" python3 "$stage/host-agent.py" install --directory "$INSTALL_DIR"; then
            failed=1
        fi
    fi
    rm -f -- "$stage/host-agent.py" "$stage/update-server.py"
    rmdir -- "$stage"
    (( failed == 0 )) || die 'Could not prepare the updater service. Fix the error above and run the installer again.'
    # The agent protects Compose sources and .env with root-only permissions.
    DOCKER=("${ROOT[@]}" docker)
}

create_config() {
    local detected ip name world password temporary
    detected="$(curl -4 --fail --silent --show-error --connect-timeout 5 --max-time 8 https://api.ipify.org 2>/dev/null || true)"
    valid_ipv4 "$detected" || detected=''
    while true; do
        read -r -p "Public server IP${detected:+ [$detected]}: " ip || die "Input closed before an IP address was provided."
        ip="${ip:-$detected}"
        valid_ipv4 "$ip" && break
        printf 'Enter a valid IPv4 address.\n'
    done
    read -r -p 'Server name [My Valheim]: ' name
    name="${name:-My Valheim}"
    [[ ${#name} -le 64 && ! "$name" =~ [[:cntrl:]] ]] || die 'Invalid name: maximum 64 characters.'
    while true; do
        IFS= read -r -p 'World name [Dedicated]: ' world || die 'Input closed before a world name was provided.'
        world="${world:-Dedicated}"
        if [[ ${#world} -le 64 && "$world" =~ ^[[:alnum:]_\ -]+$ && "$world" != ' '* && "$world" != *' ' ]]; then
            break
        fi
        printf 'Use up to 64 characters: letters, numbers, internal spaces, hyphens, or underscores.\n'
    done
    while true; do
        read -r -s -p 'Game password (5–128 characters): ' password || die 'Input closed before a password was provided.'
        printf '\n'
        if [[ ${#password} -ge 5 && ${#password} -le 128 && ! "$password" =~ [[:cntrl:]] ]]; then
            break
        fi
        printf 'Use 5 to 128 characters without control characters.\n'
    done
    temporary="$(mktemp .env.new.XXXXXX)"
    {
        env_value SERVER_IP "$ip"
        env_value SERVER_NAME "$name"
        env_value WORLD_NAME "$world"
        env_value SERVER_PASSWORD "$password"
    } > "$temporary"
    unset password
    chmod 600 "$temporary"
    mv -- "$temporary" .env
    SERVER_ADDRESS="$ip"
}

check_platform() {
    [[ "$(uname -s)" == Linux && "$(uname -m)" == aarch64 ]] || die 'Use an Ubuntu 24.04 or 26.04 LTS ARM64 host.'
    # shellcheck source=/dev/null
    . /etc/os-release
    DOCKER_SUITE="$(ubuntu_suite "${ID:-}" "${VERSION_ID:-}")"
    [[ "$(getconf PAGESIZE)" == 4096 ]] || die 'This image requires 4 KiB memory pages.'
}

main() {
    check_platform
    if (( EUID != 0 )); then
        command -v sudo >/dev/null || die 'sudo is required to prepare the server.'
        ROOT=(sudo)
        sudo -v
    fi
    prepare_docker
    prepare_firewall
    command -v curl >/dev/null || "${ROOT[@]}" apt-get install -y curl
    umask 077
    mkdir -p -- "$INSTALL_DIR"
    # Allow the SSH user to enter an installation created by sudo. Keep files
    # private via umask 077; data has its own ownership and mode below.
    chmod 0755 -- "$INSTALL_DIR"
    cd -- "$INSTALL_DIR"
    INSTALL_DIR="$PWD"
    log "Preparing Valheim in $INSTALL_DIR"
    fetch_if_missing compose.yaml compose.yaml
    fetch_if_missing update-server.py update-server.py
    mkdir -p scripts
    chmod 0755 scripts
    fetch_if_missing scripts/collect-diagnostics.py scripts/collect-diagnostics.py
    chmod 0644 scripts/collect-diagnostics.py
    SERVER_ADDRESS='IP configured in .env'
    if [[ -f .env ]]; then
        log 'Existing configuration preserved.'
    else
        create_config
    fi
    "${ROOT[@]}" install -d -o 10001 -g 10001 -m 0700 data
    prepare_update_agent
    "${DOCKER[@]}" compose config --quiet
    log 'Downloading the public image and preparing data access…'
    local configured_image
    configured_image="$("${DOCKER[@]}" compose config --images | head -n 1)"
    if [[ "$configured_image" == valheim-arm-local:* ]]; then
        "${DOCKER[@]}" image inspect "$configured_image" >/dev/null
    else
        "${DOCKER[@]}" compose pull valheim
    fi
    local access_uid="${SUDO_UID:-$(id -u)}"
    if [[ "$access_uid" =~ ^[0-9]+$ ]] && (( access_uid > 0 )); then
        "${DOCKER[@]}" compose run --rm --no-deps --user 0:0 \
            --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE valheim \
            prepare-access --access-uid "$access_uid"
    else
        log 'Running as root: use sudo from your SSH account to prepare direct access to shared data.'
    fi
    # Re-running the installer must not replace or restart a running world.
    if [[ -n "$("${DOCKER[@]}" compose ps --status running --quiet valheim)" ]]; then
        log 'The server is already running. Its files and game process were preserved.'
        log 'If the panel cannot access the updater yet, apply the integration when you can interrupt the game:'
        printf 'cd %q && sudo docker compose up -d --no-deps valheim\n' "$INSTALL_DIR"
        return
    fi
    "${DOCKER[@]}" compose run --rm --no-deps valheim preflight
    if ! "${ROOT[@]}" test -f data/config/admin.json; then
        log 'Set the panel password now (different from the game password).'
        "${DOCKER[@]}" compose run --rm --no-deps valheim set-password
    fi
    "${DOCKER[@]}" compose up -d --no-build
    log 'Server started! The first game download may take a few minutes.'
    printf 'Installation: %s\nGame: %s:2456\nPanel: https://%s\n' "$INSTALL_DIR" "$SERVER_ADDRESS" "$SERVER_ADDRESS"
    printf 'Follow progress: cd %q && sudo docker compose logs -f valheim\n' "$INSTALL_DIR"
    printf 'Allow UDP 2456–2457 for the game and TCP 443 for the panel in your host and provider firewalls.\n'
    printf 'The panel uses a local certificate; see Panel access in the README.\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
