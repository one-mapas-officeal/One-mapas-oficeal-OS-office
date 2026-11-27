#!/bin/bash
# Gera a ISO do OneMapas OS (precisa de Debian/Ubuntu com root e o pacote live-build).
#   sudo ./os/build-iso.sh beta 0.1.0
set -euo pipefail
CANAL=${1:-beta}; VERSAO=${2:-0.1.0}
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
TRAB=${TRAB:-/tmp/onemapas-build}
SAIDA="$RAIZ/out"
NOME="onemapas-os-${VERSAO}-${CANAL}-amd64.iso"

rm -rf "$TRAB"; mkdir -p "$TRAB" "$SAIDA"
cp -a "$RAIZ/os/live-build/." "$TRAB/"
INC="$TRAB/config/includes.chroot"
# copia o sistema OneMapas para dentro da ISO
mkdir -p "$INC/usr/share/onemapas/os" "$INC/usr/share/onemapas/ISO" "$INC/usr/local/sbin" \
         "$INC/etc/systemd/system" "$INC/etc/modprobe.d" "$INC/etc/NetworkManager/conf.d"
cp -a "$RAIZ/os/backend" "$RAIZ/os/webui" "$INC/usr/share/onemapas/os/"
cp "$RAIZ/ISO/manifest.json" "$INC/usr/share/onemapas/ISO/"
cp "$RAIZ/os/network/onemapas-net" "$INC/usr/local/sbin/"
cp "$RAIZ/os/network/onemapas-net.service" "$INC/etc/systemd/system/"
cp "$RAIZ/os/network/onemapas-net.conf" "$INC/etc/modprobe.d/"
cp "$RAIZ/os/network/NetworkManager.conf" "$INC/etc/NetworkManager/conf.d/onemapas.conf"
printf '{"versao":"%s","canal":"%s"}\n' "$VERSAO" "$CANAL" > "$INC/usr/share/onemapas/versao.json"

cd "$TRAB"
lb clean --purge || true
lb config
lb build
ISO=$(ls -1 "$TRAB"/*.iso | head -1)
mv "$ISO" "$SAIDA/$NOME"
( cd "$SAIDA" && sha256sum "$NOME" > "$NOME.sha256" )
echo "ISO pronta: $SAIDA/$NOME"
