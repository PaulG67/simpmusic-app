#!/bin/bash
# Installiert die Unraid-Benutzervorlage, damit SimpMusic-App unter
# Docker → Container hinzufügen → Template auswählbar ist.
#
# Auf dem Unraid-Terminal:
#   bash /mnt/user/appdata/simpmusic-app/unraid/install-template.sh
#
# Oder, wenn das Repo bereits geklont ist, von überall:
#   bash /pfad/zu/simpmusic-app/unraid/install-template.sh

set -euo pipefail

TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"
USER_XML="${TEMPLATE_DIR}/my-simpmusic-app.xml"
IMAGE="ghcr.io/paulg67/simpmusic-app:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_XML="${SCRIPT_DIR}/my-simpmusic-app.xml"

if [[ ! -f "${SOURCE_XML}" ]]; then
  SOURCE_XML="${SCRIPT_DIR}/simpmusic-app.xml"
fi

if [[ ! -f "${SOURCE_XML}" ]]; then
  echo "Vorlage nicht gefunden neben dem Skript: ${SCRIPT_DIR}"
  echo "Repo zuerst klonen, dann dieses Skript erneut ausführen."
  exit 1
fi

echo "==> 1/3 Unraid-Vorlage"
mkdir -p "${TEMPLATE_DIR}"
cp "${SOURCE_XML}" "${USER_XML}"
echo "    ${USER_XML}"

echo "==> 2/3 Docker-Image"
if docker pull "${IMAGE}" 2>/dev/null; then
  echo "    ${IMAGE}"
else
  echo "    GHCR-Pull nicht möglich (Paket noch privat)."
  echo "    Baue das Image lokal — danach funktioniert «Container hinzufügen»."
  ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
  if [[ ! -f "${ROOT_DIR}/Dockerfile" ]]; then
    echo "    Dockerfile fehlt in ${ROOT_DIR}"
    exit 1
  fi
  docker build -t "${IMAGE}" "${ROOT_DIR}"
  echo "    lokal gebaut und als ${IMAGE} getaggt"
  echo
  echo "    Damit Unraid später «Force Update» kann, das Paket öffentlich machen:"
  echo "    https://github.com/PaulG67/simpmusic-app/pkgs/container/simpmusic-app"
  echo "    → Package settings → Change visibility → Public"
fi

echo "==> 3/3 Fertig"
echo
echo "In Unraid:"
echo "  Docker → Container hinzufügen → Template «simpmusic-app»"
echo "  Passwort setzen → Apply"
echo
echo "WebUI: http://UNRAID-IP:5060"
echo "Updates: Docker → simpmusic-app → Force Update"
