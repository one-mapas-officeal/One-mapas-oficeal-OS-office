#!/usr/bin/env python3
"""
OneMapas OS - GATEWAY (roda no Render).

O site de cada pessoa roda NO PC DELA, e o visitante abre direto no PC:

    Visitante ──> http://IP-do-PC:8090/<nome>/      (o Render não entrega o site)

O Render só passa dados ao Supabase:
  - registro dos nomes (quem é dono de qual site);
  - endereço atual do PC (o IP muda): o PC manda um sinal a cada 5 minutos, o que também
    mantém o Render acordado; nesse sinal o Render testa se a porta do PC está aberta de fora;
  - cópia dos arquivos (backup no Supabase Storage).

O link com nome (https://<nome>.<DOMINIO_SITES>/ ou /_site/<nome>/) só REDIRECIONA para o PC.

Banco: Supabase (tabela public.sites = quem é dono de qual nome; bucket "sites" = cópia dos arquivos).
Variáveis: SUPABASE_URL e SUPABASE_SECRET_KEY (ou SUPABASE_SERVICE_ROLE_KEY). Sem elas, usa o disco (só para teste).

Sem DNS curinga (para testar no onrender.com): https://<servico>.onrender.com/_site/<nome>/

Rotas:
  GET  /api/status
  GET  /api/verificar?nome=oi
  POST /api/registrar           {nome, dono_nome}           (header X-Dono-Chave)
  DELETE /api/registro/<nome>                                (header X-Dono-Chave)
  PUT    /api/copia/<nome>?caminho=index.html  corpo = arquivo (header X-Dono-Chave)
  DELETE /api/copia/<nome>?caminho=index.html                 (header X-Dono-Chave)
  GET  /api/manifest  /api/novidades
  POST /api/pc/batida          {porta}  sinal de 5 em 5 min: atualiza o endereço do PC (header X-Dono-Chave)
  GET  /api/meu-ip             IP público de quem pergunta

Só usa a biblioteca padrão do Python.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import ipaddress
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent

DOMINIO_SITES = os.environ.get("ONEMAPAS_DOMINIO_SITES", "onemapasos.oficeal.com").lower().strip(".")
REPO = os.environ.get("GITHUB_REPO", "one-mapas-officeal/One-mapas-oficeal-OS-office")
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
TOKEN = os.environ.get("GITHUB_TOKEN", "")  # opcional: só para ler ISO/manifest.json se o repositório for privado
VERSAO = "0.5.0"

PARAMS_SITE = "sistema=onemapasoficealOS&site=true"
NOME_VALIDO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESERVADOS = {"api", "iso", "admin", "www", "assets", "static", "index", "sites", "os", "login", "mail",
              "smtp", "ftp", "ns1", "ns2", "status", "render", "app", "painel", "suporte", "onemapas"}
PC_ONLINE_SE = 12 * 60     # PC "ligado" se mandou sinal nos últimos 12 min (ele manda a cada 5)
MAX_SITES_POR_DONO = 20
MAX_REGISTROS_POR_IP_HORA = 10


def agora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_chave(chave):
    return hashlib.sha256(("onemapas:" + chave).encode()).hexdigest()


def chave_valida(chave):
    return isinstance(chave, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", chave or "") is not None


# Enquanto o DNS curinga (*.onemapasos.oficeal.com) não existe, defina ONEMAPAS_URL_BASE
# (ex.: https://onemapas-gateway.onrender.com) e os sites ficam em <base>/_site/<nome>/
URL_BASE = os.environ.get("ONEMAPAS_URL_BASE", "").rstrip("/")


def url_site(nome, load="em-espera"):
    if URL_BASE:
        return f"{URL_BASE}/_site/{nome}/?{PARAMS_SITE}&load={load}"
    return f"https://{nome}.{DOMINIO_SITES}/?{PARAMS_SITE}&load={load}"


def http(url, metodo="GET", dados=None, cabecalhos=None, timeout=10):
    req = urllib.request.Request(url, method=metodo, data=dados, headers=cabecalhos or {})
    req.add_header("User-Agent", "OneMapas-Gateway")
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


def gh_url(caminho):
    return f"https://api.github.com/repos/{REPO}/contents/{urllib.parse.quote(caminho)}"


# ------------------------------------------------------------------ armazenamento: Supabase (ou disco, para testes)
# Tabela public.sites  -> qual nome pertence a qual PC (hash da chave)
# Bucket  "sites"      -> cópia dos arquivos de cada site (usada quando o PC está desligado)
# A chave secreta do Supabase fica só aqui no Render; os PCs nunca a recebem.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DADOS_LOCAIS = Path(os.environ.get("ONEMAPAS_DADOS", RAIZ / ".dados-gateway"))
BUCKET = "sites"
MAX_ARQUIVO_COPIA = 5 * 1024 * 1024
CAMINHO_VALIDO = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}/){0,5}[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}$")


def caminho_ok(c):
    return bool(CAMINHO_VALIDO.match(c or "")) and ".." not in c and c != "onemapas-site.json"


class Supabase:
    nome = "supabase"

    def _h(self, extra=None):
        h = {"apikey": SUPABASE_KEY}
        if SUPABASE_KEY.startswith("eyJ"):  # chave antiga (JWT service_role)
            h["Authorization"] = "Bearer " + SUPABASE_KEY
        h.update(extra or {})
        return h

    def _q(self, nome):
        return urllib.parse.quote(nome, safe="")

    def listar(self):
        st, c = http(f"{SUPABASE_URL}/rest/v1/sites?select=nome,dono_hash,dono_nome,criado_em,copia_em,pc_endereco,visto_em,porta_aberta",
                     cabecalhos=self._h())
        if st != 200:
            raise RuntimeError(f"Supabase listar: HTTP {st} {c[:200]!r}")
        return {d["nome"]: d for d in json.loads(c)}

    def inserir(self, d):
        st, c = http(f"{SUPABASE_URL}/rest/v1/sites", "POST", json.dumps(d).encode(),
                     self._h({"Content-Type": "application/json", "Prefer": "return=minimal"}))
        if st == 201:
            return True
        if st == 409:
            return False
        raise RuntimeError(f"Supabase inserir: HTTP {st} {c[:200]!r}")

    def apagar(self, nome):
        http(f"{SUPABASE_URL}/rest/v1/sites?nome=eq.{self._q(nome)}", "DELETE", cabecalhos=self._h())

    def marcar_copia(self, nome, quando):
        http(f"{SUPABASE_URL}/rest/v1/sites?nome=eq.{self._q(nome)}", "PATCH",
             json.dumps({"copia_em": quando}).encode(),
             self._h({"Content-Type": "application/json", "Prefer": "return=minimal"}))

    def batida(self, dono_hash, endereco, aberta, quando):
        st, c = http(f"{SUPABASE_URL}/rest/v1/sites?dono_hash=eq.{self._q(dono_hash)}", "PATCH",
                     json.dumps({"pc_endereco": endereco, "porta_aberta": aberta, "visto_em": quando}).encode(),
                     self._h({"Content-Type": "application/json", "Prefer": "return=minimal"}))
        if st not in (200, 204):
            raise RuntimeError(f"Supabase batida: HTTP {st} {c[:200]!r}")

    def salvar_arquivo(self, nome, caminho, corpo, tipo):
        st, c = http(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{nome}/{urllib.parse.quote(caminho)}", "POST", corpo,
                     self._h({"Content-Type": tipo or "application/octet-stream", "x-upsert": "true",
                              "Cache-Control": "no-cache"}), timeout=30)
        if st not in (200, 201):
            raise RuntimeError(f"Supabase Storage: HTTP {st} {c[:200]!r}")

    def ler_arquivo(self, nome, caminho):
        url = f"{SUPABASE_URL}/storage/v1/object/authenticated/{BUCKET}/{nome}/{urllib.parse.quote(caminho)}"
        req = urllib.request.Request(url, headers={**self._h(), "User-Agent": "OneMapas-Gateway"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read(), r.headers.get("Content-Type")
        except Exception:
            return None

    def _listar_pasta(self, prefixo):
        st, c = http(f"{SUPABASE_URL}/storage/v1/object/list/{BUCKET}", "POST",
                     json.dumps({"prefix": prefixo, "limit": 1000}).encode(),
                     self._h({"Content-Type": "application/json"}))
        res = []
        if st == 200:
            for item in json.loads(c):
                caminho = f"{prefixo}/{item['name']}"
                res += [caminho] if item.get("id") else self._listar_pasta(caminho)
        return res

    def apagar_arquivos(self, nome, caminhos=None):
        alvos = [f"{nome}/{c}" for c in caminhos] if caminhos else self._listar_pasta(nome)
        for i in range(0, len(alvos), 500):
            http(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}", "DELETE",
                 json.dumps({"prefixes": alvos[i:i + 500]}).encode(), self._h({"Content-Type": "application/json"}))


class Local:
    """Mesma interface, no disco. Só para testar sem Supabase (no Render o disco é apagado a cada deploy)."""
    nome = "local (sem Supabase)"

    def __init__(self):
        (DADOS_LOCAIS / "registro").mkdir(parents=True, exist_ok=True)
        (DADOS_LOCAIS / "copias").mkdir(parents=True, exist_ok=True)

    def _r(self, nome):
        return DADOS_LOCAIS / "registro" / f"{nome}.json"

    def listar(self):
        res = {}
        for f in (DADOS_LOCAIS / "registro").glob("*.json"):
            d = json.loads(f.read_text("utf-8"))
            res[d["nome"]] = d
        return res

    def inserir(self, d):
        try:
            with open(self._r(d["nome"]), "x", encoding="utf-8") as f:
                json.dump({**d, "criado_em": agora(), "copia_em": None}, f)
            return True
        except FileExistsError:
            return False

    def apagar(self, nome):
        self._r(nome).unlink(missing_ok=True)

    def marcar_copia(self, nome, quando):
        if self._r(nome).exists():
            d = json.loads(self._r(nome).read_text("utf-8"))
            d["copia_em"] = quando
            self._r(nome).write_text(json.dumps(d), "utf-8")

    def batida(self, dono_hash, endereco, aberta, quando):
        for f in (DADOS_LOCAIS / "registro").glob("*.json"):
            d = json.loads(f.read_text("utf-8"))
            if d["dono_hash"] == dono_hash:
                d.update(pc_endereco=endereco, porta_aberta=aberta, visto_em=quando)
                f.write_text(json.dumps(d), "utf-8")

    def salvar_arquivo(self, nome, caminho, corpo, tipo):
        p = DADOS_LOCAIS / "copias" / nome / caminho
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(corpo)

    def ler_arquivo(self, nome, caminho):
        p = DADOS_LOCAIS / "copias" / nome / caminho
        return (p.read_bytes(), None) if p.is_file() else None

    def apagar_arquivos(self, nome, caminhos=None):
        base = DADOS_LOCAIS / "copias" / nome
        if caminhos is None:
            import shutil
            shutil.rmtree(base, ignore_errors=True)
        else:
            for c in caminhos:
                (base / c).unlink(missing_ok=True)


banco = Supabase() if SUPABASE_URL and SUPABASE_KEY else Local()
registro = {}            # cache em memória da tabela sites
trava_registro = threading.Lock()
registros_ip = {}
estado_banco = {"tipo": banco.nome, "ultimo_erro": None, "carregado_em": None}


def carregar_registro():
    try:
        novo = banco.listar()
        with trava_registro:
            registro.clear()
            registro.update(novo)
        estado_banco["carregado_em"] = agora()
    except Exception as e:
        estado_banco["ultimo_erro"] = f"{agora()} {e}"
        print("[banco]", e, file=sys.stderr, flush=True)


def recarregar_sempre():
    """Mantém o cache igual ao banco (caso o Render rode mais de uma instância)."""
    while True:
        time.sleep(60)
        carregar_registro()


def verificar(nome):
    nome = (nome or "").strip().lower()
    base = {"nome": nome, "url": url_site(nome), "dominio_sites": DOMINIO_SITES}
    if not NOME_VALIDO.match(nome):
        return {**base, "disponivel": False, "resposta": "não",
                "mensagem": "Nome inválido: use letras minúsculas, números e hífen. Escolha outra URL"}
    if nome in RESERVADOS:
        return {**base, "disponivel": False, "resposta": "não", "mensagem": "Nome reservado. Escolha outra URL"}
    ocupado = nome in registro
    return {**base, "disponivel": not ocupado, "resposta": "não" if ocupado else "sim",
            "mensagem": "Escolha outra URL" if ocupado else f"{url_site(nome)} está disponível!",
            "checagens": [{"onde": "Render + Supabase" if banco.nome == "supabase" else "Render",
                           "resultado": "já existe" if ocupado else "livre"}]}


def registrar(nome, chave, dono_nome, ip):
    if not chave_valida(chave):
        return 400, {"mensagem": "Chave do PC inválida."}
    h = hash_chave(chave)
    with trava_registro:
        v = verificar(nome)
        if not v["disponivel"]:
            return 409, v
        if sum(1 for d in registro.values() if d["dono_hash"] == h) >= MAX_SITES_POR_DONO:
            return 429, {"mensagem": f"Limite de {MAX_SITES_POR_DONO} sites por PC."}
        t = time.time()
        lst = [x for x in registros_ip.get(ip, []) if t - x < 3600]
        if len(lst) >= MAX_REGISTROS_POR_IP_HORA:
            return 429, {"mensagem": "Muitos sites criados na última hora. Tente mais tarde."}
        d = {"nome": v["nome"], "dono_hash": h, "dono_nome": str(dono_nome or "")[:60]}
        try:
            ok = banco.inserir(d)  # a chave primária no banco impede dois PCs com o mesmo nome
        except Exception as e:
            estado_banco["ultimo_erro"] = f"{agora()} {e}"
            return 503, {"mensagem": "Banco de dados indisponível. Tente de novo."}
        if not ok:
            carregar_registro()
            return 409, {**v, "disponivel": False, "resposta": "não", "mensagem": "Escolha outra URL"}
        registros_ip[ip] = lst + [t]
        registro[d["nome"]] = {**d, "criado_em": agora(), "copia_em": None}
    return 201, {**v, "disponivel": False, "mensagem": "Registrado!"}


def dono_de(nome, chave):
    d = registro.get(nome)
    return bool(d and chave_valida(chave) and hmac.compare_digest(d["dono_hash"], hash_chave(chave)))


def pc_ligado(d):
    v = d.get("visto_em")
    if not v:
        return False
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - t).total_seconds() < PC_ONLINE_SE


def link_do_site(d):
    return f"{d['pc_endereco']}/{d['nome']}/" if d.get("pc_endereco") else None


def ip_publico(ip):
    if os.environ.get("ONEMAPAS_TESTE_IP_PRIVADO") == "1":  # só para testar na mesma máquina
        return bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip or ""))
    try:
        return ipaddress.ip_address(ip).version == 4 and ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def testar_porta(ip, porta):
    """O Render tenta abrir http://IP:porta/ do PC (só o IP de quem pediu): a porta está aberta de fora?"""
    try:
        with socket.create_connection((ip, porta), timeout=5) as s:
            s.settimeout(5)
            s.sendall(b"HEAD /_onemapas HTTP/1.0\r\nHost: onemapas\r\n\r\n")
            return s.recv(64).startswith(b"HTTP/")
    except OSError:
        return False


def batida(chave, porta, ip):
    """Sinal do PC (a cada 5 min): grava o endereço atual do PC em todos os sites dele."""
    if not chave_valida(chave):
        return 400, {"mensagem": "Chave do PC inválida."}
    if not isinstance(porta, int) or not 1 <= porta <= 65535:
        return 400, {"mensagem": "Porta inválida."}
    h = hash_chave(chave)
    meus = [d for d in registro.values() if d["dono_hash"] == h]
    publico = ip_publico(ip)
    aberta = testar_porta(ip, porta) if publico else False
    endereco = f"http://{ip}:{porta}" if publico else None
    quando = agora()
    if meus:
        try:
            banco.batida(h, endereco, aberta, quando)
        except Exception as e:
            estado_banco["ultimo_erro"] = f"{agora()} {e}"
            return 503, {"mensagem": "Banco de dados indisponível."}
        with trava_registro:
            for d in meus:
                d.update(pc_endereco=endereco, porta_aberta=aberta, visto_em=quando)
    return 200, {"ip_publico": ip, "ip_e_publico": publico, "porta": porta, "porta_aberta": aberta,
                 "endereco": endereco, "sites": [{"nome": d["nome"], "link": link_do_site(d)} for d in meus]}


# ------------------------------------------------------------------ páginas do gateway
def pagina(titulo, corpo_html, extra_head=""):
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{titulo}</title>{extra_head}
<style>
:root{{--bg:#0b3d91;--txt:#fff;--card:rgba(255,255,255,.1)}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;
font-family:system-ui,"Segoe UI",sans-serif;background:linear-gradient(135deg,#0b3d91,#1565c0 45%,#42a5f5);color:var(--txt);text-align:center}}
.c{{max-width:560px;width:100%;background:var(--card);border:1px solid rgba(255,255,255,.25);border-radius:14px;padding:28px 20px}}
h1{{margin:0 0 8px}}p{{opacity:.9;line-height:1.5}}.mudo{{opacity:.7;font-size:.9rem}}
.giro{{width:42px;height:42px;margin:18px auto;border:4px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:g 1s linear infinite}}
@keyframes g{{to{{transform:rotate(360deg)}}}}
input{{padding:10px;border-radius:8px;border:0;width:100%;max-width:240px;font:inherit}}
button,.btn{{padding:10px 16px;border-radius:8px;border:0;background:#fff;color:#0b3d91;font:inherit;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block;margin:4px}}
.linha{{display:flex;gap:6px;justify-content:center;flex-wrap:wrap;align-items:center}}
</style></head><body><div class="c">{corpo_html}</div></body></html>"""


def pagina_portal():
    return pagina("OneMapas OS", f"""
<h1>OneMapas OS</h1>
<p>Crie um site que roda no seu próprio PC.<br>O visitante abre direto no PC: <b>http://IP-do-PC:8090/&lt;nome&gt;/</b></p>
<div class="linha"><input id="n" placeholder="nome do site" maxlength="63"><button id="b">Está disponível?</button></div>
<p id="r"></p>
<p class="mudo">Para criar o site, instale o OneMapas OS no PC (ISO) ou rode <code>onemapas_pc.py</code>.</p>
<p><a class="btn" id="iso" href="#" hidden>⬇ Baixar a ISO</a></p>
<script>
const n=document.getElementById("n"),r=document.getElementById("r");
n.oninput=()=>{{n.value=n.value.toLowerCase().replace(/[^a-z0-9-]/g,"");r.textContent=""}};
async function v(){{if(!n.value)return;const d=await (await fetch("/api/verificar?nome="+encodeURIComponent(n.value))).json();
 r.innerHTML=d.disponivel?"✅ SIM: o nome "+d.nome+" está disponível!":"❌ NÃO: Escolha outra URL";}}
document.getElementById("b").onclick=v;n.onkeydown=e=>{{if(e.key==="Enter")v()}};
fetch("/api/manifest").then(r=>r.json()).then(m=>{{const i=(m.isos||[]).find(x=>x.download);if(i){{const a=document.getElementById("iso");a.href=i.download;a.hidden=false;a.textContent="⬇ Baixar a ISO "+i.versao+" ("+i.canal+")"}}}}).catch(()=>{{}});
</script>""")


def json_do_repo(caminho):
    st, corpo = http(gh_url(caminho) + f"?ref={BRANCH}",
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
class Handler(BaseHTTPRequestHandler):
    server_version = "OneMapasGateway/" + VERSAO
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[gateway] %s %s %s\n" % (self.headers.get("Host", ""), self.ip(), fmt % args))

    def ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def responder_bruto(self, codigo, corpo, tipo="application/json; charset=utf-8", extra=None):
        if isinstance(corpo, str):
            corpo = corpo.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def json(self, codigo, obj):
        self.responder_bruto(codigo, json.dumps(obj, ensure_ascii=False), extra={"Cache-Control": "no-store"})

    def html(self, codigo, texto, extra=None):
        self.responder_bruto(codigo, texto, "text/html; charset=utf-8", {"Cache-Control": "no-store", **(extra or {})})

    def redirecionar(self, para):
        self.responder_bruto(302, b"", "text/plain", {"Location": para, "Cache-Control": "no-store"})

    def ler_corpo(self, limite):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limite:
            return None
        return self.rfile.read(n) if n else b""

    def site_do_pedido(self):
        """Descobre se o pedido é para um site: <nome>.<DOMINIO_SITES> ou /_site/<nome>/..."""
        host = (self.headers.get("Host") or "").split(":")[0].lower().strip(".")
        u = urllib.parse.urlsplit(self.path)
        if host.endswith("." + DOMINIO_SITES):
            sub = host[: -len(DOMINIO_SITES) - 1]
            if NOME_VALIDO.match(sub):
                return sub, "", u.path or "/", u.query
        m = re.match(r"^/_site/([a-z0-9-]+)(/.*)?$", u.path)
        if m:
            return m.group(1), f"/_site/{m.group(1)}", m.group(2) or "", u.query
        return None

    def tratar(self):
        alvo = self.site_do_pedido()
        if alvo:
            return self.servir_site(*alvo)
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        chave = self.headers.get("X-Dono-Chave", "")

        if u.path == "/" and self.command in ("GET", "HEAD"):
            return self.html(200, pagina_portal())
        if u.path == "/api/status":
            return self.json(200, {"ok": True, "versao": VERSAO, "dominio_sites": DOMINIO_SITES, "url_base": URL_BASE, "sites": len(registro),
                                   "pcs_online": len({x["dono_hash"] for x in registro.values() if pc_ligado(x)}),
                                   "banco": estado_banco})
        if u.path == "/api/verificar":
            return self.json(200, verificar(q.get("nome", [""])[0]))
        if u.path == "/api/manifest":
            return self.json(200, json_do_repo("ISO/manifest.json"))
        if u.path == "/api/novidades":
            return self.json(200, json_do_repo("os/webui/novidades.json"))
        if u.path == "/api/meus-registros":
            if not chave_valida(chave):
                return self.json(400, {"mensagem": "Chave inválida."})
            h = hash_chave(chave)
            return self.json(200, {"sites": [{"nome": n, "url": url_site(n), "link": link_do_site(d), "criado_em": d["criado_em"]}
                                             for n, d in sorted(registro.items()) if d["dono_hash"] == h]})
        if u.path == "/api/meu-ip":
            return self.json(200, {"ip": self.ip(), "publico": ip_publico(self.ip())})
        if u.path == "/api/pc/batida" and self.command == "POST":
            try:
                d = json.loads(self.ler_corpo(10_000) or b"{}")
            except Exception:
                d = {}
            return self.json(*batida(chave, d.get("porta"), self.ip()))
        if u.path == "/api/registrar" and self.command == "POST":
            corpo = self.ler_corpo(10_000)
            try:
                d = json.loads(corpo or b"{}")
            except Exception:
                d = {}
            return self.json(*registrar(str(d.get("nome", "")), chave, d.get("dono_nome"), self.ip()))
        m = re.fullmatch(r"/api/registro/([a-z0-9-]+)", u.path)
        if m and self.command == "DELETE":
            nome = m.group(1)
            if not dono_de(nome, chave):
                return self.json(403, {"mensagem": "Só o PC dono pode apagar."})
            with trava_registro:
                banco.apagar_arquivos(nome)
                banco.apagar(nome)
                registro.pop(nome, None)
            return self.json(200, {"ok": True})
        m = re.fullmatch(r"/api/copia/([a-z0-9-]+)", u.path)
        if m and self.command in ("PUT", "DELETE"):
            nome, caminho = m.group(1), q.get("caminho", [""])[0]
            if not dono_de(nome, chave):
                return self.json(403, {"mensagem": "Só o PC dono pode mexer na cópia."})
            if not caminho_ok(caminho):
                return self.json(400, {"mensagem": "Caminho inválido."})
            try:
                if self.command == "DELETE":
                    banco.apagar_arquivos(nome, [caminho])
                else:
                    corpo = self.ler_corpo(MAX_ARQUIVO_COPIA)
                    if corpo is None:
                        return self.json(413, {"mensagem": "Arquivo maior que 5 MB não entra na cópia."})
                    tipo = mimetypes.guess_type(caminho)[0] or "application/octet-stream"
                    banco.salvar_arquivo(nome, caminho, corpo, tipo)
                quando = agora()
                banco.marcar_copia(nome, quando)
                registro[nome]["copia_em"] = quando
            except Exception as e:
                estado_banco["ultimo_erro"] = f"{agora()} {e}"
                return self.json(502, {"mensagem": str(e)[:200]})
            return self.json(200, {"ok": True})

        return self.html(404, pagina("Não encontrado", "<h1>404</h1><p>Página não encontrada.</p>"))

    def servir_site(self, nome, prefixo, caminho, query):
        """O Render não entrega o site: só manda o visitante para o PC do dono."""
        d = registro.get(nome)
        if not d:
            return self.html(404, pagina("Site não existe", f"<h1>{nome}</h1><p>Esse site ainda não existe.</p>"
                                         f"<p class='mudo'>O nome está livre: crie com o OneMapas OS.</p>"))
        link = link_do_site(d)
        if caminho == "/_onemapas/estado":
            return self.json(200, {"site": nome, "online": pc_ligado(d), "link": link, "porta_aberta": d.get("porta_aberta")})
        if link and pc_ligado(d):
            resto = (caminho or "/").lstrip("/")
            return self.redirecionar(link + resto + (f"?{query}" if query else ""))
        motivo = ("O PC do dono não mandou sinal nos últimos minutos (desligado ou sem internet)."
                  if not pc_ligado(d) else "O PC do dono ainda não tem um endereço público.")
        return self.html(503, pagina("PC desligado", f"<h1>{nome}</h1><p>Este site roda direto no computador do dono.</p>"
                                     f"<p>{motivo}</p><p class='mudo'>Tente de novo mais tarde.</p>"), {"Retry-After": "300"})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = tratar


def main():
    carregar_registro()
    threading.Thread(target=recarregar_sempre, daemon=True).start()
    porta = int(os.environ.get("PORT", "8080"))
    print(f"OneMapas Gateway {VERSAO} porta {porta}  sites em *.{DOMINIO_SITES}  banco={banco.nome}  "
          f"{len(registro)} sites", flush=True)
    ThreadingHTTPServer(("0.0.0.0", porta), Handler).serve_forever()


if __name__ == "__main__":
    main()
