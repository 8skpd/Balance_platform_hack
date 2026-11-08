#!/usr/bin/env bash
# Official apt repository: https://docs.docker.com/engine/install/ubuntu/
# Debian: https://docs.docker.com/engine/install/debian/
set -euo pipefail
if (( EUID != 0 )); then echo 'Запустите: sudo ./scripts/install-docker.sh' >&2; exit 1; fi
source /etc/os-release
case "$ID" in ubuntu|debian) docker_distro="$ID";; *) echo 'Этот установщик поддерживает Ubuntu и Debian. Для другой ОС: https://docs.docker.com/engine/install/' >&2; exit 1;; esac
docker_suite="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
docker_arch="$(dpkg --print-architecture)"
case "$docker_arch" in amd64|arm64) ;; *) echo 'Приложение поддерживает Linux x86_64 и arm64.' >&2; exit 1;; esac
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo 'Docker и Compose уже установлены.'
  exit 0
fi
for package in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
  if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" == 'install ok installed' ]]; then
    echo "Обнаружен пакет $package. Разрешите конфликт по инструкции Docker перед установкой; существующие пакеты автоматически не удаляются." >&2
    exit 1
  fi
done
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/$docker_distro/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<REPOSITORY
Types: deb
URIs: https://download.docker.com/linux/$docker_distro
Suites: $docker_suite
Components: stable
Architectures: $docker_arch
Signed-By: /etc/apt/keyrings/docker.asc
REPOSITORY
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker version
docker compose version
printf '%s\n' 'Готово. Запуск приложения: sudo ./scripts/docker.sh up'
