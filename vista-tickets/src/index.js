// Vista Tickets - bot de tickets para Discord
try { process.loadEnvFile(); } catch { /* sem .env: usa as variáveis do ambiente */ }

const {
  Client, GatewayIntentBits, Events, EmbedBuilder, ActionRowBuilder,
  ButtonBuilder, ButtonStyle, StringSelectMenuBuilder, ModalBuilder,
  TextInputBuilder, TextInputStyle, ChannelType, PermissionFlagsBits,
  SlashCommandBuilder, AttachmentBuilder, MessageFlags,
} = require('discord.js');
const store = require('./store');

const {
  DISCORD_TOKEN, GUILD_ID, STAFF_ROLE_ID, TICKET_CATEGORY_ID, LOG_CHANNEL_ID,
} = process.env;

if (!DISCORD_TOKEN || !GUILD_ID || !STAFF_ROLE_ID) {
  console.error('Faltam variáveis: DISCORD_TOKEN, GUILD_ID e STAFF_ROLE_ID são obrigatórias (veja .env.example).');
  process.exit(1);
}

const COR = 0x5865f2;
const EPHEMERAL = { flags: MessageFlags.Ephemeral };

// Motivos que aparecem no menu do painel
const MOTIVOS = [
  { value: 'suporte', label: 'Suporte', emoji: '🛠️', description: 'Ajuda com dúvidas ou problemas' },
  { value: 'financeiro', label: 'Financeiro', emoji: '💰', description: 'Pagamentos, compras e reembolsos' },
  { value: 'denuncia', label: 'Denúncia', emoji: '🚨', description: 'Denunciar um usuário ou conteúdo' },
  { value: 'parceria', label: 'Parceria', emoji: '🤝', description: 'Propostas de parceria' },
  { value: 'outro', label: 'Outro', emoji: '📩', description: 'Outros assuntos' },
];
const motivoDe = (v) => MOTIVOS.find((m) => m.value === v) ?? MOTIVOS.at(-1);

// ---------- Comandos ----------
const commands = [
  new SlashCommandBuilder()
    .setName('painel')
    .setDescription('Envia o painel de abertura de tickets')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addChannelOption((o) => o.setName('canal').setDescription('Canal onde enviar (padrão: este)')
      .addChannelTypes(ChannelType.GuildText)),
  new SlashCommandBuilder()
    .setName('ticket')
    .setDescription('Gerencia o ticket atual')
    .addSubcommand((s) => s.setName('adicionar').setDescription('Adiciona alguém ao ticket')
      .addUserOption((o) => o.setName('usuario').setDescription('Usuário').setRequired(true)))
    .addSubcommand((s) => s.setName('remover').setDescription('Remove alguém do ticket')
      .addUserOption((o) => o.setName('usuario').setDescription('Usuário').setRequired(true)))
    .addSubcommand((s) => s.setName('renomear').setDescription('Renomeia o canal do ticket')
      .addStringOption((o) => o.setName('nome').setDescription('Novo nome').setRequired(true).setMaxLength(90)))
    .addSubcommand((s) => s.setName('fechar').setDescription('Fecha o ticket')),
].map((c) => c.toJSON());

const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages] });

client.once(Events.ClientReady, async (c) => {
  try {
    const guild = await c.guilds.fetch(GUILD_ID);
    await guild.commands.set(commands);
    console.log(`Vista Tickets online como ${c.user.tag} — comandos registrados em "${guild.name}".`);
  } catch (err) {
    console.error('Erro ao registrar comandos (o bot está no servidor GUILD_ID?):', err.message);
  }
  c.user.setActivity('🎫 Vista Tickets');
});

// ---------- Utilidades ----------
const isStaff = (member) =>
  member.roles.cache.has(STAFF_ROLE_ID) || member.permissions.has(PermissionFlagsBits.Administrator);

async function log(guild, payload) {
  if (!LOG_CHANNEL_ID) return;
  const ch = await guild.channels.fetch(LOG_CHANNEL_ID).catch(() => null);
  if (ch?.isTextBased()) await ch.send(payload).catch(() => {});
}

const escapeHtml = (s) => s.replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));

async function gerarTranscricao(channel, ticket) {
  const msgs = [];
  let before;
  while (msgs.length < 2000) {
    const lote = await channel.messages.fetch({ limit: 100, before });
    if (!lote.size) break;
    msgs.push(...lote.values());
    before = lote.last().id;
  }
  msgs.reverse();
  const linhas = msgs.map((m) => {
    const anexos = [...m.attachments.values()]
      .map((a) => `<a href="${escapeHtml(a.url)}">${escapeHtml(a.name)}</a>`).join(' ');
    const embeds = m.embeds.map((e) => escapeHtml([e.title, e.description].filter(Boolean).join(' — '))).join('<br>');
    return `<div class="m"><span class="t">${m.createdAt.toLocaleString('pt-BR')}</span> `
      + `<b>${escapeHtml(m.author.tag)}</b><div>${escapeHtml(m.content || '')}${embeds ? `<div class="e">${embeds}</div>` : ''}${anexos ? `<div>${anexos}</div>` : ''}</div></div>`;
  }).join('\n');
  const html = `<!doctype html><meta charset="utf-8"><title>Ticket #${ticket.number}</title>
<style>body{font-family:system-ui,sans-serif;background:#313338;color:#dbdee1;padding:16px}
.m{padding:6px 0;border-bottom:1px solid #3f4147}.t{color:#949ba4;font-size:12px}.e{border-left:3px solid #5865f2;padding-left:8px;margin-top:4px}a{color:#00a8fc}</style>
<h2>Vista Tickets — #${ticket.number} (${escapeHtml(motivoDe(ticket.motivo).label)})</h2>
${linhas}`;
  return new AttachmentBuilder(Buffer.from(html, 'utf8'), { name: `ticket-${ticket.number}.html` });
}

function painelPayload() {
  const embed = new EmbedBuilder()
    .setColor(COR)
    .setTitle('🎫 Vista Tickets')
    .setDescription('Precisa de ajuda? Escolha o motivo no menu abaixo e um ticket privado será aberto para você falar com a equipe.')
    .setFooter({ text: 'Vista Tickets' });
  const menu = new StringSelectMenuBuilder()
    .setCustomId('vt:abrir')
    .setPlaceholder('Selecione o motivo do ticket')
    .addOptions(MOTIVOS.map(({ value, label, emoji, description }) => ({ value, label, emoji, description })));
  return { embeds: [embed], components: [new ActionRowBuilder().addComponents(menu)] };
}

function botoesTicket(assumido = false) {
  return new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId('vt:assumir').setLabel(assumido ? 'Assumido' : 'Assumir')
      .setEmoji('🙋').setStyle(ButtonStyle.Success).setDisabled(assumido),
    new ButtonBuilder().setCustomId('vt:fechar').setLabel('Fechar').setEmoji('🔒').setStyle(ButtonStyle.Danger),
  );
}

// ---------- Ações ----------
async function abrirTicket(interaction, motivo, descricao) {
  const { guild, user } = interaction;
  const existente = store.openByUser(guild.id, user.id);
  if (existente) {
    const ch = await guild.channels.fetch(existente[0]).catch(() => null);
    if (ch) return interaction.reply({ content: `Você já tem um ticket aberto: ${ch}`, ...EPHEMERAL });
    store.remove(existente[0]); // canal apagado manualmente
  }

  await interaction.deferReply(EPHEMERAL);
  const numero = store.nextNumber();
  const nomeUser = user.username.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 20) || 'user';
  const canal = await guild.channels.create({
    name: `ticket-${String(numero).padStart(4, '0')}-${nomeUser}`,
    type: ChannelType.GuildText,
    parent: TICKET_CATEGORY_ID || null,
    topic: `Ticket #${numero} de ${user.tag} (${user.id}) — ${motivoDe(motivo).label}`,
    permissionOverwrites: [
      { id: guild.roles.everyone.id, deny: [PermissionFlagsBits.ViewChannel] },
      { id: user.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ReadMessageHistory, PermissionFlagsBits.AttachFiles] },
      { id: STAFF_ROLE_ID, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ReadMessageHistory, PermissionFlagsBits.AttachFiles, PermissionFlagsBits.ManageMessages] },
      { id: client.user.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ReadMessageHistory, PermissionFlagsBits.ManageChannels] },
    ],
  });

  store.create(canal.id, {
    guildId: guild.id, number: numero, ownerId: user.id, motivo,
    claimedBy: null, createdAt: Date.now(),
  });

  const m = motivoDe(motivo);
  const embed = new EmbedBuilder()
    .setColor(COR)
    .setTitle(`${m.emoji} Ticket #${numero} — ${m.label}`)
    .setDescription(`Olá ${user}, obrigado por entrar em contato! A equipe vai te responder em breve.\n\n**Descrição:**\n${descricao}`)
    .setTimestamp();
  await canal.send({ content: `${user} <@&${STAFF_ROLE_ID}>`, embeds: [embed], components: [botoesTicket()] });

  await interaction.editReply({ content: `✅ Seu ticket foi aberto: ${canal}` });
  await log(guild, { embeds: [new EmbedBuilder().setColor(0x57f287).setTitle('Ticket aberto')
    .setDescription(`#${numero} por ${user} em ${canal}\nMotivo: ${m.label}`).setTimestamp()] });
}

async function fecharTicket(interaction) {
  const { channel, guild, user } = interaction;
  const ticket = store.get(channel.id);
  if (!ticket) return interaction.reply({ content: 'Este canal não é um ticket.', ...EPHEMERAL });
  if (ticket.closing) return interaction.reply({ content: 'Este ticket já está sendo fechado.', ...EPHEMERAL });
  store.update(channel.id, { closing: true });

  await interaction.reply({ content: `🔒 Ticket fechado por ${user}. Gerando transcrição… o canal será apagado em 5 segundos.` });

  try {
    const transcricao = await gerarTranscricao(channel, ticket);
    const embed = new EmbedBuilder().setColor(0xed4245).setTitle('Ticket fechado')
      .setDescription(`#${ticket.number} de <@${ticket.ownerId}>\nMotivo: ${motivoDe(ticket.motivo).label}\nFechado por: ${user}`
        + (ticket.claimedBy ? `\nAtendido por: <@${ticket.claimedBy}>` : ''))
      .setTimestamp();
    await log(guild, { embeds: [embed], files: [transcricao] });
    const dono = await client.users.fetch(ticket.ownerId).catch(() => null);
    await dono?.send({ content: `Seu ticket #${ticket.number} em **${guild.name}** foi fechado. Segue a transcrição:`, files: [transcricao] }).catch(() => {});
  } catch (err) {
    console.error('Erro na transcrição:', err);
  }

  store.remove(channel.id);
  setTimeout(() => channel.delete('Ticket fechado').catch(() => {}), 5000);
}

// ---------- Interações ----------
client.on(Events.InteractionCreate, async (interaction) => {
  try {
    if (!interaction.inGuild()) return;

    // Menu do painel -> modal de descrição
    if (interaction.isStringSelectMenu() && interaction.customId === 'vt:abrir') {
      const motivo = interaction.values[0];
      const modal = new ModalBuilder().setCustomId(`vt:modal:${motivo}`).setTitle(`Ticket — ${motivoDe(motivo).label}`);
      modal.addComponents(new ActionRowBuilder().addComponents(
        new TextInputBuilder().setCustomId('descricao').setLabel('Descreva o que você precisa')
          .setStyle(TextInputStyle.Paragraph).setMinLength(5).setMaxLength(1000).setRequired(true),
      ));
      await interaction.showModal(modal);
      // reseta a seleção do menu para poder escolher de novo
      await interaction.message.edit(painelPayload()).catch(() => {});
      return;
    }

    if (interaction.isModalSubmit() && interaction.customId.startsWith('vt:modal:')) {
      const motivo = interaction.customId.split(':')[2];
      return abrirTicket(interaction, motivo, interaction.fields.getTextInputValue('descricao'));
    }

    if (interaction.isButton()) {
      if (interaction.customId === 'vt:assumir') {
        const ticket = store.get(interaction.channelId);
        if (!ticket) return interaction.reply({ content: 'Este canal não é um ticket.', ...EPHEMERAL });
        if (!isStaff(interaction.member)) return interaction.reply({ content: 'Só a equipe pode assumir tickets.', ...EPHEMERAL });
        store.update(interaction.channelId, { claimedBy: interaction.user.id });
        await interaction.update({ components: [botoesTicket(true)] });
        return interaction.followUp({ content: `🙋 ${interaction.user} assumiu este ticket.` });
      }
      if (interaction.customId === 'vt:fechar') {
        const ticket = store.get(interaction.channelId);
        if (!ticket) return interaction.reply({ content: 'Este canal não é um ticket.', ...EPHEMERAL });
        return interaction.reply({
          content: 'Tem certeza que deseja fechar este ticket?',
          components: [new ActionRowBuilder().addComponents(
            new ButtonBuilder().setCustomId('vt:confirmar').setLabel('Sim, fechar').setStyle(ButtonStyle.Danger),
          )],
          ...EPHEMERAL,
        });
      }
      if (interaction.customId === 'vt:confirmar') return fecharTicket(interaction);
      return;
    }

    if (!interaction.isChatInputCommand()) return;

    if (interaction.commandName === 'painel') {
      const canal = interaction.options.getChannel('canal') ?? interaction.channel;
      await canal.send(painelPayload());
      return interaction.reply({ content: `✅ Painel enviado em ${canal}.`, ...EPHEMERAL });
    }

    if (interaction.commandName === 'ticket') {
      const ticket = store.get(interaction.channelId);
      if (!ticket) return interaction.reply({ content: 'Use este comando dentro de um ticket.', ...EPHEMERAL });
      const sub = interaction.options.getSubcommand();
      if (sub === 'fechar') return fecharTicket(interaction);
      if (!isStaff(interaction.member)) return interaction.reply({ content: 'Só a equipe pode usar este comando.', ...EPHEMERAL });

      if (sub === 'adicionar') {
        const alvo = interaction.options.getUser('usuario');
        await interaction.channel.permissionOverwrites.edit(alvo.id, { ViewChannel: true, SendMessages: true, ReadMessageHistory: true, AttachFiles: true });
        return interaction.reply({ content: `➕ ${alvo} foi adicionado ao ticket.` });
      }
      if (sub === 'remover') {
        const alvo = interaction.options.getUser('usuario');
        if (alvo.id === ticket.ownerId) return interaction.reply({ content: 'Não dá para remover o dono do ticket.', ...EPHEMERAL });
        await interaction.channel.permissionOverwrites.delete(alvo.id);
        return interaction.reply({ content: `➖ ${alvo} foi removido do ticket.` });
      }
      if (sub === 'renomear') {
        const nome = interaction.options.getString('nome');
        await interaction.channel.setName(nome);
        return interaction.reply({ content: `✏️ Ticket renomeado para **${interaction.channel.name}**.` });
      }
    }
  } catch (err) {
    console.error('Erro na interação:', err);
    const msg = { content: '❌ Ocorreu um erro. Verifique se o bot tem as permissões necessárias.', ...EPHEMERAL };
    if (interaction.isRepliable()) {
      (interaction.deferred || interaction.replied ? interaction.followUp(msg) : interaction.reply(msg)).catch(() => {});
    }
  }
});

// Ticket cujo canal foi apagado manualmente
client.on(Events.ChannelDelete, (channel) => {
  if (store.get(channel.id)) store.remove(channel.id);
});

process.on('unhandledRejection', (err) => console.error('Erro não tratado:', err));

client.login(DISCORD_TOKEN);
