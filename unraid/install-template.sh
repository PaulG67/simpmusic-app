#!/bin/bash
# Installiert die Unraid-Benutzervorlage, damit Music Play unter
# Docker → Container hinzufügen → Template auswählbar ist.
#
# Auf dem Unraid-Terminal:
#   bash /mnt/user/appdata/music-play/unraid/install-template.sh
#
# Oder, wenn das Repo bereits geklont ist, von überall:
#   bash /pfad/zu/music-play/unraid/install-template.sh

set -euo pipefail

TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"
USER_XML="${TEMPLATE_DIR}/my-music-play.xml"
IMAGE="ghcr.io/paulg67/music-play:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_XML="${SCRIPT_DIR}/music-play.xml"

if [[ ! -f "${SOURCE_XML}" ]]; then
  echo "Vorlage nicht gefunden neben dem Skript: ${SCRIPT_DIR}"
  echo "Repo zuerst klonen, dann dieses Skript erneut ausführen."
  exit 1
fi

echo "==> 1/3 Unraid-Vorlage"
mkdir -p "${TEMPLATE_DIR}"
cp "${SOURCE_XML}" "${USER_XML}"
# Alte SimpMusic-Vorlage entfernen, falls vorhanden
rm -f "${TEMPLATE_DIR}/my-simpmusic-app.xml"
echo "    ${USER_XML}"

echo "==> 2/3 Docker-Image"
if docker pull "${IMAGE}" 2>/dev/null; then
  echo "    ${IMAGE}"
else
  echo "    GHCR-Pull nicht möglich — baue das Image lokal."
  ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
  if [[ ! -f "${ROOT_DIR}/Dockerfile" ]]; then
    echo "    Dockerfile fehlt in ${ROOT_DIR}"
    exit 1
  fi
  docker build -t "${IMAGE}" "${ROOT_DIR}"
  echo "    lokal gebaut und als ${IMAGE} getaggt"
fi

echo "==> 3/3 Fertig"
echo
echo "In Unraid:"
echo "  Docker → Container hinzufügen → Template «music-play»"
echo "  Passwort setzen → Apply"
echo
echo "WebUI: http://UNRAID-IP:5060"
echo "Updates: Docker → music-play → Force Update"
