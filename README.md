# One-mapas-oficeal-OS-office
This is an operating system from One Maps Office. DO NOT COPY THIS SYSTEM. WE ARE NOT COPYING OTHER SYSTEMS.

---

# OneMapas OS

Sistema da One Mapas Office para **criar sites que rodam no seu próprio PC**.

Endereço de um site:

```
https://[URL escolhida].onemapasos.oficeal.com/?sistema=onemapasoficealOS&site=true&load=em-espera
```

## Como funciona

```
Cliente (navegador) ──► Render ──► PC do dono (onde o site roda)
Cliente ◄────────────── Render ◄── PC
```

1. **Cliente → Render**: o visitante abre `https://oi.onemapasos.oficeal.com`. O Render mostra a tela de espera (`load=em-espera`).
2. **Render → PC**: o Render repassa o pedido para o PC dono do site `oi`.
3. **PC → Render → Cliente**: o PC lê a pasta do site na Área de Trabalho e responde. O Render entrega ao visitante (`load=conectado`).

O PC **se conecta ao Render primeiro** e fica esperando pedidos (túnel por *long-polling*).
Por isso funciona em internet de casa ou 4G com CGNAT, sem abrir portas no roteador e sem IP fixo.
### Supabase (projeto Mapasos)
- **Tabela `public.sites`**: qual nome pertence a qual PC (hash da chave do PC). A chave primária impede dois PCs com o mesmo nome.
- **Bucket `sites`**: cópia dos arquivos de cada site. O PC envia pelo Render só o que mudou (verificado a cada 30 s e na hora em que salva).
- **PC desligado**: o Render mostra a cópia do Supabase (`X-OneMapas-Origem: copia-supabase`), e o site continua no ar.
  Sem cópia, o visitante vê "O PC do dono está desligado".
- A chave secreta do Supabase fica **só no Render**. RLS ligado e nenhum acesso para `anon`/`authenticated`.

### Criando um site (no PC)
O usuário digita a URL e o sistema pergunta ao Render: *está disponível?*
- **SIM**: o nome fica registrado para este PC, e a pasta `Área de Trabalho/Meus Sites OneMapas/<nome>/` é criada.
  O PC é o dono (tem uma chave secreta em `~/.config/onemapas/config.json`; o Render guarda só o hash).
- **NÃO**: **"Escolha outra URL"**.

## Interface (no PC)

| Estilo | Como fica |
|---|---|
| Windows | barra de tarefas centralizada, menu Iniciar, botões _ ▢ ✕ |
| Linux Mint | painel escuro, botão "Iniciar" verde, botões redondos |
| Mac | barra de menus no topo, Dock, botões vermelho/amarelo/verde |

Aplicativos: Criador de Sites, Área de Trabalho, Editor, **Novidades**, **Progresso da ISO**,
Conexão com o Render, Terminal, Configurações.

## Estrutura

```
os/
  render/gateway.py   roda no Render: portal, registro (Supabase), túnel, proxy e cópia dos sites
  pc/onemapas_pc.py   roda no PC: interface, pasta dos sites, túnel até o Render e envio da cópia
  webui/              interface gráfica + novidades.json
  network/            drivers de rede da ISO (onemapas-net, serviço, modprobe, NetworkManager)
  live-build/         configuração da ISO (Debian 12)
  build-iso.sh        gera a ISO
ISO/manifest.json     índice das ISOs;  ISO/<canal>/<versão>/
render.yaml           configuração do Render
```

## Colocar no ar

**1. Domínio** (`oficeal.com`, no painel de DNS de onde ele foi comprado):
- `onemapasos.oficeal.com`: CNAME para o endereço `.onrender.com` do serviço
- `*.onemapasos.oficeal.com`: CNAME para o mesmo endereço (DNS curinga: é o que faz `[URL].onemapasos.oficeal.com` funcionar)

**2. Render**: *New > Blueprint* com este repositório (usa o `render.yaml`).
- Em *Environment*, defina `SUPABASE_SECRET_KEY` (Supabase > Project Settings > API Keys > *secret key*). `SUPABASE_URL` já vem no `render.yaml`.
- Em *Custom Domains*, adicione `onemapasos.oficeal.com` e `*.onemapasos.oficeal.com`.
- Use o plano **Starter** ou maior: o plano grátis "dorme" e derruba os túneis.

**3. PC**: instale a ISO, ou rode `python3 os/pc/onemapas_pc.py` e abra `http://127.0.0.1:8080`.

Para testar sem DNS: `https://<serviço>.onrender.com/_site/<nome>/`.

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
