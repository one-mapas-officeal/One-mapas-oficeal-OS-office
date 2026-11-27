# One-mapas-oficeal-OS-office
This is an operating system from One Maps Office. DO NOT COPY THIS SYSTEM. WE ARE NOT COPYING OTHER SYSTEMS.

---

# OneMapas OS

Sistema operacional (base Linux Debian 12) para **criar sites**. O usuário digita a URL que quer,
por exemplo `https://onemapasoficeal.com.br/oi`, e o sistema pergunta: *está disponível?*

- **SIM**: o site é criado numa pasta na **Área de Trabalho** (`Meus Sites OneMapas/oi`). Este PC é o dono.
  Se houver token do GitHub, os arquivos vão para `sites/oi/` neste repositório e o **Render** coloca no ar.
- **NÃO**: aparece **"Escolha outra URL"**.

A verificação consulta 3 lugares: o domínio no Render (404 = livre), a pasta `sites/<nome>` no GitHub e a Área de Trabalho do PC.

## Interface

Área de trabalho com janelas, que você escolhe em **Configurações > Aparência**:

| Estilo | Como fica |
|---|---|
| Windows | barra de tarefas centralizada, menu Iniciar, botões _ ▢ ✕ |
| Linux Mint | painel escuro, botão "Iniciar" verde, botões redondos |
| Mac | barra de menus no topo, Dock flutuante, botões vermelho/amarelo/verde |

Aplicativos: Criador de Sites, Meus Sites, **Novidades**, **Progresso da ISO**, Rede (Wi-Fi), Terminal, Configurações.

## Estrutura

```
os/
  backend/onemapas_server.py   servidor local (Python, sem dependências)
  webui/                       interface gráfica (HTML/CSS/JS) + novidades.json
  network/                     drivers de rede: detector onemapas-net, serviço, ajustes modprobe e NetworkManager
  live-build/                  configuração da ISO (Debian 12, abre direto na interface)
  build-iso.sh                 gera a ISO
ISO/
  manifest.json                índice das ISOs (canal, versão, pasta, nome, progresso, sha256, download)
  <canal>/<versão>/            ex.: ISO/beta/0.1.0/ (info.json + .sha256; a .iso fica nos Releases)
sites/                         sites publicados (servidos pelo Render em onemapasoficeal.com.br/<nome>)
render.yaml                    configuração do Render
.github/workflows/build-iso.yml  gera a ISO no GitHub Actions e atualiza o manifest
tools/atualizar_manifest.py
```

## Usar agora (sem instalar a ISO)

Em qualquer Linux (ou no Termux/proot do celular):

```bash
python3 os/backend/onemapas_server.py
# abra http://127.0.0.1:8080
```

Para publicar sites: **Configurações > Token do GitHub** (token com permissão *Contents: read and write* neste repositório).

## Gerar a ISO

- **GitHub**: Actions > *Construir ISO* > Run workflow > canal (`alpha`, `beta`, `em-testes`, `estavel`) e versão.
  A ISO vai para os Releases, e `ISO/manifest.json` e `ISO/<canal>/<versão>/` são atualizados sozinhos.
  (O GitHub não aceita arquivos acima de 100 MB no repositório, por isso a `.iso` fica nos Releases.)
- **Local** (Debian/Ubuntu): `sudo apt install live-build && sudo ./os/build-iso.sh beta 0.1.0` gera a ISO em `out/`.

Grave a ISO num pendrive (Balena Etcher, Rufus ou `dd`). Ela inicia direto no OneMapas OS (usuário `onemapas`, pt-BR, teclado ABNT2).

## Drivers de rede

Os drivers de rede ficam no kernel Linux. O OneMapas OS inclui:
- **firmware** para Intel, Realtek, Atheros/Qualcomm, Broadcom, MediaTek, TI e outros;
- **`onemapas-net`**, que na inicialização identifica as placas (PCI/USB), carrega o módulo certo, libera o Wi-Fi e avisa se falta firmware (`onemapas-net --relatorio`);
- ajustes de estabilidade (`/etc/modprobe.d/onemapas-net.conf`) e o NetworkManager configurado (Wi-Fi pelo app **Rede**).

## Render

Crie um *Static Site* no Render a partir deste repositório (ou use o `render.yaml` como Blueprint),
com publish directory `sites` e domínio `onemapasoficeal.com.br`.
