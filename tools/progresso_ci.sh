#!/bin/bash
# Chamado pelo build-iso.sh a cada etapa (no GitHub Actions): atualiza o manifest e envia ao GitHub,
# para a aba "Progresso da ISO" mostrar o andamento ao vivo.
#   uso: progresso_ci.sh "<status>" <progresso> [arquivo.iso] [url_download]
set -u
cd "$(dirname "$0")/.."
python3 tools/atualizar_manifest.py "$CANAL" "$VERSAO" "$1" "$2" "${3:-}" "${4:-}" "${URL_BUILD:-}" || exit 0
git add ISO
git commit -q -m "ISO $CANAL $VERSAO: $1 ($2%) [skip render]" || exit 0
for i in 1 2 3 4; do
  git push -q origin "HEAD:$GITHUB_REF_NAME" && exit 0
  git pull -q --rebase origin "$GITHUB_REF_NAME" || true
  sleep $((i * 3))
done
echo "aviso: não consegui enviar o progresso" >&2
