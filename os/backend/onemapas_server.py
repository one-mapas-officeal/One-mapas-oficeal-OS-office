#!/usr/bin/env python3
"""
OneMapas OS - servidor web (roda no Render, em https://onemapasoficeal.com.br).

  /                 -> interface do OneMapas OS (área de trabalho no navegador)
  /<nome>/          -> site criado pelo usuário (pasta sites/<nome>/)
  /api/...          -> API

A API:
  GET    /api/status
  GET    /api/verificar?nome=oi            a URL https://onemapasoficeal.com.br/oi está disponível?
  POST   /api/criar                        {nome, titulo, descricao, cor}  (+ header X-Dono-Chave)
  GET    /api/meus-sites                   sites do dono (header X-Dono-Chave)
  GET    /api/sites/<nome>/arquivos        lista os arquivos (dono)
  GET    /api/sites/<nome>/arquivo?caminho=index.html
  PUT    /api/sites/<nome>/arquivo         {caminho, conteudo}  (dono)
  DELETE /api/sites/<nome>/arquivo?caminho=x.css               (dono)
  DELETE /api/sites/<nome>                 apaga o site (dono)
  GET    /api/manifest                     ISO/manifest.json
  GET    /api/novidades

Quem é o dono: ao criar o primeiro site, o navegador gera uma chave secreta aleatória
(fica no navegador). O servidor guarda só o hash SHA-256 dela. Quem tem a chave edita o site.

Persistência: o disco do Render é apagado a cada deploy, então cada mudança também é
gravada no GitHub (sites/<nome>/) usando a variável de ambiente GITHUB_TOKEN.
O próximo deploy já traz os sites de volta.

Só usa a biblioteca padrão do Python.
"""
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import shutil
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
WEBUI = AQUI.parent / "webui"
SITES = Path(os.environ.get("ONEMAPAS_SITES", RAIZ / "sites"))

DOMINIO = os.environ.get("ONEMAPAS_DOMINIO", "https://onemapasoficeal.com.br").rstrip("/")
REPO = os.environ.get("GITHUB_REPO", "one-mapas-officeal/One-mapas-oficeal-OS-office")
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
VERSAO = "0.2.0"

NOME_VALIDO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESERVADOS = {"api", "iso", "admin", "www", "assets", "static", "index", "sites", "os", "login",
              "favicon", "robots", "sitemap", "health", "status", "render"}
CAMINHO_VALIDO = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}/){0,3}[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}$")
EXT_PERMITIDAS = {".html", ".htm", ".css", ".js", ".json", ".txt", ".md", ".svg", ".xml"}
META = "onemapas-site.json"          # metadados (não é servido ao público)
MAX_ARQUIVO = 512 * 1024             # 512 KB por arquivo
MAX_ARQUIVOS = 50                    # por site
MAX_SITES_POR_DONO = 20
MAX_CRIACOES_POR_IP_HORA = 10

trava = threading.Lock()
criacoes_ip = {}  # ip -> [timestamps]


# ------------------------------------------------------------------ util
def agora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_chave(chave):
    return hashlib.sha256(("onemapas:" + chave).encode()).hexdigest()


def chave_valida(chave):
    return isinstance(chave, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", chave or "") is not None


def http(url, metodo="GET", dados=None, cabecalhos=None, timeout=10):
    req = urllib.request.Request(url, method=metodo, data=dados, headers=cabecalhos or {})
    req.add_header("User-Agent", "OneMapas-OS")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode()


def gh_headers(extra=None):
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = "Bearer " + TOKEN
    h.update(extra or {})
    return h


def ler_meta(nome):
    p = SITES / nome / META
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


def eh_dono(nome, chave):
    meta = ler_meta(nome)
    if not meta or not chave_valida(chave):
        return False
    return hmac.compare_digest(meta.get("dono_hash", ""), hash_chave(chave))


# ------------------------------------------------------------------ GitHub (fila em segundo plano)
fila_github = queue.Queue()
estado_github = {"pendentes": 0, "ultimo_erro": None, "ultimo_ok": None}


def _gh_caminho(caminho):
    return f"https://api.github.com/repos/{REPO}/contents/{urllib.parse.quote(caminho)}"


def _gh_sha(caminho):
    st, corpo = http(_gh_caminho(caminho) + f"?ref={BRANCH}", cabecalhos=gh_headers())
    if st == 200:
        try:
            return json.loads(corpo).get("sha")
        except Exception:
            return None
    return None


def trabalhador_github():
    while True:
        acao, caminho, conteudo, msg = fila_github.get()
        try:
            for tentativa in range(3):
                sha = _gh_sha(caminho)
                h = gh_headers({"Content-Type": "application/json"})
                if acao == "salvar":
                    corpo = {"message": msg, "content": base64.b64encode(conteudo).decode(), "branch": BRANCH}
                    if sha:
                        corpo["sha"] = sha
                    st, resp = http(_gh_caminho(caminho), "PUT", json.dumps(corpo).encode(), h, 20)
                else:  # apagar
                    if not sha:
                        st, resp = 200, b""
                    else:
                        corpo = {"message": msg, "sha": sha, "branch": BRANCH}
                        st, resp = http(_gh_caminho(caminho), "DELETE", json.dumps(corpo).encode(), h, 20)
                if st in (200, 201):
                    estado_github["ultimo_ok"] = agora()
                    break
                if st == 409 and tentativa < 2:  # conflito de sha: tenta de novo
                    time.sleep(1.5)
                    continue
                estado_github["ultimo_erro"] = f"{agora()} {caminho}: HTTP {st} {resp[:150].decode(errors='ignore')}"
                print("[github]", estado_github["ultimo_erro"], file=sys.stderr)
                break
        finally:
            estado_github["pendentes"] = fila_github.qsize()
            fila_github.task_done()


def gh_salvar(nome, rel, conteudo, msg):
    if TOKEN:
        fila_github.put(("salvar", f"sites/{nome}/{rel}", conteudo, msg))
        estado_github["pendentes"] = fila_github.qsize()


def gh_apagar(nome, rel, msg):
    if TOKEN:
        fila_github.put(("apagar", f"sites/{nome}/{rel}", None, msg))
        estado_github["pendentes"] = fila_github.qsize()


def gh_existe(nome):
    """Confere no GitHub (outra fonte da verdade, caso o servidor ainda não tenha o site)."""
    st, _ = http(_gh_caminho(f"sites/{nome}") + f"?ref={BRANCH}", cabecalhos=gh_headers(), timeout=6)
    return st  # 200 existe, 404 livre, outro = não deu para saber


# ------------------------------------------------------------------ regras dos sites
def verificar(nome):
    nome = (nome or "").strip().lower().strip("/")
    url = f"{DOMINIO}/{nome}"
    base = {"nome": nome, "url": url, "checagens": []}
    if not NOME_VALIDO.match(nome):
        return {**base, "disponivel": False, "resposta": "não",
                "mensagem": "Nome inválido: use letras minúsculas, números e hífen. Escolha outra URL"}
    if nome in RESERVADOS:
        return {**base, "disponivel": False, "resposta": "não", "mensagem": "Nome reservado. Escolha outra URL"}

    ocupado = (SITES / nome).exists()
    base["checagens"].append({"onde": "Servidor (Render)", "resultado": "já existe" if ocupado else "livre"})
    st = gh_existe(nome)
    if st == 200:
        ocupado = True
        base["checagens"].append({"onde": "GitHub", "resultado": "já existe"})
    else:
        base["checagens"].append({"onde": "GitHub", "resultado": "livre" if st == 404 else f"não verificado ({st})"})
    return {**base, "disponivel": not ocupado, "resposta": "não" if ocupado else "sim",
            "mensagem": "Escolha outra URL" if ocupado else f"A URL {url} está disponível!"}


def modelo(titulo, descricao, cor):
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
  <footer>Feito com o OneMapas OS</footer>
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


def limite_ip(ip):
    t = time.time()
    lst = [x for x in criacoes_ip.get(ip, []) if t - x < 3600]
    criacoes_ip[ip] = lst
    if len(lst) >= MAX_CRIACOES_POR_IP_HORA:
        return False
    lst.append(t)
    return True


def sites_do_dono(chave):
    if not chave_valida(chave):
        return []
    h = hash_chave(chave)
    res = []
    if SITES.exists():
        for p in sorted(SITES.iterdir()):
            m = ler_meta(p.name) if p.is_dir() else None
            if m and hmac.compare_digest(m.get("dono_hash", ""), h):
                res.append({"nome": p.name, "url": f"{DOMINIO}/{p.name}", "titulo": m.get("titulo", p.name),
                            "criado_em": m.get("criado_em"), "atualizado_em": m.get("atualizado_em")})
    return res


def criar(dados, chave, ip):
    if not chave_valida(chave):
        return 400, {"mensagem": "Chave de dono inválida."}
    with trava:
        v = verificar(dados.get("nome", ""))
        if not v["disponivel"]:
            return 409, v
        if len(sites_do_dono(chave)) >= MAX_SITES_POR_DONO:
            return 429, {"mensagem": f"Limite de {MAX_SITES_POR_DONO} sites por dono."}
        if not limite_ip(ip):
            return 429, {"mensagem": "Muitos sites criados na última hora. Tente mais tarde."}
        nome = v["nome"]
        cor = dados.get("cor") if re.match(r"^#[0-9a-fA-F]{6}$", str(dados.get("cor", ""))) else "#2563eb"
        titulo = str(dados.get("titulo") or nome)[:120]
        html, css = modelo(titulo, str(dados.get("descricao") or "Bem-vindo ao meu site!")[:2000], cor)
        pasta = SITES / nome
        pasta.mkdir(parents=True)
        meta = {"nome": nome, "titulo": titulo, "dono_hash": hash_chave(chave),
                "criado_em": agora(), "atualizado_em": agora()}
        arquivos = {"index.html": html.encode(), "style.css": css.encode(),
                    META: json.dumps(meta, indent=2, ensure_ascii=False).encode()}
        for rel, c in arquivos.items():
            (pasta / rel).write_bytes(c)
    for rel, c in arquivos.items():
        gh_salvar(nome, rel, c, f"Site {nome}: criar {rel}")
    return 201, {**v, "mensagem": "Site criado!", "salvo_no_github": bool(TOKEN)}


def listar_arquivos(nome):
    pasta = SITES / nome
    res = []
    for f in sorted(pasta.rglob("*")):
        if f.is_file() and f.name != META:
            res.append({"caminho": f.relative_to(pasta).as_posix(), "tamanho": f.stat().st_size})
    return res


def caminho_seguro(nome, caminho):
    if not CAMINHO_VALIDO.match(caminho or "") or ".." in caminho or caminho == META:
        return None
    if Path(caminho).suffix.lower() not in EXT_PERMITIDAS:
        return None
    alvo = (SITES / nome / caminho).resolve()
    if not str(alvo).startswith(str((SITES / nome).resolve()) + os.sep):
        return None
    return alvo


def tocar_meta(nome):
    m = ler_meta(nome)
    m["atualizado_em"] = agora()
    c = json.dumps(m, indent=2, ensure_ascii=False).encode()
    (SITES / nome / META).write_bytes(c)
    gh_salvar(nome, META, c, f"Site {nome}: atualizar metadados")


# ------------------------------------------------------------------ ISO / novidades
def json_do_repo(caminho):
    """Lê do GitHub (sempre atualizado) e, se não der, usa a cópia do deploy."""
    st, corpo = http(_gh_caminho(caminho) + f"?ref={BRANCH}",
                     cabecalhos=gh_headers({"Accept": "application/vnd.github.raw+json"}), timeout=6)
    if st == 200:
        try:
            return {"origem": "github", **json.loads(corpo)}
        except Exception:
            pass
    local = RAIZ / caminho
    if local.exists():
        return {"origem": "servidor", **json.loads(local.read_text("utf-8"))}
    return {"erro": "Não encontrei " + caminho}


# ------------------------------------------------------------------ HTTP
CSP_SITES = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"


class Handler(BaseHTTPRequestHandler):
    server_version = "OneMapasOS/" + VERSAO
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[onemapas] %s %s\n" % (self.ip(), fmt % args))

    def ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def chave(self):
        return self.headers.get("X-Dono-Chave", "")

    def enviar(self, codigo, obj, extra=None):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def arquivo(self, alvo, csp=None, cache="no-cache"):
        corpo = alvo.read_bytes()
        tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript", "application/json", "image/svg+xml"):
            tipo += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def redirecionar(self, para):
        self.send_response(301)
        self.send_header("Location", para)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def pagina_404(self):
        corpo = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                 "<title>Não encontrado</title><body style='font-family:system-ui;text-align:center;padding:60px 16px'>"
                 "<h1>404</h1><p>Esse site ainda não existe.</p><p><a href='/'>Criar no OneMapas OS</a></p>").encode()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def corpo_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_ARQUIVO * 2:
            return None
        try:
            return json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            return {}

    # --------------------------------------------------------------- GET
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        partes = [p for p in u.path.split("/") if p]

        if u.path == "/":
            return self.arquivo(WEBUI / "index.html")
        if partes and partes[0] == "_os" and len(partes) == 2:
            alvo = WEBUI / partes[1]
            if alvo.is_file():
                return self.arquivo(alvo)
            return self.pagina_404()

        if partes and partes[0] == "api":
            rota = "/" + "/".join(partes[1:])
            if rota == "/status":
                total = sum(1 for p in SITES.iterdir() if p.is_dir()) if SITES.exists() else 0
                return self.enviar(200, {"ok": True, "versao": VERSAO, "dominio": DOMINIO, "sites": total,
                                         "github": {"configurado": bool(TOKEN), "repo": REPO, "branch": BRANCH,
                                                    **estado_github}})
            if rota == "/verificar":
                return self.enviar(200, verificar(q.get("nome", [""])[0]))
            if rota == "/meus-sites":
                return self.enviar(200, {"sites": sites_do_dono(self.chave())})
            if rota == "/manifest":
                return self.enviar(200, json_do_repo("ISO/manifest.json"))
            if rota == "/novidades":
                return self.enviar(200, json_do_repo("os/webui/novidades.json"))
            m = re.fullmatch(r"/sites/([a-z0-9-]+)/(arquivos|arquivo)", rota)
            if m:
                nome = m.group(1)
                if not eh_dono(nome, self.chave()):
                    return self.enviar(403, {"mensagem": "Só o dono pode ver os arquivos."})
                if m.group(2) == "arquivos":
                    return self.enviar(200, {"arquivos": listar_arquivos(nome)})
                alvo = caminho_seguro(nome, q.get("caminho", [""])[0])
                if not alvo or not alvo.is_file():
                    return self.enviar(404, {"mensagem": "Arquivo não encontrado."})
                return self.enviar(200, {"conteudo": alvo.read_text("utf-8", errors="replace")})
            return self.enviar(404, {"mensagem": "rota desconhecida"})

        # ---- sites dos usuários: /<nome>/...
        if partes and NOME_VALIDO.match(partes[0]) and (SITES / partes[0]).is_dir():
            nome = partes[0]
            if len(partes) == 1 and not u.path.endswith("/"):
                return self.redirecionar(f"/{nome}/")
            rel = "/".join(partes[1:]) or "index.html"
            if rel.split("/")[-1] == META:
                return self.pagina_404()
            alvo = (SITES / nome / rel).resolve()
            if alvo.is_dir():
                alvo = alvo / "index.html"
            if str(alvo).startswith(str((SITES / nome).resolve()) + os.sep) and alvo.is_file():
                # sandbox: o site do usuário não acessa os dados do OneMapas OS no mesmo domínio
                return self.arquivo(alvo, csp=CSP_SITES)
        return self.pagina_404()

    # --------------------------------------------------------------- POST / PUT / DELETE
    def do_POST(self):
        dados = self.corpo_json()
        if dados is None:
            return self.enviar(413, {"mensagem": "Muito grande."})
        if self.path.split("?")[0] == "/api/criar":
            return self.enviar(*criar(dados, self.chave(), self.ip()))
        return self.enviar(404, {"mensagem": "rota desconhecida"})

    def do_PUT(self):
        dados = self.corpo_json()
        if dados is None:
            return self.enviar(413, {"mensagem": "Arquivo muito grande (máx. 512 KB)."})
        m = re.fullmatch(r"/api/sites/([a-z0-9-]+)/arquivo", self.path.split("?")[0])
        if not m:
            return self.enviar(404, {"mensagem": "rota desconhecida"})
        nome = m.group(1)
        if not eh_dono(nome, self.chave()):
            return self.enviar(403, {"mensagem": "Só o dono pode editar."})
        alvo = caminho_seguro(nome, str(dados.get("caminho", "")))
        if not alvo:
            return self.enviar(400, {"mensagem": "Nome de arquivo inválido. Use .html, .css, .js, .json, .txt, .md, .svg ou .xml"})
        conteudo = str(dados.get("conteudo", "")).encode()
        if len(conteudo) > MAX_ARQUIVO:
            return self.enviar(413, {"mensagem": "Arquivo muito grande (máx. 512 KB)."})
        if not alvo.exists() and len(listar_arquivos(nome)) >= MAX_ARQUIVOS:
            return self.enviar(429, {"mensagem": f"Limite de {MAX_ARQUIVOS} arquivos por site."})
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(conteudo)
        rel = alvo.relative_to(SITES / nome).as_posix()
        gh_salvar(nome, rel, conteudo, f"Site {nome}: editar {rel}")
        tocar_meta(nome)
        return self.enviar(200, {"ok": True, "mensagem": f"{rel} salvo."})

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        m = re.fullmatch(r"/api/sites/([a-z0-9-]+)(/arquivo)?", u.path)
        if not m:
            return self.enviar(404, {"mensagem": "rota desconhecida"})
        nome = m.group(1)
        if not eh_dono(nome, self.chave()):
            return self.enviar(403, {"mensagem": "Só o dono pode apagar."})
        if m.group(2):
            alvo = caminho_seguro(nome, q.get("caminho", [""])[0])
            if not alvo or not alvo.is_file():
                return self.enviar(404, {"mensagem": "Arquivo não encontrado."})
            rel = alvo.relative_to(SITES / nome).as_posix()
            alvo.unlink()
            gh_apagar(nome, rel, f"Site {nome}: apagar {rel}")
            tocar_meta(nome)
            return self.enviar(200, {"ok": True})
        arquivos = [f.relative_to(SITES / nome).as_posix() for f in (SITES / nome).rglob("*") if f.is_file()]
        shutil.rmtree(SITES / nome)
        for rel in arquivos:
            gh_apagar(nome, rel, f"Site {nome}: apagar site")
        return self.enviar(200, {"ok": True, "mensagem": f"Site {nome} apagado."})


def main():
    SITES.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=trabalhador_github, daemon=True).start()
    porta = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"OneMapas OS {VERSAO} em http://{host}:{porta}  domínio={DOMINIO}  "
          f"github={'ligado' if TOKEN else 'DESLIGADO (defina GITHUB_TOKEN)'}", flush=True)
    ThreadingHTTPServer((host, porta), Handler).serve_forever()


if __name__ == "__main__":
    main()
