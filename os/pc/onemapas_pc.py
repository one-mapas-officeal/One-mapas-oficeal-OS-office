#!/usr/bin/env python3
"""
OneMapas OS - PC (o servidor onde o site roda).

  python3 onemapas_pc.py      -> interface em http://127.0.0.1:8080

O que ele faz:
  1. Interface gráfica (área de trabalho estilo Windows / Mint / Mac) só para este PC.
  2. Cria os sites numa pasta da Área de Trabalho: "Meus Sites OneMapas/<nome>/".
  3. SERVE OS SITES DIRETO: o visitante abre http://IP-do-PC:8090/<nome>/ (porta "porta_sites").
     O PC tenta abrir essa porta no roteador sozinho (UPnP).
  4. A cada 5 minutos manda um sinal ao Render: o Render grava no Supabase o endereço atual
     do PC (o IP muda), testa se a porta está aberta de fora e fica acordado.
  5. Envia uma cópia de cada site ao Supabase (pelo Render), como backup.

Este PC é o dono dos sites: ele tem uma chave secreta (~/.config/onemapas/config.json),
e o Render guarda só o hash dela.

Só usa a biblioteca padrão do Python.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent
WEBUI = Path(os.environ.get("ONEMAPAS_WEBUI", AQUI.parent / "webui"))
CONFIG_DIR = Path(os.environ.get("ONEMAPAS_CONFIG", Path.home() / ".config" / "onemapas"))
CONFIG_FILE = CONFIG_DIR / "config.json"
VERSAO = "0.5.0"

PADRAO = {
    "servidor": os.environ.get("ONEMAPAS_SERVIDOR", "https://onemapas-gateway.onrender.com"),
    "dono": socket.gethostname(),
    "chave": "",
    "porta_sites": int(os.environ.get("ONEMAPAS_PORTA_SITES", "8090")),
}
NOME_VALIDO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
CAMINHO_VALIDO = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}/){0,5}[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}$")
META = "onemapas-site.json"
MAX_ARQUIVO_EDITOR = 1024 * 1024
# Modo rede (ISO de Linux puro): a interface é aberta de outro aparelho (celular) pelo IP do PC,
# só com o código de acesso que aparece na tela do PC.
LAN = os.environ.get("ONEMAPAS_LAN") == "1"
IP_LITERAL = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
tentativas_pin = {}

estado = {
    "render": "aguardando",          # sinal de 5 em 5 min: "ok" | "sem conexão"
    "ultima_batida": None, "ultimo_erro": None,
    "publico": {"ip": None, "porta": None, "porta_aberta": None, "endereco": None,
                "upnp": None, "upnp_erro": None, "ip_local": None, "cgnat": None},
    "visitas": 0, "ultima_visita": None,
    "copia": {"estado": "aguardando", "ultima": None, "arquivos": 0, "erro": None},
}
MAX_ARQUIVO_COPIA = 5 * 1024 * 1024
INTERVALO_BATIDA = 5 * 60    # sinal ao Render (mantém ele acordado e atualiza o IP do PC)
INTERVALO_COPIA = 5 * 60     # cópia ao Supabase (e na hora, quando salva pela interface)
acordar_copia = threading.Event()
acordar_batida = threading.Event()


def agora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ configuração
_trava_cfg = threading.Lock()


def config():
    with _trava_cfg:
        cfg = dict(PADRAO)
        if CONFIG_FILE.exists():
            try:
                cfg.update(json.loads(CONFIG_FILE.read_text("utf-8")))
            except Exception:
                pass
        mudou = False
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", cfg.get("chave") or ""):
            cfg["chave"] = secrets.token_urlsafe(32)
            mudou = True
        if not re.fullmatch(r"\d{6}", str(cfg.get("pin") or "")):
            cfg["pin"] = f"{secrets.randbelow(10**6):06d}"
            mudou = True
        if mudou:
            _gravar(cfg)
        return cfg


def token_sessao():
    cfg = config()
    return hmac.new(cfg["chave"].encode(), ("sessao:" + cfg["pin"]).encode(), hashlib.sha256).hexdigest()[:40]


PAGINA_ENTRAR = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>OneMapas OS</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;font-family:system-ui,sans-serif;
background:linear-gradient(135deg,#0b3d91,#42a5f5);color:#fff;text-align:center}.c{max-width:360px;width:100%}
input{font-size:28px;letter-spacing:8px;text-align:center;width:100%;padding:12px;border-radius:10px;border:0;margin:12px 0}
button{font-size:18px;padding:12px 24px;border-radius:10px;border:0;background:#fff;color:#0b3d91;font-weight:700;width:100%}
p{opacity:.85}</style></head><body><div class="c"><h1>OneMapas OS</h1>
<p>Digite o código que aparece na tela do PC.</p>
<input id="p" inputmode="numeric" maxlength="6" autocomplete="one-time-code" autofocus>
<button id="b">Entrar</button><p id="m"></p></div>
<script>
async function entrar(){const r=await fetch("/entrar",{method:"POST",headers:{"Content-Type":"application/json","X-OneMapas":"1"},
 body:JSON.stringify({pin:document.getElementById("p").value})});
 if(r.ok)location.replace("/");else document.getElementById("m").textContent=(await r.json()).mensagem||"Código errado";}
document.getElementById("b").onclick=entrar;document.getElementById("p").onkeydown=e=>{if(e.key==="Enter")entrar()};
</script></body></html>"""


def _gravar(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    os.chmod(CONFIG_FILE, 0o600)


def salvar_config(novo):
    cfg = config()
    with _trava_cfg:
        for k in ("servidor", "dono", "chave", "porta_sites"):
            v = str(novo.get(k) or "").strip()
            if not v:
                continue
            if k == "porta_sites":
                if not v.isdigit() or not 1024 <= int(v) <= 65535:
                    return False
                cfg[k] = int(v)
                continue
            if k == "chave" and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", v):
                return False
            if k == "servidor" and not re.match(r"^https?://[A-Za-z0-9.:-]+$", v.rstrip("/")):
                return False
            cfg[k] = v.rstrip("/") if k == "servidor" else v
        _gravar(cfg)
    return True


def area_de_trabalho():
    try:
        p = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=3)
        c = p.stdout.strip()
        if c and c != str(Path.home()):
            return Path(c)
    except Exception:
        pass
    for nome in ("Área de Trabalho", "Desktop"):
        if (Path.home() / nome).is_dir():
            return Path.home() / nome
    return Path.home() / "Área de Trabalho"


def pasta_sites():
    p = Path(os.environ.get("ONEMAPAS_SITES", area_de_trabalho() / "Meus Sites OneMapas"))
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------ conversa com o Render
def render(metodo, rota, dados=None, timeout=15):
    cfg = config()
    corpo = json.dumps(dados).encode() if dados is not None else None
    req = urllib.request.Request(cfg["servidor"] + rota, method=metodo, data=corpo,
                                 headers={"X-Dono-Chave": cfg["chave"], "Content-Type": "application/json",
                                          "User-Agent": "OneMapas-PC/" + VERSAO})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read()
            return r.status, (json.loads(txt) if txt else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"mensagem": f"HTTP {e.code}"}
    except Exception as e:
        return None, {"mensagem": f"Sem conexão com o Render ({e.__class__.__name__})."}


# ------------------------------------------------------------------ servidor público dos sites (visitantes)
def servir_arquivo_local(nome, metodo, caminho):
    """Responde um visitante com os arquivos da pasta do site."""
    base = (pasta_sites() / nome).resolve()
    if not base.is_dir():
        return 404, {"Content-Type": "text/html; charset=utf-8"}, b"<h1>404</h1><p>Pasta do site nao existe neste PC.</p>"
    if metodo not in ("GET", "HEAD"):
        return 405, {"Content-Type": "text/plain; charset=utf-8"}, "Este site é estático (só GET).".encode()
    rel = urllib.parse.unquote(urllib.parse.urlsplit(caminho).path).lstrip("/")
    alvo = (base / rel).resolve()
    if alvo != base and not str(alvo).startswith(str(base) + os.sep):
        return 403, {"Content-Type": "text/plain"}, b"proibido"
    if alvo.is_dir():
        alvo = alvo / "index.html"
    if not alvo.is_file() or alvo.name == META or any(p.startswith(".") for p in alvo.relative_to(base).parts):
        pag = base / "404.html"
        if pag.is_file():
            return 404, {"Content-Type": "text/html; charset=utf-8"}, pag.read_bytes()
        return 404, {"Content-Type": "text/html; charset=utf-8"}, b"<h1>404</h1><p>Pagina nao encontrada.</p>"
    tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
    if tipo.startswith("text/") or tipo in ("application/javascript", "application/json", "image/svg+xml"):
        tipo += "; charset=utf-8"
    corpo = alvo.read_bytes() if metodo == "GET" else b""
    return 200, {"Content-Type": tipo, "Cache-Control": "no-cache"}, corpo


class HandlerSites(BaseHTTPRequestHandler):
    """Porta pública (8090): só entrega os arquivos dos sites. Nada de interface, API ou configuração."""
    server_version = "OneMapas"

    def log_message(self, fmt, *args):
        sys.stderr.write("[sites] %s %s\n" % (self.client_address[0], fmt % args))

    def responder(self, status, cab, corpo):
        self.send_response(status)
        for k, v in cab.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def do_GET(self):
        caminho = urllib.parse.urlsplit(self.path).path
        if caminho == "/_onemapas":  # o Render testa se a porta está aberta
            return self.responder(200, {"Content-Type": "text/plain"}, b"ok")
        partes = [x for x in caminho.split("/") if x]
        if not partes:
            itens = "".join(f'<li><a href="/{s["nome"]}/">{s["titulo"].replace("<", "&lt;")}</a></li>' for s in meus_sites())
            pag = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                   f'<title>Sites deste PC</title><body style="font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px">'
                   f'<h1>Sites deste PC</h1><ul>{itens or "<li>Nenhum site ainda.</li>"}</ul>'
                   f'<p style="color:#888">OneMapas OS</p>').encode()
            return self.responder(200, {"Content-Type": "text/html; charset=utf-8"}, pag)
        nome = partes[0]
        if not NOME_VALIDO.match(nome) or not (pasta_sites() / nome).is_dir():
            return self.responder(404, {"Content-Type": "text/html; charset=utf-8"}, b"<h1>404</h1><p>Site nao existe neste PC.</p>")
        if len(partes) == 1 and not caminho.endswith("/"):
            return self.responder(301, {"Location": f"/{nome}/"}, b"")
        status, cab, corpo = servir_arquivo_local(nome, self.command, "/" + "/".join(partes[1:]) + ("/" if caminho.endswith("/") else ""))
        estado["visitas"] += 1
        estado["ultima_visita"] = f"{agora()} {nome}{caminho[len(nome) + 1:][:60]} -> {status}"
        self.responder(status, cab, corpo)

    do_HEAD = do_GET


def link_site(nome):
    end = estado["publico"].get("endereco")
    return f"{end}/{nome}/" if end else ""


def url_escolhida(nome):
    """Endereço com o nome que a pessoa escolheu (o Render manda o visitante para este PC)."""
    return f"{config()['servidor']}/_site/{nome}/?sistema=onemapasoficealOS&site=true&load=em-espera"


def eh_cgnat(ip):
    """IP do roteador em faixa de CGNAT (100.64.0.0/10) ou privada: o provedor não dá IP público."""
    try:
        import ipaddress
        a = ipaddress.ip_address(ip)
        return a in ipaddress.ip_network("100.64.0.0/10") or a.is_private
    except Exception:
        return None


def bater():
    """Sinal ao Render: (re)abre a porta no roteador por UPnP e informa a porta; o Render testa e grava no Supabase."""
    porta = int(config()["porta_sites"])
    pub = estado["publico"]
    try:
        sys.path.insert(0, str(AQUI))
        import upnp
        u = upnp.abrir_porta(porta)
        pub.update(upnp=u["ok"], upnp_erro=u["erro"], ip_local=u["ip_local"] or pub.get("ip_local"))
        if u["ip_externo"]:
            pub["ip_roteador"] = u["ip_externo"]
            pub["cgnat"] = eh_cgnat(u["ip_externo"])
    except Exception as e:
        pub.update(upnp=False, upnp_erro=str(e)[:120])
    if not pub.get("ip_local"):
        try:  # IP deste PC na rede local (não envia nada: só pergunta ao sistema qual placa sairia)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as so:
                so.connect(("1.1.1.1", 53))
                pub["ip_local"] = so.getsockname()[0]
        except OSError:
            pass
    st, r = render("POST", "/api/pc/batida", {"porta": porta}, timeout=30)
    if st == 200:
        estado["render"] = "ok"
        estado["ultima_batida"] = agora()
        pub.update(ip=r.get("ip_publico"), porta=porta, porta_aberta=r.get("porta_aberta"), endereco=r.get("endereco"))
        # o roteador diz um IP e o Render vê outro: há um NAT do provedor no meio (CGNAT)
        if pub.get("ip_roteador") and r.get("ip_publico") and pub["ip_roteador"] != r["ip_publico"]:
            pub["cgnat"] = True
    else:
        estado["render"] = "sem conexão"
        estado["ultimo_erro"] = f"{agora()} {r.get('mensagem', st)}"


def trabalhador_batida():
    while True:
        try:
            bater()
        except Exception as e:
            estado["ultimo_erro"] = f"{agora()} {e}"[:200]
        acordar_batida.wait(INTERVALO_BATIDA)
        acordar_batida.clear()


# ------------------------------------------------------------------ cópia no Supabase (pelo Render)
def enviar_arquivo_copia(nome, caminho, corpo):
    cfg = config()
    url = f"{cfg['servidor']}/api/copia/{nome}?caminho={urllib.parse.quote(caminho)}"
    req = urllib.request.Request(url, method="PUT", data=corpo,
                                 headers={"X-Dono-Chave": cfg["chave"], "Content-Type": "application/octet-stream",
                                          "User-Agent": "OneMapas-PC/" + VERSAO})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status == 200, ""
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read()).get("mensagem", f"HTTP {e.code}")
        except Exception:
            return False, f"HTTP {e.code}"
    except Exception as e:
        return False, e.__class__.__name__


def arquivos_para_copia(nome):
    base = pasta_sites() / nome
    res = {}
    for f in base.rglob("*"):
        rel = f.relative_to(base).as_posix()
        if (f.is_file() and f.name != META and not any(x.startswith(".") for x in f.relative_to(base).parts)
                and f.stat().st_size <= MAX_ARQUIVO_COPIA and CAMINHO_VALIDO.match(rel)):
            res[rel] = f
    return res


def sincronizar_copias():
    """Compara cada arquivo (SHA-256) com o que já foi enviado e manda só o que mudou."""
    arq_estado = CONFIG_DIR / "copia.json"
    try:
        enviados = json.loads(arq_estado.read_text("utf-8"))
    except Exception:
        enviados = {}
    erros, total = [], 0
    for site in meus_sites():
        nome = site["nome"]
        antes = enviados.get(nome, {})
        agora_ = {}
        for rel, f in arquivos_para_copia(nome).items():
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            agora_[rel] = h
            if antes.get(rel) != h:
                ok, msg = enviar_arquivo_copia(nome, rel, f.read_bytes())
                if not ok:
                    erros.append(f"{nome}/{rel}: {msg}")
                    agora_[rel] = antes.get(rel)  # tenta de novo na próxima volta
        for rel in set(antes) - set(agora_):
            st, r = render("DELETE", f"/api/copia/{nome}?caminho={urllib.parse.quote(rel)}")
            if st not in (200, 400):
                erros.append(f"{nome}/{rel}: apagar {r.get('mensagem', st)}")
                agora_[rel] = antes[rel]
        enviados[nome] = {k: v for k, v in agora_.items() if v}
        total += len(enviados[nome])
    for nome in set(enviados) - {s["nome"] for s in meus_sites()}:
        enviados.pop(nome)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    arq_estado.write_text(json.dumps(enviados), "utf-8")
    estado["copia"].update(estado="com erro" if erros else "em dia", ultima=agora(), arquivos=total,
                           erro=erros[0] if erros else None)


def trabalhador_copia():
    while True:
        acordar_copia.wait(INTERVALO_COPIA)
        acordar_copia.clear()
        try:
            sincronizar_copias()
        except Exception as e:
            estado["copia"].update(estado="com erro", erro=str(e)[:200])


# ------------------------------------------------------------------ sites (pasta na Área de Trabalho)
def modelo(titulo, descricao, cor, dono):
    t = titulo.replace("&", "&amp;").replace("<", "&lt;")
    d = descricao.replace("&", "&amp;").replace("<", "&lt;")
    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
  <header><h1>{t}</h1></header>
  <main>
    <p>{d}</p>
  </main>
  <footer>Rodando no PC de {dono.replace("<", "&lt;")} · OneMapas OS</footer>
</body>
</html>
"""
    css = f""":root {{ --cor: {cor}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: system-ui, sans-serif; background: #f6f7f9; color: #1d2330; }}
header {{ background: var(--cor); color: #fff; padding: 56px 16px; text-align: center; }}
h1 {{ margin: 0; }}
main {{ max-width: 760px; margin: 32px auto; padding: 0 16px; font-size: 1.1rem; line-height: 1.6; }}
footer {{ text-align: center; color: #889; padding: 24px; font-size: .85rem; }}
"""
    return html, css


def ler_meta(nome):
    try:
        return json.loads((pasta_sites() / nome / META).read_text("utf-8"))
    except Exception:
        return {}


def meus_sites():
    res = []
    for p in sorted(pasta_sites().iterdir()):
        if p.is_dir() and NOME_VALIDO.match(p.name):
            m = ler_meta(p.name)
            # url = o endereço do NOME ESCOLHIDO (é o que a pessoa compartilha); link_direto = IP:porta do PC
            res.append({"nome": p.name, "url": m.get("url") or url_escolhida(p.name), "link_direto": link_site(p.name),
                        "titulo": m.get("titulo", p.name),
                        "criado_em": m.get("criado_em"), "atualizado_em": m.get("atualizado_em"),
                        "pasta": str(p)})
    return res


def verificar(nome):
    nome = (nome or "").strip().lower()
    st, v = render("GET", "/api/verificar?nome=" + urllib.parse.quote(nome))
    if st != 200:
        return {"nome": nome, "disponivel": False, "resposta": "não", "url": "",
                "mensagem": v.get("mensagem", "Render fora do ar."), "checagens": []}
    if v.get("disponivel") and (pasta_sites() / nome).exists():
        v.update(disponivel=False, resposta="não", mensagem="Escolha outra URL")
        v.setdefault("checagens", []).append({"onde": "Este PC", "resultado": "pasta já existe"})
    else:
        v.setdefault("checagens", []).append({"onde": "Este PC", "resultado": "livre"})
    return v


def criar(dados):
    cfg = config()
    v = verificar(dados.get("nome", ""))
    if not v.get("disponivel"):
        return 409, v
    st, r = render("POST", "/api/registrar", {"nome": v["nome"], "dono_nome": cfg["dono"]})
    if st != 201:
        return (409 if st == 409 else 502), {**v, **r, "disponivel": False}
    nome = v["nome"]
    cor = dados.get("cor") if re.match(r"^#[0-9a-fA-F]{6}$", str(dados.get("cor", ""))) else "#2563eb"
    titulo = str(dados.get("titulo") or nome)[:120]
    html, css = modelo(titulo, str(dados.get("descricao") or "Bem-vindo ao meu site!")[:2000], cor, cfg["dono"])
    pasta = pasta_sites() / nome
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "index.html").write_text(html, "utf-8")
    (pasta / "style.css").write_text(css, "utf-8")
    meta = {"nome": nome, "titulo": titulo, "url": r.get("url") or v.get("url") or url_escolhida(nome), "dono": cfg["dono"],
            "criado_em": agora(), "atualizado_em": agora()}
    (pasta / META).write_text(json.dumps(meta, indent=2, ensure_ascii=False), "utf-8")
    acordar_copia.set()
    bater()  # grava já o endereço deste PC no novo site
    return 201, {**v, "url": meta["url"], "link_direto": link_site(nome), "porta_aberta": estado["publico"].get("porta_aberta"),
                 "pasta": str(pasta), "mensagem": "Site criado!"}


def caminho_seguro(nome, caminho):
    if not NOME_VALIDO.match(nome or "") or not CAMINHO_VALIDO.match(caminho or "") or ".." in caminho:
        return None
    if caminho == META:
        return None
    base = (pasta_sites() / nome).resolve()
    alvo = (base / caminho).resolve()
    return alvo if str(alvo).startswith(str(base) + os.sep) else None


def listar_arquivos(nome):
    base = pasta_sites() / nome
    return [{"caminho": f.relative_to(base).as_posix(), "tamanho": f.stat().st_size}
            for f in sorted(base.rglob("*")) if f.is_file() and f.name != META]


def tocar_meta(nome):
    m = ler_meta(nome)
    if m:
        m["atualizado_em"] = agora()
        (pasta_sites() / nome / META).write_text(json.dumps(m, indent=2, ensure_ascii=False), "utf-8")


# ------------------------------------------------------------------ HTTP local (interface)
class Handler(BaseHTTPRequestHandler):
    server_version = "OneMapasPC/" + VERSAO

    def log_message(self, fmt, *args):
        sys.stderr.write("[pc] " + fmt % args + "\n")

    def enviar(self, codigo, obj):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def corpo_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_ARQUIVO_EDITOR * 2:
            return None
        try:
            return json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            return {}

    def host_ok(self):
        """Bloqueia DNS rebinding: só 127.0.0.1/localhost, ou um IP numérico no modo rede."""
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost") or (LAN and bool(IP_LITERAL.match(host)))

    def logado(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host in ("127.0.0.1", "localhost") and self.client_address[0] == "127.0.0.1":
            return True
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "onemapas_sessao" and hmac.compare_digest(v, token_sessao()):
                return True
        return False

    def seguro(self):
        """Só aceita a própria interface: bloqueia outros sites (CSRF), DNS rebinding e quem não tem o código."""
        if not self.host_ok() or not self.logado():
            return False
        if self.command != "GET" and self.headers.get("X-OneMapas") != "1":
            return False
        return True

    def pagina_entrar(self):
        corpo = PAGINA_ENTRAR.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def entrar(self):
        ip = self.client_address[0]
        t = time.time()
        lst = [x for x in tentativas_pin.get(ip, []) if t - x < 60]
        if len(lst) >= 5:
            return self.enviar(429, {"mensagem": "Muitas tentativas. Espere 1 minuto."})
        tentativas_pin[ip] = lst + [t]
        dados = self.corpo_json() or {}
        if not hmac.compare_digest(str(dados.get("pin", "")), config()["pin"]):
            return self.enviar(403, {"mensagem": "Código errado."})
        corpo = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", f"onemapas_sessao={token_sessao()}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if LAN and self.host_ok() and not self.logado():
            if self.path.split("?")[0] in ("/", "/entrar"):
                return self.pagina_entrar()
            return self.enviar(401, {"mensagem": "Digite o código de acesso."})
        if not self.seguro():
            return self.enviar(403, {"mensagem": "proibido"})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/" or u.path.startswith("/_os/"):
            alvo = WEBUI / ("index.html" if u.path == "/" else u.path[5:])
            if alvo.resolve().parent == WEBUI.resolve() and alvo.is_file():
                corpo = alvo.read_bytes()
                tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", tipo + ("; charset=utf-8" if tipo.startswith(("text/", "application/javascript")) else ""))
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return self.wfile.write(corpo)
            return self.enviar(404, {"mensagem": "não encontrado"})

        if u.path == "/api/status":
            cfg = config()
            st, r = render("GET", "/api/status", timeout=6)
            return self.enviar(200, {"ok": True, "versao": VERSAO, "dono": cfg["dono"], "servidor": cfg["servidor"],
                                     "render_online": st == 200, "porta_sites": cfg["porta_sites"],
                                     "sites": len(meus_sites()), "pc": estado, "pasta": str(pasta_sites())})
        if u.path == "/api/verificar":
            return self.enviar(200, verificar(q.get("nome", [""])[0]))
        if u.path == "/api/meus-sites":
            return self.enviar(200, {"sites": meus_sites()})
        if u.path in ("/api/manifest", "/api/novidades"):
            st, r = render("GET", u.path, timeout=8)
            if st != 200:
                local = RAIZ / ("ISO/manifest.json" if u.path == "/api/manifest" else "os/webui/novidades.json")
                if local.exists():
                    r = {"origem": "este PC", **json.loads(local.read_text("utf-8"))}
            return self.enviar(200, r)
        if u.path == "/api/config":
            cfg = config()
            return self.enviar(200, {"servidor": cfg["servidor"], "dono": cfg["dono"], "chave": cfg["chave"],
                                     "porta_sites": cfg["porta_sites"], "pasta": str(pasta_sites())})
        m = re.fullmatch(r"/api/sites/([a-z0-9-]+)/(arquivos|arquivo)", u.path)
        if m:
            nome = m.group(1)
            if not (pasta_sites() / nome).is_dir():
                return self.enviar(404, {"mensagem": "Site não existe neste PC."})
            if m.group(2) == "arquivos":
                return self.enviar(200, {"arquivos": listar_arquivos(nome)})
            alvo = caminho_seguro(nome, q.get("caminho", [""])[0])
            if not alvo or not alvo.is_file():
                return self.enviar(404, {"mensagem": "Arquivo não encontrado."})
            if alvo.stat().st_size > MAX_ARQUIVO_EDITOR:
                return self.enviar(413, {"mensagem": "Arquivo grande demais para o editor."})
            return self.enviar(200, {"conteudo": alvo.read_text("utf-8", errors="replace")})
        return self.enviar(404, {"mensagem": "rota desconhecida"})

    def do_POST(self):
        if LAN and self.path == "/entrar" and self.host_ok() and self.headers.get("X-OneMapas") == "1":
            return self.entrar()
        if not self.seguro():
            return self.enviar(403, {"mensagem": "proibido"})
        dados = self.corpo_json()
        if dados is None:
            return self.enviar(413, {"mensagem": "Muito grande."})
        if self.path == "/api/criar":
            return self.enviar(*criar(dados))
        if self.path == "/api/config":
            ok = salvar_config(dados)
            if ok:
                acordar_batida.set()
            return self.enviar(200 if ok else 400, {"ok": ok})
        if self.path == "/api/testar-porta":
            bater()
            return self.enviar(200, estado["publico"])
        if self.path == "/api/abrir-pasta":
            nome = str(dados.get("nome", ""))
            alvo = pasta_sites() / nome if NOME_VALIDO.match(nome) else pasta_sites()
            if alvo.exists() and shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(alvo)])
                return self.enviar(200, {"ok": True})
            return self.enviar(404, {"ok": False, "mensagem": str(alvo)})
        return self.enviar(404, {"mensagem": "rota desconhecida"})

    def do_PUT(self):
        if not self.seguro():
            return self.enviar(403, {"mensagem": "proibido"})
        dados = self.corpo_json()
        if dados is None:
            return self.enviar(413, {"mensagem": "Arquivo grande demais."})
        m = re.fullmatch(r"/api/sites/([a-z0-9-]+)/arquivo", self.path)
        if not m or not (pasta_sites() / m.group(1)).is_dir():
            return self.enviar(404, {"mensagem": "Site não existe neste PC."})
        alvo = caminho_seguro(m.group(1), str(dados.get("caminho", "")))
        if not alvo:
            return self.enviar(400, {"mensagem": "Nome de arquivo inválido."})
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(str(dados.get("conteudo", "")), "utf-8")
        tocar_meta(m.group(1))
        acordar_copia.set()
        return self.enviar(200, {"ok": True, "mensagem": "Salvo."})

    def do_DELETE(self):
        if not self.seguro():
            return self.enviar(403, {"mensagem": "proibido"})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        m = re.fullmatch(r"/api/sites/([a-z0-9-]+)(/arquivo)?", u.path)
        if not m or not (pasta_sites() / m.group(1)).is_dir():
            return self.enviar(404, {"mensagem": "Site não existe neste PC."})
        nome = m.group(1)
        if m.group(2):
            alvo = caminho_seguro(nome, q.get("caminho", [""])[0])
            if not alvo or not alvo.is_file():
                return self.enviar(404, {"mensagem": "Arquivo não encontrado."})
            alvo.unlink()
            tocar_meta(nome)
            acordar_copia.set()
            return self.enviar(200, {"ok": True})
        st, r = render("DELETE", f"/api/registro/{nome}")
        if st not in (200, 403, 404):
            return self.enviar(502, {"mensagem": "Não consegui liberar o nome no Render: " + str(r.get("mensagem", st))})
        lixo = pasta_sites().parent / ".onemapas-lixeira"
        lixo.mkdir(exist_ok=True)
        shutil.move(str(pasta_sites() / nome), str(lixo / f"{nome}-{int(time.time())}"))
        return self.enviar(200, {"ok": True, "mensagem": f"Site {nome} apagado (a pasta foi para .onemapas-lixeira)."})


def main():
    cfg = config()
    porta_sites = int(cfg["porta_sites"])
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", porta_sites), HandlerSites).serve_forever, daemon=True).start()
    threading.Thread(target=trabalhador_batida, daemon=True).start()
    threading.Thread(target=trabalhador_copia, daemon=True).start()
    acordar_copia.set()
    porta = int(os.environ.get("ONEMAPAS_PORTA", "8080"))
    if LAN and os.environ.get("ONEMAPAS_PIN_ARQ"):
        Path(os.environ["ONEMAPAS_PIN_ARQ"]).write_text(cfg["pin"])
    print(f"OneMapas OS {VERSAO} (PC) em http://{'0.0.0.0' if LAN else '127.0.0.1'}:{porta}\n"
          f"  sites em: {pasta_sites()}\n  visitantes: http://<IP>:{porta_sites}/<nome>/\n  Render: {cfg['servidor']}", flush=True)
    ThreadingHTTPServer(("0.0.0.0" if LAN else "127.0.0.1", porta), Handler).serve_forever()


if __name__ == "__main__":
    main()
