#!/usr/bin/env python3
"""Atualiza ISO/manifest.json e cria ISO/<canal>/<versao>/info.json depois de um build.
uso: atualizar_manifest.py <canal> <versao> <arquivo.iso> <url_download> [status] [progresso]
"""
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

canal, versao, iso, url = sys.argv[1:5]
status = sys.argv[5] if len(sys.argv) > 5 else "pronta"
progresso = int(sys.argv[6]) if len(sys.argv) > 6 else 100
raiz = Path(__file__).resolve().parent.parent
mpath = raiz / "ISO" / "manifest.json"
m = json.loads(mpath.read_text("utf-8"))
p = Path(iso)
sha = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
pasta = f"ISO/{canal}/{versao}"
agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

entrada = next((i for i in m["isos"] if i["canal"] == canal and i["versao"] == versao), None)
if entrada is None:
    entrada = {"canal": canal, "versao": versao, "arquitetura": "amd64", "base": "Debian 12 (bookworm)", "etapas": []}
    m["isos"].insert(0, entrada)
entrada.update({
    "pasta": pasta, "nome": p.name, "status": status, "progresso": progresso,
    "tamanho_mb": round(p.stat().st_size / 1048576, 1) if p.exists() else None,
    "sha256": sha, "download": url, "data": agora[:10],
})
for e in entrada.get("etapas", []):
    if progresso == 100 or "Build" in e["nome"]:
        e["feito"] = e["feito"] or "Build" in e["nome"] or progresso == 100
if canal not in m["canais"]:
    m["canais"].append(canal)
m["ultima"] = {"canal": canal, "versao": versao, "pasta": pasta}
m["atualizado_em"] = agora
mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", "utf-8")

d = raiz / pasta
d.mkdir(parents=True, exist_ok=True)
(d / "info.json").write_text(json.dumps(entrada, indent=2, ensure_ascii=False) + "\n", "utf-8")
if sha:
    (d / f"{p.name}.sha256").write_text(f"{sha}  {p.name}\n")
print(f"manifest atualizado: {canal} {versao} -> {url}")
