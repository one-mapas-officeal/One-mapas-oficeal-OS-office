# One-mapas-oficeal-OS-office
This is an operating system from One Maps Office. DO NOT COPY THIS SYSTEM. WE ARE NOT COPYING OTHER SYSTEMS.

---

# OneMapas OS

Sistema da One Mapas Office para **criar sites**, rodando como **servidor web** (Render) em
`https://onemapasoficeal.com.br`. A interface (área de trabalho no navegador) fica em `/`,
e cada site criado fica em `/<nome>`.

O usuário digita a URL que quer, por exemplo `https://onemapasoficeal.com.br/oi`, e o sistema responde:

- **SIM**: o site é criado no servidor e aparece como uma **pasta na Área de Trabalho** do OneMapas OS,
  onde o dono abre e edita os arquivos (HTML, CSS, JS…).
- **NÃO**: aparece **"Escolha outra URL"**.

### Quem é o dono
Quem cria o site é o dono. O navegador recebe uma **chave secreta** (Configurações > Chave do dono),
e o servidor guarda só o hash dela. Com a chave, o dono edita os sites de qualquer PC.
Guarde uma cópia da chave: sem ela, não dá para editar os sites.

### Onde os sites ficam guardados
O Render apaga o disco a cada deploy. Por isso cada mudança também é gravada no GitHub (`sites/<nome>/`),
usando a variável `GITHUB_TOKEN` do Render. O próximo deploy já traz os sites de volta.
Os sites rodam isolados (CSP sandbox) e não conseguem mexer no OneMapas OS, mesmo estando no mesmo domínio.

## Interface

| Estilo | Como fica |
|---|---|
| Windows | barra de tarefas centralizada, menu Iniciar, botões _ ▢ ✕ |
| Linux Mint | painel escuro, botão "Iniciar" verde, botões redondos |
| Mac | barra de menus no topo, Dock, botões vermelho/amarelo/verde |

Aplicativos: Criador de Sites, Área de Trabalho (Meus Sites), Editor, **Novidades**, **Progresso da ISO**,
Servidor e Rede, Terminal, Configurações.

## Estrutura

```
os/
  backend/onemapas_server.py   servidor web (Python, sem dependências)
  webui/                       interface (servida em /_os/) + novidades.json
  network/                     drivers de rede da ISO: onemapas-net, serviço, modprobe, NetworkManager
  live-build/                  configuração da ISO (Debian 12; abre o servidor web em tela cheia)
  build-iso.sh                 gera a ISO
ISO/
  manifest.json                índice das ISOs (canal, versão, pasta, nome, progresso, sha256, download)
  <canal>/<versão>/            ex.: ISO/beta/0.1.0/ (info.json + .sha256; a .iso fica nos Releases)
sites/                         sites criados (cópia no GitHub)
render.yaml                    configuração do Render
.github/workflows/build-iso.yml  gera a ISO e atualiza o manifest
tools/atualizar_manifest.py
```

## Colocar no ar (Render)

1. No Render: **New > Blueprint** e escolha este repositório (usa o `render.yaml`).
2. Em *Environment*, defina `GITHUB_TOKEN`: um token *fine-grained* só deste repositório, com **Contents: Read and write**.
3. Em *Settings > Custom Domains*, adicione `onemapasoficeal.com.br` e aponte o DNS como o Render indicar.

Para testar no seu computador: `python3 os/backend/onemapas_server.py` e abra `http://127.0.0.1:8080`.

API: `GET /api/verificar?nome=oi`, `POST /api/criar`, `GET /api/meus-sites`,
`GET|PUT|DELETE /api/sites/<nome>/arquivo`, `DELETE /api/sites/<nome>`, `GET /api/manifest`, `GET /api/novidades`, `GET /api/status`.

## ISO

A ISO é um Debian 12 que liga direto no OneMapas OS online, em tela cheia, em pt-BR e teclado ABNT2.
Sem internet, abre a tela de Wi-Fi primeiro.

- **GitHub**: Actions > *Construir ISO* > Run workflow > canal (`alpha`, `beta`, `em-testes`, `estavel`) e versão.
  A `.iso` vai para os Releases (o GitHub não aceita arquivos acima de 100 MB no repositório). `ISO/manifest.json` e
  `ISO/<canal>/<versão>/` são atualizados sozinhos.
- **Local**: `sudo apt install live-build && sudo ./os/build-iso.sh beta 0.1.0` gera a ISO em `out/`.

### Drivers de rede
Os drivers ficam no kernel Linux. A ISO traz o **firmware** (Intel, Realtek, Atheros/Qualcomm, Broadcom, MediaTek…),
o **`onemapas-net`** (detecta as placas PCI/USB na inicialização, carrega o driver, libera o Wi-Fi e avisa se falta firmware)
e ajustes de estabilidade em `/etc/modprobe.d/onemapas-net.conf`.
