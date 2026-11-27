#!/bin/bash
# Gera a ISO do OneMapas OS (Debian 12 + live-build, como root).
#   sudo ./os/build-iso.sh beta 0.1.0
# Opcional: ONEMAPAS_PROGRESSO=/caminho/script  -> chamado a cada etapa com: <status> <progresso%>
set -euo pipefail
CANAL=${1:-beta}; VERSAO=${2:-0.1.0}
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
TRAB=${TRAB:-/tmp/onemapas-build}
SAIDA="$RAIZ/out"
NOME="onemapas-os-${VERSAO}-${CANAL}-amd64.iso"

progresso() {
  echo "==> [$2%] $1"
  if [ -n "${ONEMAPAS_PROGRESSO:-}" ]; then "$ONEMAPAS_PROGRESSO" "$1" "$2" || true; fi
}

progresso "preparando" 72
rm -rf "$TRAB"; mkdir -p "$TRAB" "$SAIDA"
cp -a "$RAIZ/os/live-build/." "$TRAB/"
INC="$TRAB/config/includes.chroot"
# programa do PC (servidor dos sites + túnel até o Render), interface e drivers de rede
mkdir -p "$INC/usr/share/onemapas/os" "$INC/usr/local/sbin" \
         "$INC/etc/systemd/system" "$INC/etc/modprobe.d" "$INC/etc/NetworkManager/conf.d"
cp -a "$RAIZ/os/pc" "$RAIZ/os/webui" "$INC/usr/share/onemapas/os/"
cp "$RAIZ/os/network/onemapas-net" "$INC/usr/local/sbin/"
cp "$RAIZ/os/network/onemapas-net.service" "$INC/etc/systemd/system/"
cp "$RAIZ/os/network/onemapas-net.conf" "$INC/etc/modprobe.d/"
cp "$RAIZ/os/network/NetworkManager.conf" "$INC/etc/NetworkManager/conf.d/onemapas.conf"
printf '{"versao":"%s","canal":"%s"}\n' "$VERSAO" "$CANAL" > "$INC/usr/share/onemapas/versao.json"
chmod +x "$INC/usr/local/sbin/onemapas-net" "$INC/usr/local/bin/onemapas-sessao"

cd "$TRAB"
lb clean --purge || true
lb config

progresso "construindo: sistema base Debian 12" 75
lb bootstrap

progresso "construindo: instalando pacotes, interface e drivers de rede" 82
lb chroot

progresso "construindo: gerando a imagem ISO" 92
lb binary

ISO=$(ls -1 "$TRAB"/*.iso | head -1)
mv "$ISO" "$SAIDA/$NOME"
( cd "$SAIDA" && sha256sum "$NOME" > "$NOME.sha256" )
progresso "publicando" 97
echo "ISO pronta: $SAIDA/$NOME ($(du -h "$SAIDA/$NOME" | cut -f1))"
