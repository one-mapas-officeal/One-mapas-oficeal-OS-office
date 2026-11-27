# 🎫 Vista Tickets

Bot de tickets para Discord (discord.js v14).

## O que ele faz

- **/painel** — envia um painel com menu de motivos (Suporte, Financeiro, Denúncia, Parceria, Outro).
- O membro escolhe o motivo, escreve a descrição num formulário e ganha um **canal privado** (`ticket-0001-nome`) visível só para ele e para a equipe.
- Um ticket aberto por pessoa.
- Botões no ticket: **🙋 Assumir** (só a equipe) e **🔒 Fechar** (com confirmação).
- Ao fechar: gera a **transcrição em HTML**, manda no canal de logs e na DM do dono, e apaga o canal.
- **/ticket adicionar | remover | renomear | fechar** dentro do ticket.

## Criar o bot no Discord

1. Acesse https://discord.com/developers/applications → **New Application** → nome `Vista Tickets`.
2. Aba **Bot** → **Reset Token** e copie o token.
3. Aba **OAuth2 → URL Generator**: marque `bot` e `applications.commands`;
   permissões: *Manage Channels*, *View Channels*, *Send Messages*, *Manage Messages*,
   *Read Message History*, *Attach Files*, *Embed Links*, *Manage Roles*.
   Abra a URL gerada e adicione o bot ao seu servidor.
4. No Discord, ative o **Modo desenvolvedor** (Configurações → Avançado) para copiar IDs
   (botão direito → Copiar ID) do servidor, do cargo da equipe, da categoria e do canal de logs.

## Rodar

```bash
cd vista-tickets
cp .env.example .env   # preencha os valores
npm install
npm start
```

Funciona também no **Termux** (Android): `pkg install nodejs` e os mesmos comandos acima.

### Variáveis

| Variável | Obrigatória | O que é |
|---|---|---|
| `DISCORD_TOKEN` | sim | Token do bot |
| `GUILD_ID` | sim | ID do servidor |
| `STAFF_ROLE_ID` | sim | Cargo da equipe |
| `TICKET_CATEGORY_ID` | não | Categoria dos tickets |
| `LOG_CHANNEL_ID` | não | Canal de logs/transcrições |

Os dados (numeração e tickets abertos) ficam em `data/tickets.json`.
