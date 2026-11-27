#!/usr/bin/env python3
"""
rodar_iso.py: liga a ISO do OneMapas OS numa máquina virtual (QEMU) e mostra a TELA DO PC
numa página web (Flask), no IP deste computador.

    pip install flask
    python3 tools/rodar_iso.py                 # baixa a ISO mais recente e liga
    python3 tools/rodar_iso.py --iso minha.iso # usa uma ISO que você já tem

Depois abra no navegador (do PC ou do celular, na mesma rede):
    http://SEU-IP:5000/?t=CODIGO     tela do PC (o código aparece no terminal)
    http://SEU-IP:8080               interface do OneMapas OS (pede o código que aparece na tela do PC)
    http://SEU-IP:8090/<nome>/       sites criados

Precisa do QEMU:
    Ubuntu/Debian:  sudo apt install qemu-system-x86
    Termux:         pkg install qemu-system-x86-64-headless e2fsprogs
    Windows:        instale o QEMU (qemu.org) e deixe qemu-system-x86_64 no PATH
"""
import argparse
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib
from pathlib import Path

try:
    from flask import Flask, Response, abort, make_response, request
except ImportError:
    sys.exit("Falta o Flask. Instale com:  pip install flask")

REPO = "one-mapas-officeal/One-mapas-oficeal-OS-office"
MANIFEST = f"https://raw.githubusercontent.com/{REPO}/claude/os-website-builder-y5wp60/ISO/manifest.json"
ISO_PADRAO = f"https://github.com/{REPO}/releases/download/iso-beta-0.3.1/onemapas-os-0.3.1-beta-amd64.iso"
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
NO_TERMUX = "com.termux" in os.environ.get("PREFIX", "")
PASTA = Path(os.environ.get("ONEMAPAS_VM", Path.home() / ".onemapas-vm"))


# ------------------------------------------------------------------ utilidades
def ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 53))  # não envia nada: só descobre qual placa de rede sairia
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def porta_livre(p):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", p)) != 0


def numero(versao):
    try:
        return tuple(int(x) for x in versao.split("."))
    except ValueError:
        return (0,)


def iso_mais_nova():
    """A ISO de versão mais alta publicada nos Releases do GitHub (ou no manifest, ou a padrão)."""
    candidatas = []
    try:
        req = urllib.request.Request(RELEASES, headers={"Accept": "application/vnd.github+json", "User-Agent": "OneMapas"})
        with urllib.request.urlopen(req, timeout=15) as r:
            for rel in json.load(r):
                for a in rel.get("assets", []):
                    if a["name"].endswith(".iso") and a["name"].startswith("onemapas-os-"):
                        versao = a["name"][len("onemapas-os-"):].split("-")[0]
                        candidatas.append((numero(versao), a["browser_download_url"]))
    except Exception:
        pass
    if not candidatas:
        try:
            with urllib.request.urlopen(MANIFEST, timeout=10) as r:
                for i in json.load(r).get("isos", []):
                    if i.get("download") and i.get("status") == "pronta":
                        candidatas.append((numero(i["versao"]), i["download"]))
        except Exception:
            pass
    return max(candidatas)[1] if candidatas else ISO_PADRAO


def baixar_iso(destino: Path):
    url = iso_mais_nova()
    arq = destino / url.rsplit("/", 1)[-1]
    if arq.exists() and arq.stat().st_size > 50_000_000:
        print(f"ISO já baixada: {arq.name}")
        return arq
    print(f"Baixando {url}")
    tmp = arq.with_suffix(".parcial")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total, feito = int(r.headers.get("Content-Length") or 0), 0
        while True:
            bloco = r.read(1 << 20)
            if not bloco:
                break
            f.write(bloco)
            feito += len(bloco)
            if total:
                print(f"\r  {feito * 100 // total:3d}%  {feito >> 20} de {total >> 20} MB", end="", flush=True)
    print()
    tmp.rename(arq)
    for velha in destino.glob("onemapas-os-*.iso"):  # apaga as versões antigas (economiza espaço no celular)
        if velha != arq:
            velha.unlink()
            print(f"Apaguei a ISO antiga: {velha.name}")
    return arq


def disco_vazio(arq: Path):
    with open(arq, "rb") as f:
        return not any(f.read(2 * 1024 * 1024))


def criar_disco(arq: Path, tamanho_mb=1024):
    """Disco virtual para os dados (sites e chave do PC). Já vem com a pasta onemapas-dados."""
    if not arq.exists():
        with open(arq, "wb") as f:
            f.truncate(tamanho_mb * 1024 * 1024)
    if not disco_vazio(arq):
        return arq
    mkfs, debugfs = shutil.which("mkfs.ext4") or shutil.which("mke2fs"), shutil.which("debugfs")
    if mkfs:
        subprocess.run([mkfs, "-q", "-F", "-t", "ext4", "-L", "ONEMAPAS", str(arq)], check=False)
        if debugfs:
            subprocess.run([debugfs, "-w", "-R", "mkdir onemapas-dados", str(arq)], capture_output=True)
        print("Disco de dados pronto (os sites e a chave do PC ficam salvos).")
    else:
        dica = "pkg install e2fsprogs" if NO_TERMUX else "sudo apt install e2fsprogs"
        print(f"Aviso: falta o mkfs.ext4 ({dica}). Sem ele, os dados ficam na memória e somem ao desligar.")
    return arq


# ------------------------------------------------------------------ QEMU
class Maquina:
    def __init__(self, iso, disco, memoria, porta_monitor):
        self.iso, self.disco, self.memoria, self.porta_monitor = iso, disco, memoria, porta_monitor
        self.proc = None
        self.mon = None
        self.trava = threading.Lock()
        self.tela_png, self.tela_em = b"", 0.0
        self.tmp = Path(tempfile.mkdtemp(prefix="onemapas-tela-"))

    def ligar(self):
        qemu = shutil.which("qemu-system-x86_64")
        if not qemu:
            sys.exit("Não achei o qemu-system-x86_64. Instale com:\n  " +
                     ("pkg install qemu-system-x86-64-headless" if NO_TERMUX else
                      "sudo apt install qemu-system-x86   (Windows: instale do qemu.org e coloque no PATH)"))
        acel = ["-accel", "kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-accel", "tcg"]
        cmd = [qemu, *acel, "-m", str(self.memoria), "-smp", "2", "-display", "none", "-vga", "std",
               "-monitor", f"tcp:127.0.0.1:{self.porta_monitor},server,nowait",
               "-cdrom", str(self.iso), "-boot", "d",
               "-netdev", "user,id=n0,hostfwd=tcp::8080-:8080,hostfwd=tcp::8090-:8090",
               "-device", "e1000,netdev=n0"]
        if self.disco:
            cmd += ["-drive", f"file={self.disco},format=raw,if=virtio"]
        print("Ligando a máquina virtual", "(sem KVM: o boot leva alguns minutos)" if "tcg" in acel else "(com KVM)")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(50):
            time.sleep(0.2)
            if self.proc.poll() is not None:
                sys.exit("O QEMU fechou: " + self.proc.stderr.read().decode(errors="replace")[-500:])
            try:
                self._conectar()
                return
            except OSError:
                pass
        sys.exit("Não consegui falar com o monitor do QEMU.")

    def _conectar(self):
        self.mon = socket.create_connection(("127.0.0.1", self.porta_monitor), timeout=5)
        self._ler_ate_prompt()

    def _ler_ate_prompt(self):
        dados = b""
        while not dados.rstrip().endswith(b"(qemu)"):
            parte = self.mon.recv(65536)
            if not parte:
                raise OSError("monitor fechou")
            dados += parte
        return dados.decode(errors="replace")

    def comando(self, cmd):
        with self.trava:
            try:
                self.mon.sendall(cmd.encode() + b"\n")
                return self._ler_ate_prompt()
            except OSError:
                self._conectar()
                self.mon.sendall(cmd.encode() + b"\n")
                return self._ler_ate_prompt()

    def tela(self):
        """PNG da tela do PC (no máximo ~3 por segundo)."""
        if time.time() - self.tela_em < 0.3 and self.tela_png:
            return self.tela_png
        png = self.tmp / "tela.png"
        saida = self.comando(f"screendump {png} -f png")
        if "Invalid" in saida or "unexpected" in saida or not png.exists():  # QEMU antigo: só PPM
            ppm = self.tmp / "tela.ppm"
            self.comando(f"screendump {ppm}")
            self.tela_png = ppm_para_png(ppm.read_bytes())
        else:
            self.tela_png = png.read_bytes()
        self.tela_em = time.time()
        return self.tela_png

    def tecla(self, nome):
        self.comando(f"sendkey {nome}")

    def desligar(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.comando("quit")
            except Exception:
                self.proc.kill()


def ppm_para_png(d):
    partes = d.split(b"\n", 3)
    w, h = map(int, partes[1].split())
    px = partes[3]
    bruto = b"".join(b"\x00" + px[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def bloco(t, c):
        return struct.pack(">I", len(c)) + t + c + struct.pack(">I", zlib.crc32(t + c) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + bloco(b"IDAT", zlib.compress(bruto, 3)) + bloco(b"IEND", b""))


# teclado: nome da tecla no navegador -> nome no QEMU (teclado americano, como o console da ISO)
ESPECIAIS = {"Enter": "ret", "Backspace": "backspace", "Tab": "tab", "Escape": "esc", " ": "spc",
             "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
             "Delete": "delete", "Home": "home", "End": "end", "PageUp": "pgup", "PageDown": "pgdn"}
SIMBOLOS = {"-": "minus", "=": "equal", "[": "bracket_left", "]": "bracket_right", "\\": "backslash",
            ";": "semicolon", "'": "apostrophe", ",": "comma", ".": "dot", "/": "slash", "`": "grave_accent"}
COM_SHIFT = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
             "_": "minus", "+": "equal", "{": "bracket_left", "}": "bracket_right", "|": "backslash",
             ":": "semicolon", '"': "apostrophe", "<": "comma", ">": "dot", "?": "slash", "~": "grave_accent"}
ATALHOS = {"alt-f1", "alt-f2", "ctrl-c", "ctrl-alt-delete", "ret", "esc", "backspace", "tab", "up", "down"}


def para_qemu(k):
    if k in ESPECIAIS:
        return ESPECIAIS[k]
    if len(k) == 2 and k[0] == "F" and k[1].isdigit() or k in ("F10", "F11", "F12"):
        return k.lower()
    if len(k) == 1:
        if k.isascii() and (k.islower() or k.isdigit()):
            return k
        if k.isascii() and k.isupper():
            return "shift-" + k.lower()
        if k in SIMBOLOS:
            return SIMBOLOS[k]
        if k in COM_SHIFT:
            return "shift-" + COM_SHIFT[k]
    return None


# ------------------------------------------------------------------ Flask
PAGINA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Tela do PC · OneMapas OS</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif}
header{display:flex;gap:8px;align-items:center;padding:10px 12px;background:#161b22;flex-wrap:wrap}
header b{margin-right:auto}
#tela{display:block;width:100%;max-width:1100px;margin:0 auto;image-rendering:pixelated;background:#000;outline:none;cursor:text}
#tela:focus{box-shadow:0 0 0 2px #2f81f7}
.barra{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;padding:10px 12px;max-width:1100px;margin:0 auto}
button{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px 12px;font:inherit;cursor:pointer;min-width:44px}
button:hover{background:#30363d}
input{flex:1;min-width:160px;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px 10px;font:inherit}
.dica{color:#8b949e;font-size:13px;text-align:center;padding:0 12px 14px}
.dica a{color:#58a6ff}
</style></head><body>
<header><b>Tela do PC · OneMapas OS</b><span class="dica" id="estado" style="padding:0">conectando…</span></header>
<img id="tela" tabindex="0" alt="Tela do PC">
<div class="barra">
  <button data-k="1">1</button><button data-k="2">2</button><button data-k="3">3</button><button data-k="4">4</button>
  <button data-k="8">8</button><button data-k="9">9</button><button data-k="ret">Enter</button>
  <button data-k="esc">Esc</button><button data-k="backspace">⌫</button>
  <button data-k="alt-f1">Painel (Alt+F1)</button><button data-k="alt-f2">Terminal (Alt+F2)</button>
  <button data-k="ctrl-alt-delete">Ctrl+Alt+Del</button>
</div>
<div class="barra"><input id="texto" placeholder="Digitar texto (ex.: senha do Wi-Fi)" autocomplete="off" autocapitalize="off">
  <button id="enviar">Enviar</button><button id="enviar-enter">Enviar + Enter</button></div>
<p class="dica">Clique na tela e digite pelo teclado, ou use os botões. No celular, use o campo de texto.<br>
Interface do OneMapas: <a id="l8080" target="_blank"></a> · Sites: <a id="l8090" target="_blank"></a></p>
<script>
const img=document.getElementById("tela"), est=document.getElementById("estado");
const host=location.hostname;
for (const [id,p] of [["l8080",8080],["l8090",8090]]) { const a=document.getElementById(id); a.href=`http://${host}:${p}/`; a.textContent=`http://${host}:${p}`; }
let ok=0;
function atualizar(){
  const n=new Image();
  n.onload=()=>{img.src=n.src; if(!ok){est.textContent="ao vivo"; ok=1;} setTimeout(atualizar,400);};
  n.onerror=()=>{est.textContent="sem imagem (a máquina virtual ainda está ligando?)"; ok=0; setTimeout(atualizar,1500);};
  n.src="/tela.png?"+Date.now();
}
atualizar();
async function tecla(k){ await fetch("/tecla",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({k})}); }
async function texto(t){ await fetch("/texto",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({t})}); }
document.querySelectorAll("[data-k]").forEach(b=>b.onclick=()=>{tecla(b.dataset.k); img.focus();});
img.addEventListener("keydown",e=>{
  if(e.ctrlKey||e.metaKey) return;
  e.preventDefault();
  if(e.altKey && /^F\\d$/.test(e.key)) return tecla("alt-"+e.key.toLowerCase());
  fetch("/tecla",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chave:e.key})});
});
img.onclick=()=>img.focus();
const caixa=document.getElementById("texto");
document.getElementById("enviar").onclick=async()=>{await texto(caixa.value); caixa.value="";};
document.getElementById("enviar-enter").onclick=async()=>{await texto(caixa.value); await tecla("ret"); caixa.value="";};
caixa.onkeydown=async e=>{ if(e.key==="Enter"){ e.preventDefault(); await texto(caixa.value); await tecla("ret"); caixa.value=""; } };
</script></body></html>"""


def criar_app(vm, codigo):
    app = Flask(__name__)

    @app.before_request
    def proteger():
        if request.args.get("t") == codigo:
            return  # a resposta de "/" grava o cookie
        if request.cookies.get("onemapas_tela") != codigo:
            abort(Response("Abra o endereço com o código que aparece no terminal: http://IP:5000/?t=CODIGO", 403,
                           mimetype="text/plain; charset=utf-8"))

    @app.get("/")
    def inicio():
        r = make_response(PAGINA)
        r.set_cookie("onemapas_tela", codigo, max_age=7 * 86400, httponly=True, samesite="Strict")
        return r

    @app.get("/tela.png")
    def tela():
        try:
            png = vm.tela()
        except Exception as e:
            return Response(f"sem tela: {e}", 503)
        return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/tecla")
    def tecla():
        d = request.get_json(silent=True) or {}
        nome = d.get("k") if d.get("k") in ATALHOS or (isinstance(d.get("k"), str) and len(d["k"]) == 1 and d["k"].isdigit()) \
            else para_qemu(str(d.get("chave", "")))
        if not nome:
            return {"ok": False}, 400
        vm.tecla(nome)
        return {"ok": True}

    @app.post("/texto")
    def texto():
        t = str((request.get_json(silent=True) or {}).get("t", ""))[:200]
        enviadas = 0
        for ch in t:
            nome = para_qemu(ch)
            if nome:
                vm.tecla(nome)
                enviadas += 1
                time.sleep(0.03)
        return {"ok": True, "teclas": enviadas}

    return app


def main():
    ap = argparse.ArgumentParser(description="Liga a ISO do OneMapas OS e mostra a tela do PC no navegador (Flask).")
    ap.add_argument("--iso", help="arquivo .iso (se não passar, baixa a mais recente)")
    ap.add_argument("--porta", type=int, default=5000, help="porta da página da tela (padrão 5000)")
    ap.add_argument("--memoria", type=int, default=1024, help="memória da máquina virtual em MB (padrão 1024)")
    ap.add_argument("--sem-disco", action="store_true", help="não cria disco de dados (tudo fica na memória)")
    ap.add_argument("--codigo", help="código de acesso da página (padrão: aleatório)")
    a = ap.parse_args()

    PASTA.mkdir(parents=True, exist_ok=True)
    iso = Path(a.iso) if a.iso else baixar_iso(PASTA)
    if not iso.exists():
        sys.exit(f"ISO não encontrada: {iso}")
    disco = None if a.sem_disco else criar_disco(PASTA / "dados.img")
    for p in (8080, 8090, a.porta):
        if not porta_livre(p):
            sys.exit(f"A porta {p} já está em uso neste computador. Feche o programa que usa ela e tente de novo.")

    vm = Maquina(iso, disco, a.memoria, 45454)
    vm.ligar()
    codigo = a.codigo or f"{secrets.randbelow(10**6):06d}"
    ip = ip_local()
    print(f"""
==============================================================
  OneMapas OS ligando na máquina virtual.

  Tela do PC:            http://{ip}:{a.porta}/?t={codigo}
  Interface OneMapas:    http://{ip}:8080   (código na tela do PC)
  Sites:                 http://{ip}:8090/<nome>/

  Código da página da tela: {codigo}
  Para desligar: Ctrl+C aqui.
==============================================================
""", flush=True)
    try:
        criar_app(vm, codigo).run(host="0.0.0.0", port=a.porta, threaded=True)
    finally:
        vm.desligar()


if __name__ == "__main__":
    main()
