#!/usr/bin/env python3
"""
OneMapas OS - GATEWAY (roda no Render).

O site de cada pessoa roda no PC dela. O Render só faz a ponte:

    Cliente (navegador) ──> Render ──> PC do dono (onde o site roda)
    Cliente <── Render <── PC

O PC abre a conexão com o Render (assim funciona mesmo atrás de roteador/CGNAT,
sem abrir portas) e fica esperando pedidos (long-polling). Quando um visitante
chega em https://<nome>.onemapasos.oficeal.com, o Render entrega o pedido para o PC,
o PC responde e o Render devolve ao visitante.

Endereço público de um site:
    https://<nome>.<DOMINIO_SITES>/?sistema=onemapasoficealOS&site=true&load=em-espera
      load=em-espera  -> página de espera enquanto conecta no PC do dono
      load=conectado  -> o site em si (vem do PC)

Sem DNS curinga (para testar no onrender.com): https://<servico>.onrender.com/_site/<nome>/

Rotas:
  GET  /api/status
  GET  /api/verificar?nome=oi
  POST /api/registrar           {nome, dono_nome}           (header X-Dono-Chave)
  DELETE /api/registro/<nome>                                (header X-Dono-Chave)
  GET  /api/manifest  /api/novidades
  GET  /_tunel/esperar          o PC busca o próximo pedido   (header X-Dono-Chave)
  POST /_tunel/responder/<id>   o PC devolve a resposta       (header X-Dono-Chave)

Só usa a biblioteca padrão do Python.
"""
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent
REGISTRO_DIR = Path(os.environ.get("ONEMAPAS_REGISTRO", RAIZ / "registro"))

DOMINIO_SITES = os.environ.get("ONEMAPAS_DOMINIO_SITES", "onemapasos.oficeal.com").lower().strip(".")
REPO = os.environ.get("GITHUB_REPO", "one-mapas-officeal/One-mapas-oficeal-OS-office")
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
VERSAO = "0.3.0"

PARAMS_SITE = "sistema=onemapasoficealOS&site=true"
NOME_VALIDO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESERVADOS = {"api", "iso", "admin", "www", "assets", "static", "index", "sites", "os", "login", "mail",
              "smtp", "ftp", "ns1", "ns2", "status", "render", "app", "painel", "suporte", "onemapas"}
TEMPO_POLL = 20            # segundos que o PC fica esperando um pedido
TEMPO_RESPOSTA = 30        # segundos que o visitante espera o PC responder
PC_ONLINE_SE = TEMPO_POLL + 10  # PC online se ABRIU uma busca nos últimos N segundos
MAX_CORPO_PEDIDO = 2 * 1024 * 1024
MAX_CORPO_RESPOSTA = 15 * 1024 * 1024
MAX_SITES_POR_DONO = 20
MAX_REGISTROS_POR_IP_HORA = 10
CABECALHOS_PEDIDO = {"accept", "accept-language", "content-type", "cookie", "user-agent", "referer",
                     "if-none-match", "if-modified-since", "range", "authorization", "origin"}
CABECALHOS_RESPOSTA = {"content-type", "cache-control", "etag", "last-modified", "location", "set-cookie",
                       "content-language", "content-disposition", "content-range", "accept-ranges", "expires",
                       "vary", "content-security-policy", "x-frame-options", "access-control-allow-origin"}


def agora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_chave(chave):
    return hashlib.sha256(("onemapas:" + chave).encode()).hexdigest()


def chave_valida(chave):
    return isinstance(chave, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", chave or "") is not None


def url_site(nome, load="em-espera"):
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


# ------------------------------------------------------------------ registro dos nomes (quem é dono de quê)
# Fica em memória, no disco (registro/<nome>.json) e no GitHub (para sobreviver aos deploys do Render).
registro = {}
trava_registro = threading.Lock()
fila_github = queue.Queue()
estado_github = {"pendentes": 0, "ultimo_erro": None, "ultimo_ok": None}
registros_ip = {}


def carregar_registro():
    REGISTRO_DIR.mkdir(parents=True, exist_ok=True)
    for f in REGISTRO_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text("utf-8"))
            registro[d["nome"]] = d
        except Exception:
            pass
    # nomes registrados depois do último deploy estão só no GitHub
    if TOKEN:
        st, corpo = http(gh_url("registro") + f"?ref={BRANCH}", cabecalhos=gh_headers())
        if st == 200:
            for item in json.loads(corpo):
                nome = item.get("name", "")[:-5]
                if item.get("name", "").endswith(".json") and nome not in registro:
                    st2, c2 = http(gh_url(f"registro/{item['name']}") + f"?ref={BRANCH}",
                                   cabecalhos=gh_headers({"Accept": "application/vnd.github.raw+json"}))
                    if st2 == 200:
                        try:
                            d = json.loads(c2)
                            registro[d["nome"]] = d
                            (REGISTRO_DIR / item["name"]).write_bytes(c2)
                        except Exception:
                            pass
    print(f"[registro] {len(registro)} sites registrados", flush=True)


def trabalhador_github():
    while True:
        acao, caminho, conteudo, msg = fila_github.get()
        try:
            for tentativa in range(3):
                st, corpo = http(gh_url(caminho) + f"?ref={BRANCH}", cabecalhos=gh_headers())
                sha = json.loads(corpo).get("sha") if st == 200 else None
                h = gh_headers({"Content-Type": "application/json"})
                if acao == "salvar":
                    dados = {"message": msg, "content": base64.b64encode(conteudo).decode(), "branch": BRANCH}
                    if sha:
                        dados["sha"] = sha
                    st, resp = http(gh_url(caminho), "PUT", json.dumps(dados).encode(), h, 20)
                elif sha:
                    st, resp = http(gh_url(caminho), "DELETE",
                                    json.dumps({"message": msg, "sha": sha, "branch": BRANCH}).encode(), h, 20)
                else:
                    st, resp = 200, b""
                if st in (200, 201):
                    estado_github["ultimo_ok"] = agora()
                    break
                if st == 409 and tentativa < 2:
                    time.sleep(1.5)
                    continue
                estado_github["ultimo_erro"] = f"{agora()} {caminho}: HTTP {st} {resp[:150].decode(errors='ignore')}"
                print("[github]", estado_github["ultimo_erro"], file=sys.stderr, flush=True)
                break
        finally:
            estado_github["pendentes"] = fila_github.qsize()


def gh_fila(acao, caminho, conteudo, msg):
    if TOKEN:
        fila_github.put((acao, caminho, conteudo, msg))
        estado_github["pendentes"] = fila_github.qsize()


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
            "checagens": [{"onde": "Render", "resultado": "já existe" if ocupado else "livre"}]}


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
        registros_ip[ip] = lst + [t]
        d = {"nome": v["nome"], "dono_hash": h, "dono_nome": str(dono_nome or "")[:60], "criado_em": agora()}
        registro[d["nome"]] = d
        conteudo = json.dumps(d, indent=2, ensure_ascii=False).encode()
        (REGISTRO_DIR / f"{d['nome']}.json").write_bytes(conteudo)
    gh_fila("salvar", f"registro/{d['nome']}.json", conteudo, f"Registrar site {d['nome']}")
    return 201, {**v, "disponivel": False, "mensagem": "Registrado!", "salvo_no_github": bool(TOKEN)}


def dono_de(nome, chave):
    d = registro.get(nome)
    return bool(d and chave_valida(chave) and hmac.compare_digest(d["dono_hash"], hash_chave(chave)))


# ------------------------------------------------------------------ túnel (PC <-> Render)
class Tunel:
    def __init__(self):
        self.filas = {}        # dono_hash -> Queue de pedidos
        self.ultimo_poll = {}  # dono_hash -> time
        self.pendentes = {}    # id -> {"dono": hash, "evento": Event, "resposta": dict|None}
        self.trava = threading.Lock()

    def fila(self, dono):
        with self.trava:
            return self.filas.setdefault(dono, queue.Queue())

    def online(self, dono):
        return time.time() - self.ultimo_poll.get(dono, 0) < PC_ONLINE_SE

    def pedir(self, dono, pedido):
        """Chamado quando um visitante acessa o site: entrega ao PC e espera a resposta."""
        pid = uuid.uuid4().hex
        item = {"dono": dono, "evento": threading.Event(), "resposta": None, "expira": time.time() + TEMPO_RESPOSTA}
        self.pendentes[pid] = item
        self.fila(dono).put({"id": pid, **pedido})
        item["evento"].wait(TEMPO_RESPOSTA)
        self.pendentes.pop(pid, None)
        return item["resposta"]

    def esperar(self, dono):
        """Chamado pelo PC: devolve o próximo pedido (ou None depois de TEMPO_POLL)."""
        self.ultimo_poll[dono] = time.time()
        fim = time.time() + TEMPO_POLL
        q = self.fila(dono)
        while True:
            restante = fim - time.time()
            if restante <= 0:
                return None
            try:
                ped = q.get(timeout=restante)
            except queue.Empty:
                return None
            p = self.pendentes.get(ped["id"])
            if p and p["expira"] > time.time():  # pula pedidos cujo visitante já desistiu
                return ped

    def devolver(self, dono, ped):
        """O PC caiu antes de receber o pedido: volta para a fila (outro trabalhador do PC pega)."""
        if ped["id"] in self.pendentes:
            self.fila(dono).put(ped)

    def responder(self, dono, pid, resposta):
        p = self.pendentes.get(pid)
        if not p or not hmac.compare_digest(p["dono"], dono):
            return False
        p["resposta"] = resposta
        p["evento"].set()
        return True


tunel = Tunel()


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


def pagina_espera(nome, base):
    return pagina(f"{nome} · OneMapas", f"""
<h1 id="t">Conectando…</h1><div class="giro" id="g"></div>
<p id="m">Ligando você ao PC onde o site <b>{nome}</b> está rodando.</p>
<p class="mudo">Cliente → Render → PC do dono</p>
<script>
const base={json.dumps(base)}, alvo=base+"/?{PARAMS_SITE}&load=conectado";
let n=0;
async function tenta(){{
  try{{const r=await fetch(base+"/_onemapas/estado",{{cache:"no-store"}});const d=await r.json();
    if(d.online){{location.replace(alvo);return;}}}}catch(e){{}}
  if(++n===15){{document.getElementById("t").textContent="O PC do dono está desligado";
    document.getElementById("m").innerHTML="Este site roda no computador do dono, e ele não está conectado agora.<br>Esta página continua tentando sozinha.";}}
  setTimeout(tenta, n<15?2000:10000);
}}
tenta();
</script>""")


def pagina_portal():
    return pagina("OneMapas OS", f"""
<h1>OneMapas OS</h1>
<p>Crie um site que roda no seu próprio PC e fica no ar em<br><b>&lt;nome&gt;.{DOMINIO_SITES}</b></p>
<div class="linha"><input id="n" placeholder="nome do site" maxlength="63"><button id="b">Está disponível?</button></div>
<p id="r"></p>
<p class="mudo">Para criar o site, instale o OneMapas OS no PC (ISO) ou rode <code>onemapas_pc.py</code>.</p>
<p><a class="btn" id="iso" href="#" hidden>⬇ Baixar a ISO</a></p>
<script>
const n=document.getElementById("n"),r=document.getElementById("r");
n.oninput=()=>{{n.value=n.value.toLowerCase().replace(/[^a-z0-9-]/g,"");r.textContent=""}};
async function v(){{if(!n.value)return;const d=await (await fetch("/api/verificar?nome="+encodeURIComponent(n.value))).json();
 r.innerHTML=d.disponivel?"✅ SIM: "+d.url.split("/?")[0]+" está disponível!":"❌ NÃO: Escolha outra URL";}}
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
            return self.json(200, {"ok": True, "versao": VERSAO, "dominio_sites": DOMINIO_SITES, "sites": len(registro),
                                   "pcs_online": sum(1 for d in set(x["dono_hash"] for x in registro.values()) if tunel.online(d)),
                                   "github": {"configurado": bool(TOKEN), **estado_github}})
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
            return self.json(200, {"sites": [{"nome": n, "url": url_site(n), "criado_em": d["criado_em"]}
                                             for n, d in sorted(registro.items()) if d["dono_hash"] == h],
                                   "online": tunel.online(h)})
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
                registro.pop(nome, None)
                (REGISTRO_DIR / f"{nome}.json").unlink(missing_ok=True)
            gh_fila("apagar", f"registro/{nome}.json", None, f"Apagar site {nome}")
            return self.json(200, {"ok": True})

        # ---- túnel
        if u.path == "/_tunel/esperar" and self.command == "GET":
            if not chave_valida(chave):
                return self.json(401, {"mensagem": "Chave inválida."})
            dono = hash_chave(chave)
            ped = tunel.esperar(dono)
            if ped is None:
                return self.responder_bruto(204, b"", "text/plain")
            try:
                return self.json(200, ped)
            except (BrokenPipeError, ConnectionResetError, OSError):
                tunel.devolver(dono, ped)
        m = re.fullmatch(r"/_tunel/responder/([0-9a-f]{32})", u.path)
        if m and self.command == "POST":
            if not chave_valida(chave):
                return self.json(401, {"mensagem": "Chave inválida."})
            corpo = self.ler_corpo(MAX_CORPO_RESPOSTA * 2)
            if corpo is None:
                return self.json(413, {"mensagem": "Resposta grande demais."})
            try:
                resp = json.loads(corpo)
            except Exception:
                return self.json(400, {"mensagem": "JSON inválido."})
            ok = tunel.responder(hash_chave(chave), m.group(1), resp)
            return self.json(200 if ok else 410, {"ok": ok})

        return self.html(404, pagina("Não encontrado", "<h1>404</h1><p>Página não encontrada.</p>"))

    def servir_site(self, nome, prefixo, caminho, query):
        d = registro.get(nome)
        if not d:
            return self.html(404, pagina("Site não existe", f"<h1>{nome}</h1><p>Esse site ainda não existe.</p>"
                                         f"<p class='mudo'>O nome está livre: crie com o OneMapas OS.</p>"))
        base = f"{prefixo}" if prefixo else ""
        q = urllib.parse.parse_qs(query)
        load = q.get("load", [""])[0]
        online = tunel.online(d["dono_hash"])

        if caminho == "/_onemapas/estado":
            return self.json(200, {"site": nome, "online": online})
        # a página inicial sempre passa pela tela de espera primeiro
        if caminho in ("", "/") and self.command in ("GET", "HEAD"):
            if load != "conectado" or not online:
                if load != "em-espera" or q.get("sistema", [""])[0] != "onemapasoficealOS":
                    return self.redirecionar(f"{base}/?{PARAMS_SITE}&load=em-espera")
                return self.html(200, pagina_espera(nome, base))
        if not online:
            return self.html(503, pagina("PC desligado", f"<h1>{nome}</h1><p>O PC do dono está desligado agora.</p>"
                                         f"<p><a class='btn' href='{base}/?{PARAMS_SITE}&load=em-espera'>Tentar de novo</a></p>"),
                             {"Retry-After": "10"})

        corpo = self.ler_corpo(MAX_CORPO_PEDIDO)
        if corpo is None:
            return self.html(413, pagina("Muito grande", "<h1>413</h1><p>Envio grande demais.</p>"))
        pedido = {
            "site": nome, "metodo": self.command,
            "caminho": (caminho or "/") + (f"?{query}" if query else ""),
            "cabecalhos": {k: v for k, v in self.headers.items() if k.lower() in CABECALHOS_PEDIDO},
            "ip": self.ip(), "corpo_b64": base64.b64encode(corpo).decode(),
        }
        resp = tunel.pedir(d["dono_hash"], pedido)
        if not resp:
            return self.html(504, pagina("Sem resposta", f"<h1>{nome}</h1><p>O PC do dono não respondeu a tempo.</p>"))
        try:
            corpo_resp = base64.b64decode(resp.get("corpo_b64", ""))[:MAX_CORPO_RESPOSTA]
            status = int(resp.get("status", 200))
            if not 100 <= status <= 599:
                status = 502
        except Exception:
            return self.html(502, pagina("Erro", "<h1>502</h1><p>Resposta inválida do PC.</p>"))
        extra = {}
        for k, v in (resp.get("cabecalhos") or {}).items():
            if k.lower() in CABECALHOS_RESPOSTA and "\n" not in str(v) and "\r" not in str(v):
                if k.lower() == "location" and prefixo and str(v).startswith("/"):
                    v = prefixo + v
                extra[k] = str(v)
        tipo = extra.pop("Content-Type", None) or extra.pop("content-type", None) or "application/octet-stream"
        extra["X-OneMapas-Site"] = nome
        self.responder_bruto(status, corpo_resp, tipo, extra)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = tratar


def main():
    carregar_registro()
    threading.Thread(target=trabalhador_github, daemon=True).start()
    porta = int(os.environ.get("PORT", "8080"))
    print(f"OneMapas Gateway {VERSAO} porta {porta}  sites em *.{DOMINIO_SITES}  "
          f"github={'ligado' if TOKEN else 'DESLIGADO'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", porta), Handler).serve_forever()


if __name__ == "__main__":
    main()
