#!/usr/bin/env python3
"""Atualiza ISO/manifest.json (aba Progresso da ISO) e ISO/<canal>/<versao>/info.json.

uso: atualizar_manifest.py <canal> <versao> <status> <progresso> [arquivo.iso] [url_download] [url_build]
  ex.: atualizar_manifest.py beta 0.1.0 "construindo: sistema base" 75
       atualizar_manifest.py beta 0.1.0 pronta 100 out/onemapas.iso https://github.com/.../x.iso
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

canal, versao, status, progresso = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
iso = Path(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else None
url = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] else None
url_build = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else None

raiz = Path(__file__).resolve().parent.parent
mpath = raiz / "ISO" / "manifest.json"
m = json.loads(mpath.read_text("utf-8"))
pasta = f"ISO/{canal}/{versao}"
nome = f"onemapas-os-{versao}-{canal}-amd64.iso"
agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

entrada = next((i for i in m["isos"] if i["canal"] == canal and i["versao"] == versao), None)
if entrada is None:
    modelo = m["isos"][0] if m["isos"] else {}
    entrada = {"canal": canal, "versao": versao, "arquitetura": "amd64", "base": "Debian 12 (bookworm)",
               "etapas": [dict(e, feito=e["feito"] and "Build" not in e["nome"] and "Testes" not in e["nome"])
                          for e in modelo.get("etapas", [])],
               "tamanho_mb": None, "sha256": None, "download": None}
    m["isos"].insert(0, entrada)

entrada.update({"pasta": pasta, "nome": nome, "status": status, "progresso": progresso, "data": agora[:10]})
if url_build:
    entrada["build"] = url_build
if iso and iso.exists():
    h = hashlib.sha256()
    with open(iso, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    entrada["sha256"] = h.hexdigest()
    entrada["tamanho_mb"] = round(iso.stat().st_size / 1048576, 1)
if url:
    entrada["download"] = url
for e in entrada.get("etapas", []):
    if "Build" in e["nome"]:
        e["feito"] = status == "pronta"

if canal not in m["canais"]:
    m["canais"].append(canal)
if status == "pronta":
    m["ultima"] = {"canal": canal, "versao": versao, "pasta": pasta}
m["atualizado_em"] = agora
mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", "utf-8")

d = raiz / pasta
d.mkdir(parents=True, exist_ok=True)
(d / "info.json").write_text(json.dumps(entrada, indent=2, ensure_ascii=False) + "\n", "utf-8")
if entrada.get("sha256"):
    (d / f"{nome}.sha256").write_text(f"{entrada['sha256']}  {nome}\n")
print(f"manifest: {canal} {versao} -> {status} {progresso}%")
