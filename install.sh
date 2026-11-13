#!/usr/bin/env bash
# Instalação interativa para Ubuntu 24.04/26.04 ARM64. Não compila o jogo nem o FEX.
set -Eeuo pipefail
set +x

SOURCE_URL="${VALHEIM_SOURCE_URL:-https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main}"
INSTALL_DIR="${VALHEIM_INSTALL_DIR:-$PWD/valheim-arm}"
ROOT=()
DOCKER=(docker)
DOCKER_SUITE=''

die() { printf '\nErro: %s\n' "$*" >&2; exit 1; }
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
        *) die 'A instalação automática suporta Ubuntu 24.04 e 26.04 LTS.' ;;
    esac
}

prepare_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        log 'Instalando Docker e Docker Compose pelo repositório oficial…'
        local package
        for package in docker.io podman-docker containerd runc; do
            if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
                die "Já existe $package. Instale Docker Compose conforme https://docs.docker.com/engine/install/ubuntu/ e execute novamente."
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
    "${DOCKER[@]}" info >/dev/null || die 'Não foi possível acessar o Docker.'
    if ! "${DOCKER[@]}" compose version >/dev/null 2>&1; then
        "${ROOT[@]}" apt-get update
        "${ROOT[@]}" apt-get install -y docker-compose-plugin
    fi
    "${DOCKER[@]}" compose version >/dev/null || die 'Docker Compose indisponível.'
}

fetch_if_missing() {
    local remote="$1" target="$2" temporary
    [[ ! -e "$target" ]] || return 0
    temporary="$(mktemp "${target}.download.XXXXXX")"
    if ! curl --fail --silent --show-error --location --retry 3 "$SOURCE_URL/$remote" -o "$temporary"; then
        rm -f -- "$temporary"
        die "Não foi possível baixar $remote. Tente novamente."
    fi
    mv -- "$temporary" "$target"
}

create_config() {
    local detected ip name password temporary
    detected="$(curl -4 --fail --silent --show-error --connect-timeout 5 --max-time 8 https://api.ipify.org 2>/dev/null || true)"
    valid_ipv4 "$detected" || detected=''
    while true; do
        read -r -p "IP público do servidor${detected:+ [$detected]}: " ip
        ip="${ip:-$detected}"
        valid_ipv4 "$ip" && break
        printf 'Informe um IPv4 válido.\n'
    done
    read -r -p 'Nome do servidor [Meu Valheim]: ' name
    name="${name:-Meu Valheim}"
    [[ ${#name} -le 64 && ! "$name" =~ [[:cntrl:]] ]] || die 'Nome inválido: máximo 64 caracteres.'
    while true; do
        read -r -s -p 'Senha para entrar no jogo (5–128 caracteres): ' password
        printf '\n'
        if [[ ${#password} -ge 5 && ${#password} -le 128 && ! "$password" =~ [[:cntrl:]] ]]; then
            break
        fi
        printf 'Use de 5 a 128 caracteres, sem caracteres de controle.\n'
    done
    temporary="$(mktemp .env.new.XXXXXX)"
    {
        env_value SERVER_IP "$ip"
        env_value SERVER_NAME "$name"
        env_value SERVER_PASSWORD "$password"
    } > "$temporary"
    unset password
    chmod 600 "$temporary"
    mv -- "$temporary" .env
    SERVER_ADDRESS="$ip"
}

check_platform() {
    [[ "$(uname -s)" == Linux && "$(uname -m)" == aarch64 ]] || die 'Use uma instância Ubuntu 24.04 ou 26.04 LTS ARM64.'
    # shellcheck source=/dev/null
    . /etc/os-release
    DOCKER_SUITE="$(ubuntu_suite "${ID:-}" "${VERSION_ID:-}")"
    [[ "$(getconf PAGESIZE)" == 4096 ]] || die 'Esta imagem exige páginas de memória de 4 KiB.'
}

main() {
    check_platform
    if (( EUID != 0 )); then
        command -v sudo >/dev/null || die 'É necessário sudo para preparar o servidor.'
        ROOT=(sudo)
        sudo -v
    fi
    prepare_docker
    command -v curl >/dev/null || "${ROOT[@]}" apt-get install -y curl
    umask 077
    mkdir -p -- "$INSTALL_DIR/docker"
    # Allow the SSH user to enter an installation created by sudo. Keep files
    # private via umask 077; data has its own ownership and mode below.
    chmod 0755 -- "$INSTALL_DIR" "$INSTALL_DIR/docker"
    cd -- "$INSTALL_DIR"
    INSTALL_DIR="$PWD"
    log "Preparando Valheim em $INSTALL_DIR"
    fetch_if_missing compose.yaml compose.yaml
    fetch_if_missing docker/Caddyfile docker/Caddyfile
    SERVER_ADDRESS='IP configurado no .env'
    if [[ -f .env ]]; then
        log 'Configuração existente preservada.'
    else
        create_config
    fi
    "${ROOT[@]}" install -d -o 10001 -g 10001 -m 0700 data
    "${DOCKER[@]}" compose config --quiet
    # Re-running the installer must not replace or restart a running world.
    if [[ -n "$("${DOCKER[@]}" compose ps --status running --quiet valheim)" ]]; then
        log 'O servidor já está em execução. Seus arquivos e o jogo foram preservados.'
        return
    fi
    log 'Baixando as imagens públicas e verificando a plataforma…'
    "${DOCKER[@]}" compose pull
    "${DOCKER[@]}" compose run --rm --no-deps valheim preflight
    if ! "${ROOT[@]}" test -f data/config/admin.json; then
        log 'Defina agora a senha do painel (diferente da senha do jogo).'
        "${DOCKER[@]}" compose run --rm --no-deps valheim set-password
    fi
    "${DOCKER[@]}" compose up -d --no-build
    log 'Servidor iniciado! O primeiro download do jogo pode levar alguns minutos.'
    printf 'Instalação: %s\nJogo: %s:2456\nPainel: https://%s\n' "$INSTALL_DIR" "$SERVER_ADDRESS" "$SERVER_ADDRESS"
    printf 'Acompanhe: cd %q && sudo docker compose logs -f valheim\n' "$INSTALL_DIR"
    printf 'Na Oracle, libere UDP 2456–2457 para jogar e TCP 443 para acessar o painel.\n'
    printf 'O painel usa um certificado local: veja a seção Painel no README.\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
