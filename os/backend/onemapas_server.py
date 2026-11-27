#!/usr/bin/env python3
"""
OneMapas OS - servidor local (backend da interface gráfica).

Roda no próprio PC (o PC é o dono do site). Só usa a biblioteca padrão do Python.

  python3 onemapas_server.py            -> abre em http://127.0.0.1:8080

Funções:
  GET  /api/verificar?nome=oi   -> a URL https://onemapasoficeal.com.br/oi está disponível?
  POST /api/criar               -> cria a pasta do site na Área de Trabalho (+ publica no GitHub)
  GET  /api/sites               -> lista os sites deste PC
  GET  /api/rede                -> estado da rede (NetworkManager / nmcli)
  POST /api/rede/wifi           -> conecta em uma rede Wi-Fi
  GET  /api/manifest            -> ISO/manifest.json (GitHub, com cópia local de reserva)
  GET  /api/novidades           -> novidades do sistema
  GET/POST /api/config          -> configurações (token do GitHub, repositório, domínio)
"""
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ_REPO = AQUI.parent.parent  # quando roda de dentro do repositório
WEBUI = Path(os.environ.get("ONEMAPAS_WEBUI", AQUI.parent / "webui"))
CONFIG_DIR = Path.home() / ".config" / "onemapas"
CONFIG_FILE = CONFIG_DIR / "config.json"

PADRAO = {
    "dominio": "https://onemapasoficeal.com.br",
    "repositorio": "one-mapas-officeal/One-mapas-oficeal-OS-office",
    "branch": "main",
    "pasta_sites": "sites",
    "github_token": "",
    "dono": socket.gethostname(),
}

NOME_VALIDO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESERVADOS = {"api", "iso", "admin", "www", "assets", "static", "index", "sites", "os", "login"}


# ----------------------------------------------------------------- utilidades
def carregar_config():
    cfg = dict(PADRAO)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text("utf-8")))
        except Exception:
            pass
    if os.environ.get("GITHUB_TOKEN"):
        cfg["github_token"] = os.environ["GITHUB_TOKEN"]
    return cfg


def salvar_config(novo):
    cfg = carregar_config()
    for k in PADRAO:
        if k in novo and novo[k] is not None:
            cfg[k] = str(novo[k]).strip()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    os.chmod(CONFIG_FILE, 0o600)
    return cfg


def area_de_trabalho():
    """Descobre a Área de Trabalho do usuário (Linux: xdg-user-dir; senão ~/Desktop)."""
    try:
        p = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=3)
        caminho = p.stdout.strip()
        if caminho and caminho != str(Path.home()):
            return Path(caminho)
    except Exception:
        pass
    for nome in ("Área de Trabalho", "Desktop"):
        if (Path.home() / nome).is_dir():
            return Path.home() / nome
    return Path.home() / "Área de Trabalho"


def pasta_dos_sites():
    p = area_de_trabalho() / "Meus Sites OneMapas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def http(url, metodo="GET", dados=None, cabecalhos=None, timeout=8):
    req = urllib.request.Request(url, method=metodo, data=dados, headers=cabecalhos or {})
    req.add_header("User-Agent", "OneMapas-OS")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # sem rede, DNS etc.
        return None, str(e).encode()


def gh_headers(cfg):
    h = {"Accept": "application/vnd.github+json"}
    if cfg.get("github_token"):
        h["Authorization"] = "Bearer " + cfg["github_token"]
    return h


# ----------------------------------------------------------------- regras do site
def verificar(nome):
    """Responde se https://<dominio>/<nome> está disponível."""
    cfg = carregar_config()
    nome = (nome or "").strip().lower().strip("/")
    url = f"{cfg['dominio'].rstrip('/')}/{nome}"
    checagens = []

    if not NOME_VALIDO.match(nome):
        return {"nome": nome, "url": url, "disponivel": False, "resposta": "não",
                "mensagem": "Nome inválido. Use só letras minúsculas, números e hífen. Escolha outra URL.",
                "checagens": checagens}
    if nome in RESERVADOS:
        return {"nome": nome, "url": url, "disponivel": False, "resposta": "não",
                "mensagem": "Esse nome é reservado pelo sistema. Escolha outra URL.", "checagens": checagens}

    ocupado = False

    # 1) O servidor (Render) já responde nessa URL?
    status, _ = http(url, timeout=6)
    if status is None:
        checagens.append({"onde": "Render (domínio)", "resultado": "sem resposta (offline ou DNS)"})
    elif status == 404:
        checagens.append({"onde": "Render (domínio)", "resultado": "404, livre"})
    elif 200 <= status < 400:
        checagens.append({"onde": "Render (domínio)", "resultado": f"{status}, já existe"})
        ocupado = True
    else:
        checagens.append({"onde": "Render (domínio)", "resultado": f"HTTP {status}"})

    # 2) Já existe a pasta sites/<nome> no GitHub?
    api = f"https://api.github.com/repos/{cfg['repositorio']}/contents/{cfg['pasta_sites']}/{nome}?ref={cfg['branch']}"
    status, _ = http(api, cabecalhos=gh_headers(cfg))
    if status == 200:
        checagens.append({"onde": "GitHub", "resultado": "pasta já existe"})
        ocupado = True
    elif status == 404:
        checagens.append({"onde": "GitHub", "resultado": "livre"})
    else:
        checagens.append({"onde": "GitHub", "resultado": f"não verificado ({status})"})

    # 3) Já existe neste PC?
    if (pasta_dos_sites() / nome).exists():
        checagens.append({"onde": "Este PC", "resultado": "já existe na Área de Trabalho"})
        ocupado = True
    else:
        checagens.append({"onde": "Este PC", "resultado": "livre"})

    return {
        "nome": nome, "url": url, "disponivel": not ocupado,
        "resposta": "não" if ocupado else "sim",
        "mensagem": "Escolha outra URL" if ocupado else f"A URL {url} está disponível!",
        "checagens": checagens,
    }


def modelo_html(titulo, descricao, cor, dono):
    titulo_e = titulo.replace("<", "&lt;")
    descricao_e = descricao.replace("<", "&lt;")
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo_e}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
  <header><h1>{titulo_e}</h1></header>
  <main>
    <p>{descricao_e}</p>
    <p class="nota">Site criado com o OneMapas OS.</p>
  </main>
  <footer>Dono: {dono} · {datetime.now():%d/%m/%Y}</footer>
</body>
</html>
""", f""":root {{ --cor: {cor}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: system-ui, sans-serif; background: #f6f7f9; color: #1d2330; }}
header {{ background: var(--cor); color: #fff; padding: 48px 16px; text-align: center; }}
main {{ max-width: 760px; margin: 32px auto; padding: 0 16px; font-size: 1.1rem; line-height: 1.6; }}
.nota {{ color: #667; font-size: .9rem; }}
footer {{ text-align: center; color: #889; padding: 24px; font-size: .85rem; }}
"""


def publicar_github(cfg, nome, arquivos):
    """Envia os arquivos para <repo>/<pasta_sites>/<nome>/ pela API do GitHub."""
    if not cfg.get("github_token"):
        return {"ok": False, "mensagem": "Sem token do GitHub. Configure em Configurações para publicar."}
    resultados = []
    for rel, conteudo in arquivos.items():
        caminho = f"{cfg['pasta_sites']}/{nome}/{rel}"
        api = f"https://api.github.com/repos/{cfg['repositorio']}/contents/{urllib.parse.quote(caminho)}"
        sha = None
        st, corpo = http(api + f"?ref={cfg['branch']}", cabecalhos=gh_headers(cfg))
        if st == 200:
            sha = json.loads(corpo).get("sha")
        payload = {"message": f"Site {nome}: {rel} (OneMapas OS, dono {cfg['dono']})",
                   "content": base64.b64encode(conteudo).decode(), "branch": cfg["branch"]}
        if sha:
            payload["sha"] = sha
        h = gh_headers(cfg)
        h["Content-Type"] = "application/json"
        st, corpo = http(api, "PUT", json.dumps(payload).encode(), h, timeout=20)
        resultados.append({"arquivo": rel, "status": st})
        if st not in (200, 201):
            return {"ok": False, "mensagem": f"GitHub recusou {rel} (HTTP {st}): {corpo[:200].decode(errors='ignore')}",
                    "resultados": resultados}
    return {"ok": True, "mensagem": "Publicado no GitHub. O Render faz o deploy sozinho em alguns minutos.",
            "resultados": resultados}


def criar(dados):
    cfg = carregar_config()
    v = verificar(dados.get("nome", ""))
    if not v["disponivel"]:
        return 409, v
    nome = v["nome"]
    titulo = dados.get("titulo") or nome
    descricao = dados.get("descricao") or "Bem-vindo ao meu site!"
    cor = dados.get("cor") or "#2563eb"
    if not re.match(r"^#[0-9a-fA-F]{6}$", cor):
        cor = "#2563eb"
    html, css = modelo_html(titulo, descricao, cor, cfg["dono"])

    pasta = pasta_dos_sites() / nome
    pasta.mkdir(parents=True)
    (pasta / "index.html").write_text(html, "utf-8")
    (pasta / "style.css").write_text(css, "utf-8")
    info = {"nome": nome, "url": v["url"], "dono": cfg["dono"], "criado_em": datetime.now().isoformat(timespec="seconds")}
    (pasta / "onemapas-site.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), "utf-8")

    resposta = {**v, "pasta": str(pasta), "mensagem": f"Site criado em {pasta}"}
    if dados.get("publicar"):
        arquivos = {"index.html": html.encode(), "style.css": css.encode(),
                    "onemapas-site.json": json.dumps(info, indent=2, ensure_ascii=False).encode()}
        resposta["publicacao"] = publicar_github(cfg, nome, arquivos)
    return 201, resposta


def listar_sites():
    base = pasta_dos_sites()
    sites = []
    for p in sorted(base.iterdir()):
        if p.is_dir():
            meta = {}
            if (p / "onemapas-site.json").exists():
                try:
                    meta = json.loads((p / "onemapas-site.json").read_text("utf-8"))
                except Exception:
                    pass
            sites.append({"nome": p.name, "pasta": str(p), **meta,
                          "arquivos": sorted(f.name for f in p.iterdir())})
    return {"pasta": str(base), "sites": sites}


def publicar_existente(nome):
    cfg = carregar_config()
    pasta = pasta_dos_sites() / nome
    if not NOME_VALIDO.match(nome) or not pasta.is_dir():
        return 404, {"ok": False, "mensagem": "Site não encontrado neste PC."}
    arquivos = {}
    for f in pasta.rglob("*"):
        if f.is_file() and f.stat().st_size < 5_000_000:
            arquivos[f.relative_to(pasta).as_posix()] = f.read_bytes()
    return 200, publicar_github(cfg, nome, arquivos)


# ----------------------------------------------------------------- rede
def rede():
    info = {"nmcli": bool(shutil.which("nmcli")), "dispositivos": [], "wifi": [], "internet": False}
    st, _ = http("https://api.github.com", timeout=4)
    info["internet"] = st is not None
    if info["nmcli"]:
        p = subprocess.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
                           capture_output=True, text=True)
        for linha in p.stdout.splitlines():
            d = linha.split(":")
            if len(d) >= 4:
                info["dispositivos"].append({"dispositivo": d[0], "tipo": d[1], "estado": d[2], "conexao": d[3]})
        p = subprocess.run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list"],
                           capture_output=True, text=True)
        for linha in p.stdout.splitlines():
            d = linha.split(":")
            if len(d) >= 4 and d[0]:
                info["wifi"].append({"ssid": d[0], "sinal": d[1], "seguranca": d[2], "em_uso": d[3] == "*"})
    else:
        # sem NetworkManager: mostra as interfaces do kernel
        for iface in sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else []:
            try:
                estado = Path(f"/sys/class/net/{iface}/operstate").read_text().strip()
            except Exception:
                estado = "?"
            info["dispositivos"].append({"dispositivo": iface, "tipo": "?", "estado": estado, "conexao": ""})
    return info


def conectar_wifi(dados):
    if not shutil.which("nmcli"):
        return 400, {"ok": False, "mensagem": "NetworkManager (nmcli) não está instalado."}
    cmd = ["nmcli", "device", "wifi", "connect", str(dados.get("ssid", ""))]
    if dados.get("senha"):
        cmd += ["password", str(dados["senha"])]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    return (200 if p.returncode == 0 else 400), {"ok": p.returncode == 0, "mensagem": (p.stdout or p.stderr).strip()}


# ----------------------------------------------------------------- ISO / novidades
def json_remoto_ou_local(caminho_repo):
    cfg = carregar_config()
    url = f"https://raw.githubusercontent.com/{cfg['repositorio']}/{cfg['branch']}/{caminho_repo}"
    st, corpo = http(url, timeout=5)
    if st == 200:
        try:
            return {"origem": "github", **json.loads(corpo)}
        except Exception:
            pass
    for local in (RAIZ_REPO / caminho_repo, Path("/usr/share/onemapas") / caminho_repo):
        if local.exists():
            return {"origem": "local", **json.loads(local.read_text("utf-8"))}
    return {"origem": "nenhuma", "erro": "Não encontrei " + caminho_repo}


# ----------------------------------------------------------------- HTTP
TIPOS = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript",
         ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "OneMapasOS/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[onemapas] " + fmt % args + "\n")

    def enviar(self, codigo, obj):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def corpo_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/ping":
            return self.enviar(200, {"ok": True, "dono": carregar_config()["dono"]})
        if u.path == "/api/verificar":
            return self.enviar(200, verificar(q.get("nome", [""])[0]))
        if u.path == "/api/sites":
            return self.enviar(200, listar_sites())
        if u.path == "/api/rede":
            return self.enviar(200, rede())
        if u.path == "/api/manifest":
            return self.enviar(200, json_remoto_ou_local("ISO/manifest.json"))
        if u.path == "/api/novidades":
            return self.enviar(200, json_remoto_ou_local("os/webui/novidades.json"))
        if u.path == "/api/config":
            cfg = carregar_config()
            cfg["github_token"] = "definido" if cfg["github_token"] else ""
            cfg["area_de_trabalho"] = str(pasta_dos_sites())
            return self.enviar(200, cfg)
        return self.arquivo_estatico(u.path)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        dados = self.corpo_json()
        if u.path == "/api/criar":
            return self.enviar(*criar(dados))
        if u.path == "/api/publicar":
            return self.enviar(*publicar_existente(str(dados.get("nome", ""))))
        if u.path == "/api/rede/wifi":
            return self.enviar(*conectar_wifi(dados))
        if u.path == "/api/config":
            salvar_config(dados)
            return self.enviar(200, {"ok": True})
        if u.path == "/api/abrir-pasta":
            alvo = pasta_dos_sites() / str(dados.get("nome", ""))
            if alvo.exists() and shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(alvo)])
                return self.enviar(200, {"ok": True})
            return self.enviar(404, {"ok": False})
        return self.enviar(404, {"erro": "rota desconhecida"})

    def arquivo_estatico(self, caminho):
        if caminho in ("", "/"):
            caminho = "/index.html"
        alvo = (WEBUI / caminho.lstrip("/")).resolve()
        if not str(alvo).startswith(str(WEBUI.resolve())) or not alvo.is_file():
            return self.enviar(404, {"erro": "não encontrado"})
        corpo = alvo.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TIPOS.get(alvo.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def main():
    porta = int(os.environ.get("ONEMAPAS_PORTA", "8080"))
    srv = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    print(f"OneMapas OS rodando em http://127.0.0.1:{porta}  (sites em: {pasta_dos_sites()})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
