#!/bin/bash
# Chamado pelo build-iso.sh a cada etapa (no GitHub Actions): atualiza o manifest e envia ao GitHub,
# para a aba "Progresso da ISO" mostrar o andamento ao vivo.
#   uso: progresso_ci.sh "<status>" <progresso> [arquivo.iso] [url_download]
# No passo final ("pronta") um envio que falhar faz o build falhar: o manifest não pode ficar desatualizado.
set -u
cd "$(dirname "$0")/.."
python3 tools/atualizar_manifest.py "$CANAL" "$VERSAO" "$1" "$2" "${3:-}" "${4:-}" "${URL_BUILD:-}" || exit 0
git add ISO
git commit -q -m "ISO $CANAL $VERSAO: $1 ($2%) [skip render]" || exit 0
for i in 1 2 3 4 5 6 7 8; do
  git push -q origin "HEAD:$GITHUB_REF_NAME" && exit 0
  # alguém enviou outra coisa ao mesmo tempo: pega e refaz o manifest em cima do mais novo
  git fetch -q origin "$GITHUB_REF_NAME"
  git reset -q --hard "origin/$GITHUB_REF_NAME"
  python3 tools/atualizar_manifest.py "$CANAL" "$VERSAO" "$1" "$2" "${3:-}" "${4:-}" "${URL_BUILD:-}" >/dev/null
  git add ISO
  git commit -q -m "ISO $CANAL $VERSAO: $1 ($2%) [skip render]" || exit 0
  sleep $((i * 3))
done
echo "ERRO: não consegui enviar o progresso ($1)" >&2
[ "$1" = "pronta" ] && exit 1
exit 0
