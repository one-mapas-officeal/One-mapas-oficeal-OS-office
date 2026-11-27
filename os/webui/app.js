/* OneMapas OS: gerenciador de janelas e aplicativos */
"use strict";

const CFG_PADRAO = {
  dominio: "https://onemapasoficeal.com.br",
  repositorio: "one-mapas-officeal/One-mapas-oficeal-OS-office",
  branch: "main",
  pasta_sites: "sites",
};
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------- preferências (por navegador) ---------------- */
const pref = {
  get(k, d) { try { const v = localStorage.getItem("onemapas." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("onemapas." + k, JSON.stringify(v)); } catch {} },
};

/* ---------------- backend local ---------------- */
const backend = {
  on: false,
  async ping() {
    try {
      const r = await fetch("/api/ping", { cache: "no-store" });
      this.on = r.ok;
      if (r.ok) { const d = await r.json(); $("#menu-dono").textContent = "Dono: " + d.dono; }
    } catch { this.on = false; }
    $("#bandeja-backend").classList.toggle("on", this.on);
    $("#bandeja-backend").title = this.on ? "Backend local conectado" : "Sem backend local (modo navegador)";
    return this.on;
  },
  async get(rota) { const r = await fetch(rota, { cache: "no-store" }); return r.json(); },
  async post(rota, dados) {
    const r = await fetch(rota, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dados || {}) });
    return { status: r.status, dados: await r.json() };
  },
};

function cfgNavegador() { return { ...CFG_PADRAO, ...pref.get("cfg", {}) }; }

/* ---------------- notificações ---------------- */
function notificar(titulo, texto = "") {
  const n = document.createElement("div");
  n.className = "notif";
  n.innerHTML = `<b>${esc(titulo)}</b><div class="mudo">${esc(texto)}</div>`;
  $("#notificacoes").appendChild(n);
  setTimeout(() => n.remove(), 5000);
}

/* ---------------- gerenciador de janelas ---------------- */
let zTopo = 10, contJanela = 0;
const janelas = new Map(); // id -> {el, app, botao}

function abrirApp(id, opcoes = {}) {
  const app = APPS[id];
  if (!app) return;
  // um aplicativo = uma janela (traz para frente se já estiver aberta)
  for (const [jid, j] of janelas) if (j.app === id && !opcoes.nova) { focar(jid, true); return; }
  const jid = "j" + (++contJanela);
  const el = document.createElement("div");
  el.className = "janela";
  const w = Math.min(app.largura || 640, innerWidth - 20), h = Math.min(app.altura || 460, innerHeight - 90);
  const desloc = (janelas.size % 6) * 26;
  el.style.width = w + "px"; el.style.height = h + "px";
  el.style.left = Math.max(10, (innerWidth - w) / 2 - 60 + desloc) + "px";
  el.style.top = Math.max(10, (innerHeight - h) / 2 - 60 + desloc) + "px";
  el.innerHTML = `
    <div class="titulo"><span class="t">${app.emoji} ${esc(app.nome)}</span>
      <div class="botoes">
        <button class="minimizar" title="Minimizar">—</button>
        <button class="maximizar" title="Maximizar">▢</button>
        <button class="fechar" title="Fechar">✕</button>
      </div></div>
    <div class="conteudo"></div>`;
  $("#janelas").appendChild(el);

  const botao = document.createElement("button");
  botao.innerHTML = `<span>${app.emoji}</span><span class="t">${esc(app.nome)}</span>`;
  botao.onclick = () => {
    const j = janelas.get(jid);
    if (j.el.classList.contains("min")) { j.el.classList.remove("min"); focar(jid); }
    else if (botao.classList.contains("ativa")) minimizar(jid);
    else focar(jid);
  };
  $("#tarefas").appendChild(botao);
  janelas.set(jid, { el, app: id, botao });

  el.addEventListener("mousedown", () => focar(jid));
  $(".fechar", el).onclick = (e) => { e.stopPropagation(); fechar(jid); };
  $(".minimizar", el).onclick = (e) => { e.stopPropagation(); minimizar(jid); };
  $(".maximizar", el).onclick = (e) => { e.stopPropagation(); el.classList.toggle("max"); };
  $(".titulo", el).ondblclick = () => el.classList.toggle("max");
  arrastar(el, $(".titulo", el));

  focar(jid);
  app.abrir($(".conteudo", el), opcoes, jid);
  atualizarDock();
}

function focar(jid, restaurar) {
  const j = janelas.get(jid); if (!j) return;
  if (restaurar) j.el.classList.remove("min");
  j.el.style.zIndex = ++zTopo;
  for (const [, o] of janelas) o.botao.classList.toggle("ativa", o === j);
  $("#menubar-app").textContent = APPS[j.app].nome;
}
function minimizar(jid) { const j = janelas.get(jid); j.el.classList.add("min"); j.botao.classList.remove("ativa"); j.botao.classList.add("min"); }
function fechar(jid) {
  const j = janelas.get(jid); if (!j) return;
  if (j.limpar) j.limpar();
  j.el.remove(); j.botao.remove(); janelas.delete(jid);
  $("#menubar-app").textContent = "Área de Trabalho";
  atualizarDock();
}
function arrastar(el, alca) {
  alca.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button") || el.classList.contains("max")) return;
    const x0 = e.clientX - el.offsetLeft, y0 = e.clientY - el.offsetTop;
    alca.setPointerCapture(e.pointerId);
    const mover = (ev) => {
      el.style.left = Math.min(innerWidth - 60, Math.max(-el.offsetWidth + 80, ev.clientX - x0)) + "px";
      el.style.top = Math.min(innerHeight - 80, Math.max(0, ev.clientY - y0)) + "px";
    };
    const soltar = () => { alca.removeEventListener("pointermove", mover); alca.removeEventListener("pointerup", soltar); };
    alca.addEventListener("pointermove", mover); alca.addEventListener("pointerup", soltar);
  });
}

/* ---------------- aplicativos ---------------- */
const APPS = {
  criador: { nome: "Criador de Sites", emoji: "🌐", largura: 620, altura: 600, abrir: appCriador },
  arquivos: { nome: "Meus Sites", emoji: "📁", largura: 640, altura: 440, abrir: appArquivos },
  novidades: { nome: "Novidades", emoji: "📰", largura: 560, altura: 520, abrir: appNovidades },
  iso: { nome: "Progresso da ISO", emoji: "💿", largura: 620, altura: 560, abrir: appIso },
  rede: { nome: "Rede", emoji: "📶", largura: 560, altura: 480, abrir: appRede },
  terminal: { nome: "Terminal", emoji: "⌨️", largura: 640, altura: 400, abrir: appTerminal },
  config: { nome: "Configurações", emoji: "⚙️", largura: 600, altura: 560, abrir: appConfig },
  sobre: { nome: "Sobre o OneMapas OS", emoji: "ℹ️", largura: 460, altura: 360, abrir: appSobre },
};
const FIXADOS = ["criador", "arquivos", "novidades", "iso", "rede", "terminal", "config"];

/* ---- verificação de URL (backend ou direto pelo navegador) ---- */
async function verificarUrl(nome) {
  if (backend.on) return backend.get("/api/verificar?nome=" + encodeURIComponent(nome));
  const cfg = cfgNavegador();
  nome = nome.trim().toLowerCase().replace(/^\/+|\/+$/g, "");
  const url = cfg.dominio.replace(/\/$/, "") + "/" + nome;
  if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(nome))
    return { nome, url, disponivel: false, resposta: "não", mensagem: "Nome inválido. Escolha outra URL.", checagens: [] };
  const checagens = [];
  let ocupado = false;
  try {
    const h = { Accept: "application/vnd.github+json" };
    const tok = pref.get("token", ""); if (tok) h.Authorization = "Bearer " + tok;
    const r = await fetch(`https://api.github.com/repos/${cfg.repositorio}/contents/${cfg.pasta_sites}/${nome}?ref=${cfg.branch}`, { headers: h });
    if (r.status === 200) { ocupado = true; checagens.push({ onde: "GitHub", resultado: "pasta já existe" }); }
    else checagens.push({ onde: "GitHub", resultado: r.status === 404 ? "livre" : "HTTP " + r.status });
  } catch { checagens.push({ onde: "GitHub", resultado: "sem conexão" }); }
  checagens.push({ onde: "Render (domínio)", resultado: "só o backend local consegue checar" });
  return { nome, url, disponivel: !ocupado, resposta: ocupado ? "não" : "sim", mensagem: ocupado ? "Escolha outra URL" : `A URL ${url} está disponível!`, checagens };
}

function appCriador(c) {
  const cfg = cfgNavegador();
  c.innerHTML = `
    <h2>Criar um novo site</h2>
    <p class="mudo">Digite o endereço que você quer. O sistema pergunta ao Render e ao GitHub se ele está livre.</p>
    <div class="campo"><label>URL do site</label>
      <div class="url-linha"><span>${esc(cfg.dominio.replace(/^https?:\/\//, ""))}/</span><input type="text" id="cs-nome" placeholder="oi" maxlength="63"></div></div>
    <div class="linha"><button class="btn prim" id="cs-verificar">Está disponível?</button></div>
    <div id="cs-resp"></div>
    <div id="cs-form" hidden>
      <h3>Dados do site</h3>
      <div class="campo"><label>Título</label><input type="text" id="cs-titulo" placeholder="Meu site"></div>
      <div class="campo"><label>Texto da página inicial</label><textarea id="cs-desc" rows="3" placeholder="Bem-vindo ao meu site!"></textarea></div>
      <div class="linha">
        <label>Cor <input type="color" id="cs-cor" value="#2563eb"></label>
        <label><input type="checkbox" id="cs-pub" checked> Publicar no GitHub (o Render coloca no ar)</label>
      </div>
      <p class="mudo">A pasta do site será criada na sua Área de Trabalho, em <b>Meus Sites OneMapas</b>. Este PC é o dono do site.</p>
      <div class="linha"><button class="btn prim" id="cs-criar">Criar site</button></div>
    </div>`;
  const nome = $("#cs-nome", c), resp = $("#cs-resp", c), form = $("#cs-form", c);
  let ultimo = null;
  nome.addEventListener("input", () => { nome.value = nome.value.toLowerCase().replace(/[^a-z0-9-]/g, ""); form.hidden = true; resp.innerHTML = ""; });
  nome.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#cs-verificar", c).click(); });

  $("#cs-verificar", c).onclick = async () => {
    if (!nome.value) { nome.focus(); return; }
    resp.innerHTML = `<div class="resposta">Perguntando: a URL <b>${esc(cfg.dominio)}/${esc(nome.value)}</b> está disponível?…</div>`;
    const v = await verificarUrl(nome.value);
    ultimo = v;
    const sim = v.disponivel;
    resp.innerHTML = `<div class="resposta ${sim ? "sim" : "nao"}">
      Resposta: <b class="${sim ? "sim" : "nao"}">${sim ? "SIM" : "NÃO"}</b>. ${sim ? esc(v.mensagem) : "<b>Escolha outra URL</b>"}
      ${!sim && v.mensagem !== "Escolha outra URL" ? `<div class="mudo">${esc(v.mensagem)}</div>` : ""}
      <ul>${(v.checagens || []).map((k) => `<li>${esc(k.onde)}: ${esc(k.resultado)}</li>`).join("")}</ul></div>`;
    form.hidden = !sim;
    if (sim) $("#cs-titulo", c).value ||= v.nome; else nome.select();
  };

  $("#cs-criar", c).onclick = async () => {
    if (!ultimo?.disponivel) return;
    const dados = { nome: ultimo.nome, titulo: $("#cs-titulo", c).value, descricao: $("#cs-desc", c).value, cor: $("#cs-cor", c).value, publicar: $("#cs-pub", c).checked };
    if (!backend.on) {
      resp.innerHTML = `<div class="resposta nao">O backend local não está rodando, então não dá para criar a pasta na Área de Trabalho.<br>
        Rode <code>python3 os/backend/onemapas_server.py</code> e abra <code>http://127.0.0.1:8080</code>.</div>`;
      return;
    }
    $("#cs-criar", c).disabled = true;
    const { status, dados: r } = await backend.post("/api/criar", dados);
    $("#cs-criar", c).disabled = false;
    if (status === 201) {
      form.hidden = true;
      const pub = r.publicacao;
      resp.innerHTML = `<div class="resposta sim"><b class="sim">Site criado!</b><br>📁 ${esc(r.pasta)}<br>🌐 ${esc(r.url)}
        ${pub ? `<div class="${pub.ok ? "" : "mudo"}">${pub.ok ? "✅" : "⚠️"} ${esc(pub.mensagem)}</div>` : ""}</div>
        <div class="linha"><button class="btn" id="cs-abrir">Abrir pasta</button><button class="btn" id="cs-ver">Ver online</button></div>`;
      $("#cs-abrir", c).onclick = () => backend.post("/api/abrir-pasta", { nome: r.nome });
      $("#cs-ver", c).onclick = () => window.open(r.url, "_blank");
      notificar("Site criado", r.url);
      carregarIcones();
    } else {
      resp.innerHTML = `<div class="resposta nao"><b class="nao">NÃO</b>. Escolha outra URL<div class="mudo">${esc(r.mensagem || "")}</div></div>`;
    }
  };
  setTimeout(() => nome.focus(), 50);
}

async function appArquivos(c) {
  c.innerHTML = `<p class="mudo">Carregando…</p>`;
  if (!backend.on) { c.innerHTML = `<h2>Meus Sites</h2><p>Sem backend local: os arquivos da Área de Trabalho só aparecem quando o OneMapas OS roda no PC.</p>`; return; }
  const d = await backend.get("/api/sites");
  c.innerHTML = `<h2>Meus Sites</h2><p class="mudo">📂 ${esc(d.pasta)}</p>
    ${d.sites.length ? "" : `<p>Nenhum site ainda. Abra o <b>Criador de Sites</b>.</p>`}
    <div class="arquivos-grade">${d.sites.map((s) => `<div class="item" data-nome="${esc(s.nome)}"><span class="emoji">📁</span>${esc(s.nome)}</div>`).join("")}</div>
    <div id="ar-det"></div>`;
  c.querySelectorAll(".item").forEach((it) => it.onclick = () => {
    const s = d.sites.find((x) => x.nome === it.dataset.nome);
    $("#ar-det", c).innerHTML = `<div class="cartao"><b>${esc(s.nome)}</b><br><span class="mudo">${esc(s.url || "")}</span>
      <p>Arquivos: ${s.arquivos.map(esc).join(", ")}</p><p class="mudo">Dono: ${esc(s.dono || "")} · ${esc(s.criado_em || "")}</p>
      <div class="linha"><button class="btn" data-a="pasta">Abrir pasta</button><button class="btn" data-a="pub">Publicar de novo</button><button class="btn" data-a="ver">Ver online</button></div></div>`;
    $("[data-a=pasta]", c).onclick = () => backend.post("/api/abrir-pasta", { nome: s.nome });
    $("[data-a=ver]", c).onclick = () => window.open(s.url, "_blank");
    $("[data-a=pub]", c).onclick = async () => { const r = await backend.post("/api/publicar", { nome: s.nome }); notificar(r.dados.ok ? "Publicado" : "Não publicado", r.dados.mensagem); };
  });
}

async function buscarJson(rotaBackend, caminhoRepo, local) {
  if (backend.on) { try { return await backend.get(rotaBackend); } catch {} }
  const cfg = cfgNavegador();
  for (const url of [`https://raw.githubusercontent.com/${cfg.repositorio}/${cfg.branch}/${caminhoRepo}`, local]) {
    try { const r = await fetch(url, { cache: "no-store" }); if (r.ok) return await r.json(); } catch {}
  }
  return { erro: "Não consegui carregar " + caminhoRepo };
}

async function appNovidades(c) {
  c.innerHTML = `<p class="mudo">Carregando novidades…</p>`;
  const d = await buscarJson("/api/novidades", "os/webui/novidades.json", "novidades.json");
  if (d.erro) { c.innerHTML = `<p>${esc(d.erro)}</p>`; return; }
  c.innerHTML = `<h2>Novidades</h2>` + (d.novidades || []).map((n) => `
    <div class="cartao"><span class="tag">${esc(n.versao)}</span><span class="tag cinza">${esc(n.canal)}</span><span class="mudo">${esc(n.data)}</span>
      <h3>${esc(n.titulo)}</h3><ul>${(n.itens || []).map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`).join("");
}

async function appIso(c, _o, jid) {
  const pintar = async () => {
    const m = await buscarJson("/api/manifest", "ISO/manifest.json", "../../ISO/manifest.json");
    if (m.erro) { c.innerHTML = `<p>${esc(m.erro)}</p>`; return; }
    const isos = m.isos || [];
    const canais = [...new Set(isos.map((i) => i.canal))];
    let canal = pref.get("isoCanal", canais[0]);
    if (!canais.includes(canal)) canal = canais[0];
    const desenhar = () => {
      const lista = isos.filter((i) => i.canal === canal);
      c.innerHTML = `<h2>Progresso da ISO</h2>
        <p class="mudo">Lido de <code>ISO/manifest.json</code> (${esc(m.origem || "github")}) · atualizado ${esc(m.atualizado_em || "")}</p>
        <div class="abas">${canais.map((k) => `<button data-c="${esc(k)}" class="${k === canal ? "ativa" : ""}">${esc(k)}</button>`).join("")}</div>
        ${lista.map((i) => `
          <div class="cartao">
            <div class="linha"><b>${esc(i.nome)}</b><span class="espaco"></span><span class="tag">${esc(i.status)}</span></div>
            <div class="barra-prog"><div style="width:${Number(i.progresso) || 0}%"></div></div>
            <div class="mudo">${Number(i.progresso) || 0}% · versão ${esc(i.versao)} · pasta <code>${esc(i.pasta)}</code></div>
            <ul class="etapas">${(i.etapas || []).map((e) => `<li>${e.feito ? "✅" : "⏳"} ${esc(e.nome)}</li>`).join("")}</ul>
            <table>
              <tr><td>Arquitetura</td><td>${esc(i.arquitetura || "")}</td></tr>
              <tr><td>Base</td><td>${esc(i.base || "")}</td></tr>
              <tr><td>Tamanho</td><td>${i.tamanho_mb ? esc(i.tamanho_mb) + " MB" : "—"}</td></tr>
              <tr><td>SHA-256</td><td style="word-break:break-all">${esc(i.sha256 || "—")}</td></tr>
            </table>
            ${i.download ? `<p><a class="btn prim" href="${esc(i.download)}" target="_blank" rel="noopener">⬇ Baixar ISO</a></p>` : `<p class="mudo">Download disponível quando o build terminar.</p>`}
          </div>`).join("")}`;
      c.querySelectorAll(".abas button").forEach((b) => b.onclick = () => { canal = b.dataset.c; pref.set("isoCanal", canal); desenhar(); });
    };
    desenhar();
  };
  await pintar();
  const t = setInterval(pintar, 60000); // atualiza sozinho a cada minuto
  janelas.get(jid).limpar = () => clearInterval(t);
}

async function appRede(c) {
  c.innerHTML = `<p class="mudo">Verificando a rede…</p>`;
  if (!backend.on) {
    c.innerHTML = `<h2>Rede</h2><p>Navegador ${navigator.onLine ? "online ✅" : "offline ❌"}.</p><p class="mudo">Os detalhes (placas de rede, Wi-Fi) aparecem quando o backend local está rodando.</p>`;
    return;
  }
  const d = await backend.get("/api/rede");
  c.innerHTML = `<h2>Rede</h2><p>Internet: ${d.internet ? "conectado ✅" : "sem conexão ❌"}</p>
    <h3>Placas de rede</h3>
    <table><tr><th>Dispositivo</th><th>Tipo</th><th>Estado</th><th>Conexão</th></tr>
    ${d.dispositivos.map((x) => `<tr><td>${esc(x.dispositivo)}</td><td>${esc(x.tipo)}</td><td>${esc(x.estado)}</td><td>${esc(x.conexao)}</td></tr>`).join("")}</table>
    ${d.nmcli ? `<h3>Redes Wi-Fi</h3>
    ${d.wifi.length ? `<table>${d.wifi.map((w) => `<tr><td>${w.em_uso ? "✅ " : ""}${esc(w.ssid)}</td><td>${esc(w.sinal)}%</td><td>${esc(w.seguranca)}</td>
      <td><button class="btn" data-ssid="${esc(w.ssid)}">Conectar</button></td></tr>`).join("")}</table>` : `<p class="mudo">Nenhuma rede Wi-Fi encontrada.</p>`}` :
    `<p class="mudo">NetworkManager não instalado: Wi-Fi indisponível.</p>`}
    <div class="linha" style="margin-top:12px"><button class="btn" id="rd-at">Atualizar</button></div>`;
  $("#rd-at", c).onclick = () => appRede(c);
  c.querySelectorAll("[data-ssid]").forEach((b) => b.onclick = async () => {
    const senha = prompt("Senha da rede " + b.dataset.ssid + " (deixe vazio se for aberta):") ?? null;
    if (senha === null) return;
    const r = await backend.post("/api/rede/wifi", { ssid: b.dataset.ssid, senha });
    notificar(r.dados.ok ? "Conectado" : "Falhou", r.dados.mensagem);
    appRede(c);
  });
}

function appTerminal(c) {
  c.style.padding = "0";
  c.innerHTML = `<div class="terminal"><div class="saida">OneMapas OS Terminal. Digite "ajuda".\n</div><div>$ <input autocomplete="off" spellcheck="false"></div></div>`;
  const saida = $(".saida", c), inp = $("input", c), term = $(".terminal", c);
  const escrever = (t) => { saida.textContent += t + "\n"; term.scrollTop = term.scrollHeight; };
  const cmds = {
    ajuda: () => "Comandos: ajuda, verificar <nome>, sites, rede, abrir <app>, tema <windows|mint|mac>, limpar, data",
    data: () => new Date().toLocaleString("pt-BR"),
    limpar: () => { saida.textContent = ""; return ""; },
    tema: (a) => { if (!TEMAS[a]) return "temas: windows, mint, mac"; aplicarTema(a); return "tema: " + a; },
    abrir: (a) => { if (!APPS[a]) return "apps: " + Object.keys(APPS).join(", "); abrirApp(a); return ""; },
    verificar: async (a) => { if (!a) return "uso: verificar oi"; const v = await verificarUrl(a); return `${v.url}: ${v.resposta.toUpperCase()}${v.disponivel ? "" : ". Escolha outra URL"}`; },
    sites: async () => backend.on ? (await backend.get("/api/sites")).sites.map((s) => s.nome).join("\n") || "(nenhum)" : "backend offline",
    rede: async () => backend.on ? (await backend.get("/api/rede")).dispositivos.map((d) => `${d.dispositivo}\t${d.tipo}\t${d.estado}`).join("\n") : (navigator.onLine ? "online" : "offline"),
  };
  inp.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const linha = inp.value.trim(); inp.value = "";
    escrever("$ " + linha);
    if (!linha) return;
    const [cmd, ...args] = linha.split(/\s+/);
    const f = cmds[cmd];
    const r = f ? await f(args.join(" ")) : `comando não encontrado: ${cmd}`;
    if (r) escrever(r);
  });
  c.addEventListener("click", () => inp.focus());
  setTimeout(() => inp.focus(), 50);
}

const TEMAS = {
  windows: { nome: "Windows", prev: "linear-gradient(135deg,#0b3d91,#42a5f5)" },
  mint: { nome: "Linux Mint", prev: "radial-gradient(circle at 30% 20%,#8fbf5a,#2b3a1d)" },
  mac: { nome: "Mac", prev: "linear-gradient(160deg,#2b1055,#7597de,#f5b0c7)" },
};
function aplicarTema(t) {
  document.body.classList.remove(...Object.keys(TEMAS).map((k) => "tema-" + k));
  document.body.classList.add("tema-" + t);
  pref.set("tema", t);
}

async function appConfig(c) {
  const tema = pref.get("tema", "windows");
  const cfgB = backend.on ? await backend.get("/api/config") : null;
  const cfg = cfgB || cfgNavegador();
  c.innerHTML = `<h2>Aparência</h2>
    <div class="temas">${Object.entries(TEMAS).map(([k, t]) => `<button data-t="${k}" class="${k === tema ? "ativa" : ""}"><div class="prev" style="background:${t.prev}"></div><div class="nome">${t.nome}</div></button>`).join("")}</div>
    <div class="linha" style="margin-top:10px"><label><input type="checkbox" id="cf-escuro" ${pref.get("escuro", false) ? "checked" : ""}> Janelas no modo escuro</label></div>
    <h2 style="margin-top:20px">Sites e GitHub</h2>
    <div class="campo"><label>Domínio (Render)</label><input type="text" id="cf-dom" value="${esc(cfg.dominio)}"></div>
    <div class="campo"><label>Repositório GitHub</label><input type="text" id="cf-repo" value="${esc(cfg.repositorio)}"></div>
    <div class="linha">
      <div class="campo" style="flex:1"><label>Branch</label><input type="text" id="cf-branch" value="${esc(cfg.branch)}"></div>
      <div class="campo" style="flex:1"><label>Pasta dos sites</label><input type="text" id="cf-pasta" value="${esc(cfg.pasta_sites)}"></div>
    </div>
    ${cfgB ? `<div class="campo"><label>Nome do dono (este PC)</label><input type="text" id="cf-dono" value="${esc(cfg.dono)}"></div>` : ""}
    <div class="campo"><label>Token do GitHub (permissão "Contents: read and write") ${cfgB?.github_token ? "· já definido" : ""}</label>
      <input type="password" id="cf-token" placeholder="${cfgB ? "fica salvo só neste PC (~/.config/onemapas)" : "fica salvo só neste navegador"}"></div>
    <div class="linha"><button class="btn prim" id="cf-salvar">Salvar</button></div>`;
  c.querySelectorAll("[data-t]").forEach((b) => b.onclick = () => { aplicarTema(b.dataset.t); c.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("ativa", x === b)); });
  $("#cf-escuro", c).onchange = (e) => { document.body.classList.toggle("escuro", e.target.checked); pref.set("escuro", e.target.checked); };
  $("#cf-salvar", c).onclick = async () => {
    const novo = { dominio: $("#cf-dom", c).value, repositorio: $("#cf-repo", c).value, branch: $("#cf-branch", c).value, pasta_sites: $("#cf-pasta", c).value };
    const tok = $("#cf-token", c).value.trim();
    if (cfgB) {
      if ($("#cf-dono", c)) novo.dono = $("#cf-dono", c).value;
      if (tok) novo.github_token = tok;
      await backend.post("/api/config", novo);
    } else {
      pref.set("cfg", novo);
      if (tok) pref.set("token", tok);
    }
    notificar("Configurações salvas");
  };
}

function appSobre(c) {
  c.innerHTML = `<h2>OneMapas OS</h2><p>Versão 0.1.0 beta</p>
    <p>Sistema da One Mapas Office para criar sites. Cada site fica numa pasta na Área de Trabalho deste PC (o dono) e é publicado em <b>onemapasoficeal.com.br/&lt;nome&gt;</b>.</p>
    <p class="mudo">Base Linux: Debian 12 (live-build) · Interface: OneMapas Shell (estilos Windows, Mint e Mac).</p>
    <p class="mudo">Backend local: ${backend.on ? "conectado ✅" : "desconectado (modo navegador)"}</p>`;
}

/* ---------------- área de trabalho ---------------- */
async function carregarIcones() {
  const base = [
    { emoji: "🌐", rot: "Criador de Sites", app: "criador" },
    { emoji: "📁", rot: "Meus Sites", app: "arquivos" },
    { emoji: "📰", rot: "Novidades", app: "novidades" },
    { emoji: "💿", rot: "Progresso da ISO", app: "iso" },
  ];
  let sites = [];
  if (backend.on) { try { sites = (await backend.get("/api/sites")).sites; } catch {} }
  $("#icones").innerHTML = [...base, ...sites.map((s) => ({ emoji: "🗂️", rot: s.nome, site: s.nome }))]
    .map((i) => `<div class="icone" ${i.app ? `data-app="${i.app}"` : `data-site="${esc(i.site)}"`}><span class="emoji">${i.emoji}</span><span class="rot">${esc(i.rot)}</span></div>`).join("");
  $("#icones").querySelectorAll(".icone").forEach((el) => el.ondblclick = () => {
    if (el.dataset.app) abrirApp(el.dataset.app);
    else backend.post("/api/abrir-pasta", { nome: el.dataset.site });
  });
}

function montarDock() {
  $("#fixados").innerHTML = FIXADOS.map((id) => `<button data-app="${id}" title="${esc(APPS[id].nome)}">${APPS[id].emoji}<span class="lbl">${esc(APPS[id].nome)}</span></button>`).join("");
  $("#fixados").querySelectorAll("button").forEach((b) => b.onclick = () => abrirApp(b.dataset.app));
}
function atualizarDock() {
  const abertos = new Set([...janelas.values()].map((j) => j.app));
  $("#fixados").querySelectorAll("button").forEach((b) => b.classList.toggle("aberto", abertos.has(b.dataset.app)));
}

function montarMenu() {
  const lista = (filtro = "") => {
    $("#menu-apps").innerHTML = Object.entries(APPS).filter(([, a]) => a.nome.toLowerCase().includes(filtro.toLowerCase()))
      .map(([id, a]) => `<button data-app="${id}"><span class="emoji">${a.emoji}</span>${esc(a.nome)}</button>`).join("");
    $("#menu-apps").querySelectorAll("button").forEach((b) => b.onclick = () => { fecharMenu(); abrirApp(b.dataset.app); });
  };
  lista();
  $("#menu-busca").oninput = (e) => lista(e.target.value);
  $("#menu-busca").onkeydown = (e) => { if (e.key === "Enter") $("#menu-apps button")?.click(); if (e.key === "Escape") fecharMenu(); };
}
function abrirMenu() { $("#menu").hidden = false; $("#menu-busca").value = ""; montarMenu(); $("#menu-busca").focus(); }
function fecharMenu() { $("#menu").hidden = true; }

document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-acao]")?.dataset.acao;
  if (a === "menu") { $("#menu").hidden ? abrirMenu() : fecharMenu(); return; }
  if (a === "desligar") { fecharMenu(); notificar("Desligar", "No ISO, use o botão de energia do PC ou o comando 'poweroff'."); return; }
  if (!e.target.closest("#menu")) fecharMenu();
});
document.addEventListener("keydown", (e) => { if (e.key === "Meta" || (e.ctrlKey && e.key === "Escape")) abrirMenu(); });

function relogio() {
  const d = new Date();
  const h = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  $("#bandeja-relogio").innerHTML = `${h}<br><small>${d.toLocaleDateString("pt-BR")}</small>`;
  $("#menubar-relogio").textContent = d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "short" }) + " " + h;
  const on = navigator.onLine;
  $("#bandeja-rede").textContent = $("#menubar-rede").textContent = on ? "📶" : "🚫";
}

/* ---------------- início ---------------- */
(async function iniciar() {
  aplicarTema(pref.get("tema", "windows"));
  document.body.classList.toggle("escuro", pref.get("escuro", false));
  montarDock();
  relogio(); setInterval(relogio, 15000);
  $("#bandeja-rede").onclick = $("#menubar-rede").onclick = () => abrirApp("rede");
  await backend.ping();
  setInterval(() => backend.ping(), 30000);
  await carregarIcones();
  if (!pref.get("jaAbriu", false)) { abrirApp("novidades"); pref.set("jaAbriu", true); }
  notificar("Bem-vindo ao OneMapas OS", backend.on ? "Backend local conectado." : "Modo navegador: rode o backend para criar sites na Área de Trabalho.");
})();
