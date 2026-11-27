#!/usr/bin/env python3
"""
OneMapas OS - PC (o servidor onde o site roda).

  python3 onemapas_pc.py      -> interface em http://127.0.0.1:8080

O que ele faz:
  1. Interface gráfica (área de trabalho estilo Windows / Mint / Mac) só para este PC.
  2. Cria os sites numa pasta da Área de Trabalho: "Meus Sites OneMapas/<nome>/".
  3. Liga o PC ao Render por um túnel: o PC busca os pedidos dos visitantes no Render,
     responde com os arquivos da pasta do site, e o Render entrega ao visitante.
        Cliente -> Render -> PC -> Render -> Cliente
     Funciona atrás de roteador/CGNAT, sem abrir portas.

Este PC é o dono dos sites: ele tem uma chave secreta (~/.config/onemapas/config.json),
e o Render guarda só o hash dela.

Só usa a biblioteca padrão do Python.
"""
import base64
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
VERSAO = "0.3.0"

PADRAO = {
    "servidor": os.environ.get("ONEMAPAS_SERVIDOR", "https://onemapasos.oficeal.com"),
    "dono": socket.gethostname(),
    "chave": "",
}
NOME_VALIDO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
CAMINHO_VALIDO = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}/){0,5}[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}$")
META = "onemapas-site.json"
MAX_ARQUIVO_EDITOR = 1024 * 1024
TRABALHADORES_TUNEL = 4

estado = {"tunel": "desligado", "ultimo_erro": None, "pedidos_atendidos": 0, "ultimo_pedido": None}


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
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", cfg.get("chave") or ""):
            cfg["chave"] = secrets.token_urlsafe(32)
            _gravar(cfg)
        return cfg


def _gravar(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    os.chmod(CONFIG_FILE, 0o600)


def salvar_config(novo):
    cfg = config()
    with _trava_cfg:
        for k in ("servidor", "dono", "chave"):
            v = str(novo.get(k) or "").strip()
            if not v:
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


# ------------------------------------------------------------------ túnel: atende os visitantes
def servir_arquivo_local(nome, metodo, caminho):
    """Responde um pedido de visitante com os arquivos da pasta do site."""
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


def trabalhador_tunel(n):
    espera_erro = 2
    while True:
        st, ped = render("GET", "/_tunel/esperar", timeout=40)
        if st == 204:
            estado["tunel"] = "conectado"
            espera_erro = 2
            continue
        if st != 200:
            estado["tunel"] = "reconectando"
            estado["ultimo_erro"] = f"{agora()} {ped.get('mensagem', st)}"
            time.sleep(espera_erro)
            espera_erro = min(espera_erro * 2, 30)
            continue
        estado["tunel"] = "conectado"
        espera_erro = 2
        try:
            status, cab, corpo = servir_arquivo_local(ped["site"], ped["metodo"], ped["caminho"])
        except Exception as e:
            status, cab, corpo = 500, {"Content-Type": "text/plain"}, f"erro no PC: {e}".encode()
        render("POST", f"/_tunel/responder/{ped['id']}",
               {"status": status, "cabecalhos": cab, "corpo_b64": base64.b64encode(corpo).decode()}, timeout=30)
        estado["pedidos_atendidos"] += 1
        estado["ultimo_pedido"] = f"{agora()} {ped['metodo']} {ped['site']}{ped['caminho'][:80]} -> {status}"


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
            res.append({"nome": p.name, "url": m.get("url", ""), "titulo": m.get("titulo", p.name),
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
    meta = {"nome": nome, "titulo": titulo, "url": r.get("url") or v.get("url"), "dono": cfg["dono"],
            "criado_em": agora(), "atualizado_em": agora()}
    (pasta / META).write_text(json.dumps(meta, indent=2, ensure_ascii=False), "utf-8")
    return 201, {**v, "url": meta["url"], "pasta": str(pasta), "mensagem": "Site criado!"}


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

    def seguro(self):
        """Só aceita a própria interface: bloqueia outros sites (CSRF) e DNS rebinding."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        if self.command != "GET" and self.headers.get("X-OneMapas") != "1":
            return False
        return True

    def do_GET(self):
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
                                     "render_online": st == 200, "dominio_sites": r.get("dominio_sites", ""),
                                     "sites": len(meus_sites()), "tunel": estado, "pasta": str(pasta_sites())})
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
                                     "pasta": str(pasta_sites())})
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
        if not self.seguro():
            return self.enviar(403, {"mensagem": "proibido"})
        dados = self.corpo_json()
        if dados is None:
            return self.enviar(413, {"mensagem": "Muito grande."})
        if self.path == "/api/criar":
            return self.enviar(*criar(dados))
        if self.path == "/api/config":
            return self.enviar(200 if salvar_config(dados) else 400, {"ok": True})
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
    for i in range(TRABALHADORES_TUNEL):
        threading.Thread(target=trabalhador_tunel, args=(i,), daemon=True).start()
    porta = int(os.environ.get("ONEMAPAS_PORTA", "8080"))
    print(f"OneMapas OS {VERSAO} (PC) em http://127.0.0.1:{porta}\n  sites em: {pasta_sites()}\n"
          f"  Render: {cfg['servidor']}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", porta), Handler).serve_forever()


if __name__ == "__main__":
    main()
