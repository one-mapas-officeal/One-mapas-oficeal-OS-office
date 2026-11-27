#!/usr/bin/env python3
"""Teste de ponta a ponta do OneMapas OS com o Render e o Supabase de verdade.

  python3 tools/teste_e2e.py https://onemapas-gateway.onrender.com

O visitante abre o site DIRETO no PC (http://IP:8090/<nome>/). O Render só guarda dados no Supabase
(registro, endereço do PC enviado a cada 5 min, cópia) e o link com nome só redireciona para o PC.
"""
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error

GW = sys.argv[1].rstrip("/")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="onemapas-e2e-")
NOME = f"teste-e2e-{int(time.time()) % 100000}"
PC, SITES = "http://127.0.0.1:8081", "http://127.0.0.1:8090"
resultados = []


class SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


ABRIR = urllib.request.build_opener(SemRedirecionar)


def req(url, metodo="GET", dados=None, timeout=60):
    r = urllib.request.Request(url, method=metodo, data=json.dumps(dados).encode() if dados is not None else None,
                               headers={"Content-Type": "application/json", "X-OneMapas": "1"})
    try:
        with ABRIR.open(r, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode(errors="replace")
    except Exception as e:
        return None, {}, str(e)


def ok(nome, cond, detalhe=""):
    resultados.append((nome, bool(cond)))
    print(("✅" if cond else "❌"), nome, ("- " + str(detalhe)[:200]) if detalhe else "", flush=True)
    return cond


def esperar(fn, seg, passo=2):
    fim = time.time() + seg
    while time.time() < fim:
        v = fn()
        if v:
            return v
        time.sleep(passo)
    return None


def ligar_pc():
    env = {**os.environ, "ONEMAPAS_SERVIDOR": GW, "ONEMAPAS_SITES": f"{TMP}/sites", "ONEMAPAS_CONFIG": f"{TMP}/cfg",
           "ONEMAPAS_PORTA": "8081", "ONEMAPAS_PORTA_SITES": "8090"}
    env.pop("ONEMAPAS_LAN", None)
    return subprocess.Popen([sys.executable, f"{RAIZ}/os/pc/onemapas_pc.py"], env=env,
                            stdout=open(f"{TMP}/pc.log", "a"), stderr=subprocess.STDOUT)


def status():
    st, _, c = req(f"{PC}/api/status")
    return json.loads(c) if st == 200 else None


# 0) acorda o Render (o plano grátis dorme)
r = esperar(lambda: (lambda x: x if x[0] == 200 else None)(req(f"{GW}/api/status")), 180, 5) or (None, {}, "")
ok("Render responde /api/status", r[0] == 200, r[2][:120])
if r[0] == 200:
    banco = json.loads(r[2]).get("banco", {})
    ok("Render usa o Supabase", banco.get("tipo") == "supabase", json.dumps(banco))

pc = ligar_pc()
try:
    esperar(status, 30, 1)
    ok("PC mandou o sinal ao Render", esperar(lambda: (status() or {}).get("pc", {}).get("render") == "ok", 90))
    st, _, c = req(f"{PC}/api/verificar?nome={NOME}")
    ok(f"{NOME} está disponível", st == 200 and json.loads(c).get("disponivel"), c[:100])
    st, _, c = req(f"{PC}/api/criar", "POST", {"nome": NOME, "titulo": "Teste E2E OneMapas", "descricao": "ok"})
    ok("Criar site (registro no Supabase)", st == 201, c[:200])
    st, _, c = req(f"{GW}/api/verificar?nome={NOME}")
    ok("Depois de criado: Escolha outra URL", st == 200 and not json.loads(c).get("disponivel"))

    st, h, c = req(f"{SITES}/{NOME}/")
    ok("Visitante abre o site DIRETO no PC (porta 8090)", st == 200 and "Teste E2E OneMapas" in c, f"HTTP {st}")
    st, h, c = req(f"{SITES}/{NOME}/style.css")
    ok("style.css vem direto do PC", st == 200 and "--cor" in c)
    st, h, c = req(f"{SITES}/{NOME}/onemapas-site.json")
    ok("Arquivo interno do site fica escondido", st == 404)
    st, h, c = req(f"{SITES}/{NOME}/../../cfg/config.json")
    ok("Não sai da pasta do site", st in (400, 403, 404) and "chave" not in c, f"HTTP {st}")

    s = status() or {}
    pub = s.get("pc", {}).get("publico", {})
    print("   IP público:", pub.get("ip"), "| porta aberta de fora:", pub.get("porta_aberta"), "| UPnP:", pub.get("upnp"), pub.get("upnp_erro") or "")
    st, _, c = req(f"{GW}/_site/{NOME}/_onemapas/estado")
    est = json.loads(c) if st == 200 else {}
    ok("Supabase guardou o endereço do PC", est.get("online") is True and (est.get("link") or "").endswith(f"/{NOME}/"), c)
    st, h, c = req(f"{GW}/_site/{NOME}/")
    ok("Link com nome no Render só REDIRECIONA para o PC", st == 302 and h.get("location", "").endswith(f"/{NOME}/"),
       f"HTTP {st} -> {h.get('location')}")

    ok("Cópia enviada ao Supabase", esperar(lambda: (lambda s: s and s["pc"]["copia"]["estado"] == "em dia"
                                                     and s["pc"]["copia"]["arquivos"] >= 2)(status()), 90),
       json.dumps((status() or {}).get("pc", {}).get("copia")))

    st, _, c = req(f"{PC}/api/sites/{NOME}", "DELETE")
    ok("Apagar site de teste", st == 200, c[:120])
finally:
    pc.terminate(); pc.wait(10)
st, _, c = req(f"{GW}/api/verificar?nome={NOME}")
ok("Nome livre de novo depois de apagar", st == 200 and json.loads(c).get("disponivel"))

falhas = [n for n, v in resultados if not v]
print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} ok" + (f" — falhou: {falhas}" if falhas else ""))
if falhas:
    print("\n--- log do PC ---\n" + open(f"{TMP}/pc.log").read()[-3000:])
sys.exit(1 if falhas else 0)
