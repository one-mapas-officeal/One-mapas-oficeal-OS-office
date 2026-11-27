#!/bin/bash
# Gera a ISO do OneMapas OS: LINUX PURO, sem distribuição instalada.
#   kernel Linux + drivers e firmware de rede + BusyBox + Python + OneMapas OS, tudo rodando na RAM.
#
#   sudo ./os/build-iso.sh beta 0.2.0
#
# Precisa de um Linux com apt (Ubuntu/Debian) só para BAIXAR o kernel, o BusyBox e o Python já compilados.
# Opcional: ONEMAPAS_PROGRESSO=/caminho/script  -> chamado a cada etapa com: <status> <progresso%>
set -euo pipefail
CANAL=${1:-beta}; VERSAO=${2:-0.2.0}
RAIZ=$(cd "$(dirname "$0")/.." && pwd)
TRAB=${TRAB:-/tmp/onemapas-iso}
SAIDA="$RAIZ/out"
NOME="onemapas-os-${VERSAO}-${CANAL}-amd64.iso"
R="$TRAB/rootfs"
export DEBIAN_FRONTEND=noninteractive

progresso() {
  echo "==> [$2%] $1"
  if [ -n "${ONEMAPAS_PROGRESSO:-}" ]; then "$ONEMAPAS_PROGRESSO" "$1" "$2" || true; fi
}

# ---------------------------------------------------------------- ferramentas
progresso "preparando" 72
FERRAMENTAS="busybox-static grub-pc-bin grub-efi-amd64-bin grub-common xorriso mtools dosfstools kmod
  wpasupplicant iw util-linux kbd console-setup-linux ca-certificates zstd cpio python3"
faltando=""
for p in $FERRAMENTAS; do dpkg -s "$p" >/dev/null 2>&1 || faltando="$faltando $p"; done
apt-get update -qq
[ -n "$faltando" ] && apt-get install -y -qq --no-install-recommends $faltando >/dev/null

rm -rf "$TRAB"; mkdir -p "$TRAB"/{pacotes,kernel,firmware,iso/boot/grub} "$SAIDA"
mkdir -p "$R"/usr/{bin,lib,lib64,share} "$R"/{etc/modprobe.d,etc/ssl/certs,proc,sys,dev,run,tmp,mnt,root,var/log}
ln -s bin "$R/usr/sbin"; ln -s usr/bin "$R/bin"; ln -s usr/bin "$R/sbin"
ln -s usr/lib "$R/lib"; ln -s usr/lib64 "$R/lib64"
chmod 1777 "$R/tmp"

# copia um programa e todas as bibliotecas de que ele precisa
copiar() {
  local f l
  for f in "$@"; do
    f=$(readlink -f "$f")
    cp --update=none --parents "$f" "$R"
    ldd "$f" 2>/dev/null | grep -oE '/[^ ]+' | while read -r l; do cp -L --update=none --parents "$l" "$R" 2>/dev/null || true; done || true
  done
}

# ---------------------------------------------------------------- BusyBox + programas
cp /usr/bin/busybox "$R/usr/bin/busybox"
for a in $(/usr/bin/busybox --list); do [ -e "$R/usr/bin/$a" ] || ln -s busybox "$R/usr/bin/$a"; done
copiar /usr/bin/kmod "$(command -v blkid)" "$(command -v setfont)" \
       "$(command -v wpa_supplicant)" "$(command -v wpa_passphrase)" "$(command -v iw)"
for x in modprobe insmod rmmod lsmod depmod modinfo; do ln -sf kmod "$R/usr/bin/$x"; done
for x in blkid setfont wpa_supplicant wpa_passphrase iw; do
  [ -e "$R/usr/bin/$x" ] || ln -sf "$(readlink -f "$(command -v $x)")" "$R/usr/bin/$x"
done

# ---------------------------------------------------------------- Python (só o necessário)
PY=$(readlink -f /usr/bin/python3)
copiar "$PY"; ln -sf "$(basename "$PY")" "$R/usr/bin/python3"
STD=$(/usr/bin/python3 -c 'import sysconfig; print(sysconfig.get_paths()["stdlib"])')
tar -C / -cf - --exclude='__pycache__' --exclude='test' --exclude='tests' --exclude='idlelib' \
    --exclude='tkinter' --exclude='turtledemo' --exclude='ensurepip' --exclude='lib2to3' \
    --exclude='pydoc_data' --exclude='site-packages' --exclude='dist-packages' --exclude='config-3*' \
    "${STD#/}" | tar -C "$R" -xf -
for so in "$R$STD"/lib-dynload/*.so; do
  ldd "$so" 2>/dev/null | grep -oE '/[^ ]+' | while read -r l; do cp -L --update=none --parents "$l" "$R" 2>/dev/null || true; done || true
done
/usr/bin/python3 -m compileall -q -j0 "$R$STD" >/dev/null || true

# ---------------------------------------------------------------- arquivos do sistema
cp /etc/ssl/certs/ca-certificates.crt "$R/etc/ssl/certs/"
zcat /usr/share/consolefonts/Lat15-Fixed16.psf.gz > "$TRAB/font.psf" 2>/dev/null || true
echo 'root:x:0:0:root:/root:/bin/sh' > "$R/etc/passwd"
echo 'root:x:0:' > "$R/etc/group"
printf '127.0.0.1 localhost onemapas-pc\n' > "$R/etc/hosts"
printf 'hosts: files dns\n' > "$R/etc/nsswitch.conf"
printf 'nameserver 1.1.1.1\n' > "$R/etc/resolv.conf"
printf 'export PATH=/usr/bin\nexport PS1="onemapas:\\w# "\necho "OneMapas OS - terminal. Alt+F1 volta ao painel."\n' > "$R/etc/profile"
cp -a "$RAIZ/os/iso/rootfs/." "$R/"
cp "$RAIZ/os/network/onemapas-net.conf" "$R/etc/modprobe.d/"
mkdir -p "$R/usr/share/onemapas/os" "$R/usr/share/onemapas/ISO"
cp -a "$RAIZ/os/pc" "$RAIZ/os/webui" "$R/usr/share/onemapas/os/"
cp "$RAIZ/ISO/manifest.json" "$R/usr/share/onemapas/ISO/"
[ -s "$TRAB/font.psf" ] && cp "$TRAB/font.psf" "$R/usr/share/onemapas/font.psf"
echo "$VERSAO ($CANAL)" > "$R/usr/share/onemapas/versao"
find "$R" -name __pycache__ -path '*/onemapas/*' -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------- kernel Linux + drivers
progresso "baixando o kernel Linux" 76
cd "$TRAB/pacotes"
if apt-cache show linux-image-generic >/dev/null 2>&1; then META=linux-image-generic; else META=linux-image-amd64; fi
IMG=$(apt-cache depends "$META" | awk '/Depends: linux-image-[0-9]/{print $2; exit}')
KVER=${IMG#linux-image-}
PKGS="$IMG"
for p in "linux-modules-$KVER" "linux-modules-extra-$KVER"; do apt-cache show "$p" >/dev/null 2>&1 && PKGS="$PKGS $p"; done
apt-get download $PKGS >/dev/null 2>&1
for d in *.deb; do dpkg-deb -x "$d" "$TRAB/kernel"; done
cp "$TRAB/kernel/boot/vmlinuz-$KVER" "$TRAB/iso/boot/vmlinuz"
MODS=$(ls -d "$TRAB/kernel/lib/modules/$KVER" "$TRAB/kernel/usr/lib/modules/$KVER" 2>/dev/null | head -1 || true)
echo "kernel $KVER"
depmod -b "${MODS%/lib/modules/*}" "$KVER"   # o pacote não traz o modules.dep

# drivers que entram: rede (cabo, Wi-Fi, USB), discos (SATA, NVMe, USB, cartão), teclado, sistemas de arquivos
cd "$MODS"
{ find kernel/drivers/net kernel/net kernel/drivers/usb kernel/drivers/ata kernel/drivers/nvme kernel/drivers/scsi \
     kernel/drivers/mmc kernel/drivers/hid kernel/drivers/input kernel/drivers/virtio kernel/drivers/block \
     kernel/drivers/platform/x86 kernel/drivers/misc/cardreader kernel/crypto kernel/lib kernel/arch/x86/crypto \
     kernel/fs/fat kernel/fs/ntfs3 kernel/fs/exfat kernel/fs/ext4 kernel/fs/jbd2 kernel/fs/btrfs kernel/fs/xfs \
     kernel/fs/nls kernel/fs/isofs kernel/fs/fuse \
     -name '*.ko*' 2>/dev/null || true; } \
  | grep -vE 'drivers/net/(can|ipa|hamradio|arcnet|fddi|plip)/|ethernet/(mellanox|chelsio|qlogic|cavium|huawei|netronome|emulex|sfc|pensando|fungible|intel/(ice|idpf|i40e|iavf)|broadcom/bnxt|marvell/(octeontx2|prestera))/|scsi/(lpfc|qla2xxx|qla4xxx|megaraid|mpt3sas|bfa|be2iscsi|bnx2fc|bnx2i|csiostor|cxgbi|fnic|hisi_sas|isci|libfc|mpi3mr|pm8001|qedf|qedi|smartpqi|snic|elx|aacraid|aic7xxx|aic94xx|arcmsr)/' \
  > "$TRAB/modulos.txt" || true
# + tudo de que esses drivers dependem
python3 - "$TRAB/modulos.txt" <<'PY'
import sys
lista = sys.argv[1]
dep = {}
for l in open("modules.dep"):
    k, _, v = l.partition(":")
    dep[k.strip()] = v.split()
quero = set(x.strip() for x in open(lista) if x.strip())
fila = list(quero)
while fila:
    m = fila.pop()
    for d in dep.get(m, []):
        if d not in quero:
            quero.add(d); fila.append(d)
open(lista, "w").write("\n".join(sorted(quero)) + "\n")
PY
DEST="$R/usr/lib/modules/$KVER"
mkdir -p "$DEST"
tar -cf - -T "$TRAB/modulos.txt" | tar -C "$DEST" -xf -
cp modules.order modules.builtin modules.builtin.modinfo "$DEST/" 2>/dev/null || true
depmod -b "$R" "$KVER"
echo "drivers: $(wc -l < "$TRAB/modulos.txt") módulos"

# ---------------------------------------------------------------- firmware de rede
progresso "baixando o firmware de rede" 82
cd "$TRAB/pacotes"
if apt-cache show linux-firmware-intel-wireless >/dev/null 2>&1; then   # Ubuntu novo: firmware separado por fabricante
  apt-get download linux-firmware-intel-wireless linux-firmware-realtek linux-firmware-qualcomm-wireless \
    linux-firmware-broadcom-wireless linux-firmware-mediatek linux-firmware-marvell-wireless linux-firmware-misc >/dev/null 2>&1
  for d in linux-firmware-*.deb; do dpkg-deb -x "$d" "$TRAB/firmware"; done
elif apt-cache show linux-firmware >/dev/null 2>&1; then
  apt-get download linux-firmware >/dev/null 2>&1
  dpkg-deb -x linux-firmware_*.deb "$TRAB/firmware"
else
  apt-get download firmware-iwlwifi firmware-realtek firmware-atheros firmware-brcm80211 firmware-misc-nonfree >/dev/null 2>&1
  for d in firmware-*.deb; do dpkg-deb -x "$d" "$TRAB/firmware"; done
fi
FW=$(ls -d "$TRAB/firmware/lib/firmware" "$TRAB/firmware/usr/lib/firmware" 2>/dev/null | head -1 || true)
mkdir -p "$R/usr/lib/firmware"
cd "$FW"
for p in iwlwifi-* rtl_nic rtlwifi rtw88 rtw89 ath9k_htc ath10k ath11k ath12k ath6k ar9271* htc_* brcm cypress \
         mediatek mt7* rt2* rt3* rt73* regulatory.db* ti-connectivity tigon bnx2 intel/ice libertas mrvl; do
  for f in $p; do [ -e "$f" ] && cp -a --parents "$f" "$R/usr/lib/firmware/"; done
done
echo "firmware: $(du -sh "$R/usr/lib/firmware" | cut -f1)"

# ---------------------------------------------------------------- initramfs + ISO
progresso "gerando a imagem ISO" 90
( cd "$R" && find . -print0 | cpio --null -o -H newc --quiet ) | zstd -9 -T0 -q > "$TRAB/iso/boot/initrd.img"
cp "$RAIZ/os/iso/boot/grub/grub.cfg" "$TRAB/iso/boot/grub/"
grub-mkrescue -o "$SAIDA/$NOME" "$TRAB/iso" -- -volid ONEMAPAS_OS >/dev/null 2>&1
( cd "$SAIDA" && sha256sum "$NOME" > "$NOME.sha256" )
progresso "publicando" 97
echo "ISO pronta: $SAIDA/$NOME ($(du -h "$SAIDA/$NOME" | cut -f1))  sistema: $(du -sh "$R" | cut -f1)"
