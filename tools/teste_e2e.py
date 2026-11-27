#!/usr/bin/env python3
"""Teste de ponta a ponta do OneMapas OS com o Render e o Supabase de verdade.

  python3 tools/teste_e2e.py https://onemapas-gateway.onrender.com

Cria um site de teste, confere que o visitante recebe o site vindo do PC (túnel), que a cópia
chegou ao Supabase, que com o PC desligado o visitante recebe a cópia, e apaga tudo no fim.
"""
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error

GW = sys.argv[1].rstrip("/")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="onemapas-e2e-")
NOME = f"teste-e2e-{int(time.time()) % 100000}"
PC = "http://127.0.0.1:8081"
resultados = []


def req(url, metodo="GET", dados=None, cab=None, timeout=60):
    r = urllib.request.Request(url, method=metodo, data=json.dumps(dados).encode() if dados is not None else None,
                               headers={"Content-Type": "application/json", "X-OneMapas": "1", **(cab or {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode(errors="replace")
    except Exception as e:
        return None, {}, str(e)


def ok(nome, cond, detalhe=""):
    resultados.append((nome, bool(cond)))
    print(("✅" if cond else "❌"), nome, ("- " + detalhe) if detalhe else "", flush=True)
    return cond


def esperar(fn, seg, passo=3):
    fim = time.time() + seg
    while time.time() < fim:
        v = fn()
        if v:
            return v
        time.sleep(passo)
    return None


def ligar_pc():
    env = {**os.environ, "ONEMAPAS_SERVIDOR": GW, "ONEMAPAS_SITES": f"{TMP}/sites", "ONEMAPAS_CONFIG": f"{TMP}/cfg",
           "ONEMAPAS_PORTA": "8081"}
    env.pop("ONEMAPAS_LAN", None)
    return subprocess.Popen([sys.executable, f"{RAIZ}/os/pc/onemapas_pc.py"], env=env,
                            stdout=open(f"{TMP}/pc.log", "a"), stderr=subprocess.STDOUT)


def status():
    st, _, c = req(f"{PC}/api/status")
    return json.loads(c) if st == 200 else None


# 0) acorda o Render (o plano grátis dorme)
st, _, c = esperar(lambda: (lambda r: r if r[0] == 200 else None)(req(f"{GW}/api/status")), 180, 5) or (None, {}, "")
ok("Render responde /api/status", st == 200, c[:160])
if st == 200:
    info = json.loads(c)
    ok("Render usa o Supabase", info.get("banco", {}).get("tipo") == "supabase", json.dumps(info.get("banco")))

pc = ligar_pc()
try:
    esperar(status, 30, 1)
    ok("Túnel do PC conectou", esperar(lambda: (status() or {}).get("tunel", {}).get("tunel") == "conectado", 60))

    st, _, c = req(f"{PC}/api/verificar?nome={NOME}")
    ok(f"{NOME} está disponível", st == 200 and json.loads(c).get("disponivel"), c[:120])
    st, _, c = req(f"{PC}/api/criar", "POST", {"nome": NOME, "titulo": "Teste E2E OneMapas", "descricao": "ok"})
    ok("Criar site (registro no Supabase)", st == 201, c[:200])
    st, _, c = req(f"{GW}/api/verificar?nome={NOME}")
    ok("Depois de criado: Escolha outra URL", st == 200 and not json.loads(c).get("disponivel"))

    url = f"{GW}/_site/{NOME}/?sistema=onemapasoficealOS&site=true&load=conectado"
    st, h, c = req(url)
    ok("Visitante recebe o site vindo do PC", st == 200 and "Teste E2E OneMapas" in c and h.get("X-OneMapas-Origem") == "pc",
       f"HTTP {st} origem={h.get('X-OneMapas-Origem')}")
    st, h, c = req(f"{GW}/_site/{NOME}/style.css")
    ok("style.css vem do PC", st == 200 and "--cor" in c)

    ok("Cópia enviada ao Supabase", esperar(lambda: (lambda s: s and s["tunel"]["copia"]["estado"] == "em dia"
                                                     and s["tunel"]["copia"]["arquivos"] >= 2)(status()), 90),
       json.dumps((status() or {}).get("tunel", {}).get("copia")))
finally:
    pc.terminate(); pc.wait(10)

# 3) PC desligado -> cópia
print("… PC desligado, esperando o Render perceber (~35 s)", flush=True)
time.sleep(38)
st, h, c = req(f"{GW}/_site/{NOME}/_onemapas/estado")
ok("Render sabe que o PC desligou e que há cópia", st == 200 and json.loads(c) == {"site": NOME, "online": False, "copia": True}, c)
st, h, c = req(url)
ok("Visitante recebe a CÓPIA do Supabase", st == 200 and "Teste E2E OneMapas" in c and h.get("X-OneMapas-Origem") == "copia-supabase",
   f"HTTP {st} origem={h.get('X-OneMapas-Origem')}")
st, h, c = req(f"{GW}/_site/{NOME}/style.css")
ok("style.css vem da cópia", st == 200 and "--cor" in c and h.get("X-OneMapas-Origem") == "copia-supabase")

# 4) limpeza: liga o PC de novo e apaga o site (registro + cópia)
pc = ligar_pc()
try:
    esperar(status, 30, 1)
    st, _, c = req(f"{PC}/api/sites/{NOME}", "DELETE")
    ok("Apagar site de teste", st == 200, c[:160])
finally:
    pc.terminate(); pc.wait(10)
st, _, c = req(f"{GW}/api/verificar?nome={NOME}")
ok("Nome livre de novo depois de apagar", st == 200 and json.loads(c).get("disponivel"))

falhas = [n for n, v in resultados if not v]
print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} ok" + (f" — falhou: {falhas}" if falhas else ""))
if falhas:
    print("\n--- log do PC ---\n" + open(f"{TMP}/pc.log").read()[-3000:])
sys.exit(1 if falhas else 0)
