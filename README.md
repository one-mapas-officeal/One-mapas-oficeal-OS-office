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
Visitante ──────────────► PC do dono   http://IP-do-PC:8090/<nome>/   (o PC entrega o site)
PC ── sinal a cada 5 min ──► Render ──► Supabase   (endereço do PC, registro, cópia)
```

- **O visitante abre o site direto no PC**, na porta **8090**. O Render não entrega o site.
- **A cada 5 minutos o PC manda um sinal ao Render.** O sinal mantém o Render acordado; o Render grava no Supabase
  o endereço atual do PC (o IP muda) e testa se a porta 8090 está aberta de fora.
- **Porta no roteador:** o PC tenta abrir sozinho (UPnP). Se o roteador não deixar, o app **Conexão** mostra o passo a passo
  (redirecionar a porta 8090 para o IP do PC). Se o provedor usa **CGNAT**, só com IP público do provedor.
- **Link com nome:** `<nome>.onemapasos.oficeal.com` (ou `onemapas-gateway.onrender.com/_site/<nome>/`) só **redireciona** para o PC.
- **Cópia:** os arquivos também vão para o Supabase Storage, como backup.

### Criando um site (no PC)
O usuário digita o nome e o sistema pergunta ao Render/Supabase: *está disponível?*
- **SIM**: o nome fica registrado para este PC, e a pasta `Área de Trabalho/Meus Sites OneMapas/<nome>/` é criada.
  O PC é o dono (tem uma chave secreta em `~/.config/onemapas/config.json`; o Render guarda só o hash).
- **NÃO**: **"Escolha outra URL"**.

### Supabase (projeto Mapasos)
- **Tabela `public.sites`**: nome, hash da chave do PC dono, `pc_endereco` (http://IP:porta), `visto_em` (último sinal), `porta_aberta`.
- **Bucket `sites`**: cópia dos arquivos.
- A chave secreta do Supabase fica **só no Render**. RLS ligado e nenhum acesso para `anon`/`authenticated`.

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
  render/gateway.py   roda no Render: registro dos nomes, sinal do PC, teste de porta e redirecionamento (Supabase)
  pc/onemapas_pc.py   roda no PC: interface, servidor dos sites (porta 8090), UPnP, sinal de 5 min e cópia
  webui/              interface gráfica + novidades.json
  network/            ajustes dos drivers de rede (modprobe)
  iso/                rootfs da ISO (init, rede, disco, painel) + GRUB
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

## ISO (Linux puro)

A ISO **não instala nenhuma distribuição**. Ela tem só:
- o **kernel Linux** e os drivers (rede com cabo e Wi-Fi, USB, SATA, NVMe, cartão; sistemas de arquivos ext4, NTFS, FAT, exFAT);
- o **firmware** de Wi-Fi e rede (Intel, Realtek, Atheros/Qualcomm, Broadcom, MediaTek, Marvell);
- o **BusyBox** (comandos básicos, `init`, DHCP), o `kmod` (carrega os drivers), o `wpa_supplicant` (Wi-Fi) e o **Python**;
- o **OneMapas OS** (programa do PC + interface).

Tudo roda na memória RAM. A ISO tem cerca de 180 MB e é montada em segundos. Inicia em PCs com BIOS e com UEFI
(desligue o *Secure Boot*).

**Na tela do PC** aparece um painel em modo texto: o endereço `http://<IP-do-PC>:8080`, o **código de acesso**,
a rede, o estado do túnel, e as opções Wi-Fi, onde salvar, log, terminal (Alt+F2), reiniciar e desligar.
A interface Windows / Mint / Mac abre **no celular** (na mesma rede) com esse código.

**Onde ficam os dados**: numa pasta `onemapas-dados` em qualquer partição do PC (ext4, NTFS, FAT, exFAT).
Ela é achada sozinha no boot; a primeira vez, escolha o disco com a opção **2** do painel.
Sem disco, os dados ficam na memória e somem ao desligar.

**Testar a ISO sem pendrive (tela do PC no navegador):** `tools/rodar_iso.py` baixa a ISO mais recente, liga numa
máquina virtual (QEMU) e mostra a tela do PC numa página Flask no seu IP, com teclado e botões:

```bash
pip install flask            # e o QEMU: sudo apt install qemu-system-x86  |  Termux: pkg install qemu-system-x86-64-headless
python3 tools/rodar_iso.py   # abre http://SEU-IP:5000/?t=CODIGO (o código aparece no terminal)
```
As portas 8080 (interface do OneMapas) e 8090 (sites) da máquina virtual ficam no seu IP também.

**Gerar**:
- GitHub: mude `ISO/pedido-build.json` (canal/versão). O Actions monta e publica nos Releases.
  A aba **Progresso da ISO** mostra o andamento e o botão de download.
- Local (Ubuntu/Debian, como root): `sudo ./os/build-iso.sh beta 0.2.0` gera a ISO em `out/`.
- Diagnóstico: no GRUB, adicione `onemapas.debug` à linha do kernel para ver o log do boot na tela.

Arquivos: `os/build-iso.sh` (monta tudo), `os/iso/rootfs/` (init, rcS, rede, disco, painel), `os/iso/boot/grub/grub.cfg`.
