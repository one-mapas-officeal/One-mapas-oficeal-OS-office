/* OneMapas OS: área de trabalho do PC. O PC roda o site; o Render faz a ponte com os visitantes. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------- preferências deste navegador ---------------- */
const pref = {
  get(k, d) { try { const v = localStorage.getItem("onemapas." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("onemapas." + k, JSON.stringify(v)); } catch {} },
};

/* ---------------- API do servidor ---------------- */
const api = {
  status: null,
  async req(metodo, rota, dados) {
    try {
      const r = await fetch("/api" + rota, {
        method: metodo, cache: "no-store",
        headers: { "Content-Type": "application/json", "X-OneMapas": "1" },
        body: dados ? JSON.stringify(dados) : undefined,
      });
      let corpo = {}; try { corpo = await r.json(); } catch {}
      return { status: r.status, ok: r.ok, ...corpo };
    } catch (e) { return { status: 0, ok: false, mensagem: "O OneMapas OS deste PC não está rodando." }; }
  },
  get(r) { return this.req("GET", r); },
  post(r, d) { return this.req("POST", r, d); },
  put(r, d) { return this.req("PUT", r, d); },
  del(r) { return this.req("DELETE", r); },
  async ping() {
    const s = await this.get("/status");
    this.status = s.ok ? s : null;
    const no_ar = !!this.status && this.status.tunel.tunel === "conectado";
    const b = $("#bandeja-backend");
    b.classList.toggle("on", no_ar);
    b.title = no_ar ? `PC conectado ao Render · ${this.status.sites} site(s) no ar` : "PC desconectado do Render: os sites estão fora do ar";
    if (this.status) $("#menu-dono").textContent = "Dono: " + this.status.dono;
    return !!this.status;
  },
};
const DOMINIO_SITES = () => (api.status?.dominio_sites || "onemapasos.oficeal.com");

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
const janelas = new Map();

function abrirApp(id, opcoes = {}) {
  const app = APPS[id];
  if (!app) return;
  const chaveJanela = id + (opcoes.site ? ":" + opcoes.site : "");
  for (const [jid, j] of janelas) if (j.chave === chaveJanela) { focar(jid, true); return; }
  const jid = "j" + (++contJanela);
  const el = document.createElement("div");
  el.className = "janela";
  const w = Math.min(app.largura || 640, innerWidth - 20), h = Math.min(app.altura || 460, innerHeight - 90);
  const desloc = (janelas.size % 6) * 26;
  el.style.width = w + "px"; el.style.height = h + "px";
  el.style.left = Math.max(10, (innerWidth - w) / 2 - 60 + desloc) + "px";
  el.style.top = Math.max(10, (innerHeight - h) / 2 - 60 + desloc) + "px";
  const titulo = app.nome + (opcoes.site ? " · " + opcoes.site : "");
  el.innerHTML = `
    <div class="titulo"><span class="t">${app.emoji} ${esc(titulo)}</span>
      <div class="botoes">
        <button class="minimizar" title="Minimizar">—</button>
        <button class="maximizar" title="Maximizar">▢</button>
        <button class="fechar" title="Fechar">✕</button>
      </div></div>
    <div class="conteudo"></div>`;
  $("#janelas").appendChild(el);

  const botao = document.createElement("button");
  botao.innerHTML = `<span>${app.emoji}</span><span class="t">${esc(titulo)}</span>`;
  botao.onclick = () => {
    const j = janelas.get(jid);
    if (j.el.classList.contains("min")) { j.el.classList.remove("min"); focar(jid); }
    else if (botao.classList.contains("ativa")) minimizar(jid);
    else focar(jid);
  };
  $("#tarefas").appendChild(botao);
  janelas.set(jid, { el, app: id, botao, chave: chaveJanela });

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
  j.botao.classList.remove("min");
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
  arquivos: { nome: "Área de Trabalho", emoji: "📁", largura: 640, altura: 460, abrir: appArquivos },
  editor: { nome: "Editor", emoji: "📝", largura: 860, altura: 600, abrir: appEditor },
  novidades: { nome: "Novidades", emoji: "📰", largura: 560, altura: 520, abrir: appNovidades },
  iso: { nome: "Progresso da ISO", emoji: "💿", largura: 620, altura: 560, abrir: appIso },
  rede: { nome: "Conexão com o Render", emoji: "📶", largura: 540, altura: 440, abrir: appRede },
  terminal: { nome: "Terminal", emoji: "⌨️", largura: 640, altura: 400, abrir: appTerminal },
  config: { nome: "Configurações", emoji: "⚙️", largura: 600, altura: 600, abrir: appConfig },
  sobre: { nome: "Sobre o OneMapas OS", emoji: "ℹ️", largura: 460, altura: 380, abrir: appSobre },
};
const FIXADOS = ["criador", "arquivos", "novidades", "iso", "rede", "terminal", "config"];

/* ---- Criador de Sites ---- */
function appCriador(c) {
  c.innerHTML = `
    <h2>Criar um novo site</h2>
    <p class="mudo">Digite o endereço que você quer. O Render confere se ele está livre. O site vai rodar neste PC.</p>
    <div class="campo"><label>URL do site</label>
      <div class="url-linha"><span>https://</span><input type="text" id="cs-nome" placeholder="oi" maxlength="63" style="text-align:right"><span style="padding-right:10px">.${esc(DOMINIO_SITES())}</span></div></div>
    <div class="linha"><button class="btn prim" id="cs-verificar">Está disponível?</button></div>
    <div id="cs-resp"></div>
    <div id="cs-form" hidden>
      <h3>Dados do site</h3>
      <div class="campo"><label>Título</label><input type="text" id="cs-titulo" maxlength="120"></div>
      <div class="campo"><label>Texto da página inicial</label><textarea id="cs-desc" rows="3" placeholder="Bem-vindo ao meu site!"></textarea></div>
      <div class="linha"><label>Cor principal <input type="color" id="cs-cor" value="#2563eb"></label></div>
      <p class="mudo">A pasta do site será criada na <b>Área de Trabalho</b> deste PC (Meus Sites OneMapas). Este PC é o dono e o servidor do site.</p>
      <div class="linha"><button class="btn prim" id="cs-criar">Criar site</button></div>
    </div>`;
  const nome = $("#cs-nome", c), resp = $("#cs-resp", c), form = $("#cs-form", c);
  let ultimo = null;
  nome.addEventListener("input", () => { nome.value = nome.value.toLowerCase().replace(/[^a-z0-9-]/g, ""); form.hidden = true; resp.innerHTML = ""; });
  nome.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#cs-verificar", c).click(); });

  $("#cs-verificar", c).onclick = async () => {
    if (!nome.value) { nome.focus(); return; }
    resp.innerHTML = `<div class="resposta">Perguntando ao Render: a URL <b>https://${esc(nome.value)}.${esc(DOMINIO_SITES())}</b> está disponível?…</div>`;
    const v = await api.get("/verificar?nome=" + encodeURIComponent(nome.value));
    if (v.status === 0) { resp.innerHTML = `<div class="resposta nao">${esc(v.mensagem)}</div>`; return; }
    ultimo = v;
    const sim = v.disponivel;
    resp.innerHTML = `<div class="resposta ${sim ? "sim" : "nao"}">
      Resposta: <b class="${sim ? "sim" : "nao"}">${sim ? "SIM" : "NÃO"}</b>. ${sim ? esc(v.mensagem) : "<b>Escolha outra URL</b>"}
      ${!sim && v.mensagem !== "Escolha outra URL" ? `<div class="mudo">${esc(v.mensagem.replace(/\.? ?Escolha outra URL$/, ""))}</div>` : ""}
      <ul>${(v.checagens || []).map((k) => `<li>${esc(k.onde)}: ${esc(k.resultado)}</li>`).join("")}</ul></div>`;
    form.hidden = !sim;
    if (sim) $("#cs-titulo", c).value ||= v.nome; else nome.select();
  };

  $("#cs-criar", c).onclick = async () => {
    if (!ultimo?.disponivel) return;
    $("#cs-criar", c).disabled = true;
    const r = await api.post("/criar", { nome: ultimo.nome, titulo: $("#cs-titulo", c).value, descricao: $("#cs-desc", c).value, cor: $("#cs-cor", c).value });
    $("#cs-criar", c).disabled = false;
    if (r.status === 201) {
      form.hidden = true;
      resp.innerHTML = `<div class="resposta sim"><b class="sim">Site criado!</b><br>📁 ${esc(r.pasta)}<br>🌐 <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url)}</a>
        <div class="mudo">O site roda neste PC. Uma cópia vai para o Supabase: se o PC desligar, os visitantes veem a cópia.</div></div>
        <div class="linha"><button class="btn prim" id="cs-editar">Editar arquivos</button><button class="btn" id="cs-ver">Ver site</button></div>`;
      $("#cs-editar", c).onclick = () => abrirApp("editor", { site: r.nome });
      $("#cs-ver", c).onclick = () => window.open(r.url, "_blank", "noopener");
      notificar("Site criado", r.url);
      carregarIcones();
    } else if (r.status === 409) {
      resp.innerHTML = `<div class="resposta nao"><b class="nao">NÃO</b>. Escolha outra URL</div>`;
      form.hidden = true;
    } else {
      resp.innerHTML = `<div class="resposta nao">${esc(r.mensagem || "Erro ao criar.")}</div>`;
    }
  };
  setTimeout(() => nome.focus(), 50);
}

/* ---- Área de Trabalho (pasta dos sites do dono) ---- */
async function appArquivos(c, _o, jid) {
  const pintar = async () => {
    const d = await api.get("/meus-sites");
    const sites = d.sites || [];
    c.innerHTML = `<h2>Área de Trabalho</h2><p class="mudo">📂 ${esc(api.status?.pasta || "Meus Sites OneMapas")} · ${sites.length} site(s)</p>
      ${sites.length ? "" : `<p>Nenhum site ainda. Abra o <b>Criador de Sites</b>.</p>`}
      <div class="arquivos-grade">${sites.map((s) => `<div class="item" data-nome="${esc(s.nome)}" title="${esc(s.titulo)}"><span class="emoji">🗂️</span>${esc(s.nome)}</div>`).join("")}</div>
      <div id="ar-det"></div>`;
    c.querySelectorAll(".item").forEach((it) => {
      it.ondblclick = () => abrirApp("editor", { site: it.dataset.nome });
      it.onclick = () => {
        const s = sites.find((x) => x.nome === it.dataset.nome);
        $("#ar-det", c).innerHTML = `<div class="cartao"><b>${esc(s.titulo)}</b><br><a href="${esc(s.url)}" target="_blank" rel="noopener" style="word-break:break-all">${esc(s.url)}</a>
          <p class="mudo">Criado ${esc((s.criado_em || "").replace("T", " ").slice(0, 16))} · alterado ${esc((s.atualizado_em || "").replace("T", " ").slice(0, 16))}</p>
          <div class="linha"><button class="btn prim" data-a="ed">Editar arquivos</button><button class="btn" data-a="fm">Abrir no gerenciador</button><button class="btn" data-a="ver">Ver online</button><button class="btn" data-a="del">Apagar site</button></div></div>`;
        $("[data-a=ed]", c).onclick = () => abrirApp("editor", { site: s.nome });
        $("[data-a=ver]", c).onclick = () => window.open(s.url, "_blank", "noopener");
        $("[data-a=fm]", c).onclick = async () => { const r = await api.post("/abrir-pasta", { nome: s.nome }); if (!r.ok) notificar("Pasta", r.mensagem || ""); };
        $("[data-a=del]", c).onclick = async () => {
          if (prompt(`Para apagar, digite o nome do site: ${s.nome}`) !== s.nome) return;
          const r = await api.del("/sites/" + s.nome);
          notificar(r.ok ? "Site apagado" : "Não apagado", r.mensagem || "");
          pintar(); carregarIcones();
        };
      };
    });
  };
  await pintar();
  janelas.get(jid).recarregar = pintar;
}

/* ---- Editor de arquivos de um site ---- */
async function appEditor(c, { site }) {
  c.style.display = "flex"; c.style.flexDirection = "column"; c.style.gap = "8px";
  c.innerHTML = `
    <div class="linha"><b>📂 ${esc(site)}</b><span class="espaco"></span>
      <button class="btn" id="ed-novo">+ Arquivo</button>
      <button class="btn" id="ed-ver">Ver site ↗</button></div>
    <div style="display:flex;gap:8px;flex:1;min-height:0">
      <div id="ed-lista" style="width:180px;overflow:auto;border:1px solid var(--borda);border-radius:6px"></div>
      <div style="flex:1;display:flex;flex-direction:column;gap:6px;min-width:0">
        <div class="linha"><span id="ed-atual" class="mudo">Escolha um arquivo</span><span class="espaco"></span>
          <button class="btn" id="ed-apagar" disabled>Apagar</button><button class="btn prim" id="ed-salvar" disabled>Salvar (Ctrl+S)</button></div>
        <textarea id="ed-texto" spellcheck="false" style="flex:1;font-family:ui-monospace,monospace;font-size:13px;resize:none;tab-size:2" disabled></textarea>
      </div>
    </div>`;
  let atual = null, sujo = false;
  const texto = $("#ed-texto", c);
  const lista = async () => {
    const r = await api.get(`/sites/${site}/arquivos`);
    if (!r.ok) { c.innerHTML = `<p>${esc(r.mensagem || "Sem acesso.")}</p>`; return; }
    $("#ed-lista", c).innerHTML = r.arquivos.map((a) => `<div class="item-arq" data-c="${esc(a.caminho)}" style="padding:6px 8px;cursor:pointer;${a.caminho === atual ? "background:var(--suave);font-weight:600" : ""}">${a.caminho.endsWith(".html") ? "📄" : a.caminho.endsWith(".css") ? "🎨" : a.caminho.endsWith(".js") ? "⚙️" : "📃"} ${esc(a.caminho)}</div>`).join("");
    c.querySelectorAll(".item-arq").forEach((d) => d.onclick = () => abrir(d.dataset.c));
  };
  const abrir = async (cam) => {
    if (sujo && !confirm("Descartar alterações não salvas?")) return;
    const r = await api.get(`/sites/${site}/arquivo?caminho=${encodeURIComponent(cam)}`);
    if (!r.ok) return notificar("Erro", r.mensagem);
    atual = cam; sujo = false;
    texto.value = r.conteudo; texto.disabled = false;
    $("#ed-salvar", c).disabled = false; $("#ed-apagar", c).disabled = false;
    $("#ed-atual", c).textContent = cam;
    lista();
  };
  const salvar = async () => {
    if (!atual) return;
    const r = await api.put(`/sites/${site}/arquivo`, { caminho: atual, conteudo: texto.value });
    if (r.ok) { sujo = false; $("#ed-atual", c).textContent = atual; notificar("Salvo", `${site}/${atual}`); }
    else notificar("Não salvo", r.mensagem);
  };
  texto.addEventListener("input", () => { if (!sujo) { sujo = true; $("#ed-atual", c).textContent = atual + " •"; } });
  texto.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); salvar(); }
    if (e.key === "Tab") { e.preventDefault(); const p = texto.selectionStart; texto.setRangeText("  ", p, texto.selectionEnd, "end"); }
  });
  $("#ed-salvar", c).onclick = salvar;
  $("#ed-ver", c).onclick = async () => { const s = ((await api.get("/meus-sites")).sites || []).find((x) => x.nome === site); if (s?.url) window.open(s.url, "_blank", "noopener"); };
  $("#ed-novo", c).onclick = async () => {
    const cam = prompt("Nome do arquivo (ex.: sobre.html, js/app.js):");
    if (!cam) return;
    const r = await api.put(`/sites/${site}/arquivo`, { caminho: cam.trim(), conteudo: "" });
    if (!r.ok) return notificar("Erro", r.mensagem);
    await lista(); abrir(cam.trim());
  };
  $("#ed-apagar", c).onclick = async () => {
    if (!atual || !confirm(`Apagar ${atual}?`)) return;
    const r = await api.del(`/sites/${site}/arquivo?caminho=${encodeURIComponent(atual)}`);
    if (!r.ok) return notificar("Erro", r.mensagem);
    atual = null; sujo = false; texto.value = ""; texto.disabled = true;
    $("#ed-salvar", c).disabled = true; $("#ed-apagar", c).disabled = true; $("#ed-atual", c).textContent = "Escolha um arquivo";
    lista();
  };
  await lista();
  if (!atual) abrir("index.html");
}

/* ---- Novidades ---- */
async function appNovidades(c) {
  c.innerHTML = `<p class="mudo">Carregando novidades…</p>`;
  const d = await api.get("/novidades");
  if (!d.ok) { c.innerHTML = `<p>${esc(d.erro || d.mensagem || "Erro")}</p>`; return; }
  c.innerHTML = `<h2>Novidades</h2>` + (d.novidades || []).map((n) => `
    <div class="cartao"><span class="tag">${esc(n.versao)}</span><span class="tag cinza">${esc(n.canal)}</span><span class="mudo">${esc(n.data)}</span>
      <h3>${esc(n.titulo)}</h3><ul>${(n.itens || []).map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`).join("");
}

/* ---- Progresso da ISO ---- */
async function appIso(c, _o, jid) {
  let canal = pref.get("isoCanal", null);
  const pintar = async () => {
    const m = await api.get("/manifest");
    if (!m.ok || m.erro) { c.innerHTML = `<p>${esc(m.erro || m.mensagem)}</p>`; return; }
    const isos = m.isos || [];
    const canais = [...new Set(isos.map((i) => i.canal))];
    if (!canais.includes(canal)) canal = m.ultima?.canal || canais[0];
    const lista = isos.filter((i) => i.canal === canal);
    c.innerHTML = `<h2>Progresso da ISO</h2>
      <p class="mudo">Lido de <code>ISO/manifest.json</code> (${esc(m.origem)}) · atualizado ${esc((m.atualizado_em || "").replace("T", " ").replace("Z", ""))}</p>
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
    c.querySelectorAll(".abas button").forEach((b) => b.onclick = () => { canal = b.dataset.c; pref.set("isoCanal", canal); pintar(); });
  };
  await pintar();
  const t = setInterval(pintar, 60000);
  janelas.get(jid).limpar = () => clearInterval(t);
}

/* ---- Conexão com o Render ---- */
async function appRede(c) {
  const t0 = performance.now();
  await api.ping();
  const ms = Math.round(performance.now() - t0);
  const s = api.status, t = s?.tunel || {};
  const icone = { conectado: "✅ conectado", reconectando: "🔄 reconectando…", desligado: "⏸ iniciando…" };
  c.innerHTML = `<h2>Conexão com o Render</h2>
    <p class="mudo">Visitante → Render → <b>este PC</b> → Render → visitante</p>
    <table>
      <tr><td>Internet</td><td>${navigator.onLine ? "✅ conectado" : "❌ offline"}</td></tr>
      ${s ? `<tr><td>Render</td><td>${s.render_online ? "✅ online" : "❌ sem resposta"} · ${esc(s.servidor)}</td></tr>
      <tr><td>Túnel</td><td>${icone[t.tunel] || esc(t.tunel)}</td></tr>
      <tr><td>Sites neste PC</td><td>${esc(s.sites)}</td></tr>
      <tr><td>Cópia no Supabase</td><td>${{ "em dia": "✅ em dia", "com erro": "⚠️ com erro", aguardando: "⏳ aguardando" }[t.copia?.estado] || esc(t.copia?.estado)} · ${esc(t.copia?.arquivos ?? 0)} arquivo(s)${t.copia?.ultima ? " · " + esc(t.copia.ultima.slice(11, 16)) + " UTC" : ""}</td></tr>
      ${t.copia?.erro ? `<tr><td>Erro da cópia</td><td class="mudo">${esc(t.copia.erro)}</td></tr>` : ""}
      <tr><td>Pedidos atendidos</td><td>${esc(t.pedidos_atendidos)}</td></tr>
      <tr><td>Último pedido</td><td class="mudo">${esc(t.ultimo_pedido || "—")}</td></tr>
      ${t.ultimo_erro ? `<tr><td>Último erro</td><td class="mudo">${esc(t.ultimo_erro)}</td></tr>` : ""}
      <tr><td>Interface local</td><td>${ms} ms</td></tr>` : `<tr><td>OneMapas OS</td><td>❌ não está rodando</td></tr>`}
    </table>
    <div class="linha" style="margin-top:12px"><button class="btn" id="rd-at">Atualizar</button></div>`;
  $("#rd-at", c).onclick = () => appRede(c);
}

/* ---- Terminal ---- */
function appTerminal(c) {
  c.style.padding = "0";
  c.innerHTML = `<div class="terminal"><div class="saida">OneMapas OS Terminal. Digite "ajuda".\n</div><div>$ <input autocomplete="off" spellcheck="false"></div></div>`;
  const saida = $(".saida", c), inp = $("input", c), term = $(".terminal", c);
  const escrever = (t) => { saida.textContent += t + "\n"; term.scrollTop = term.scrollHeight; };
  const hist = []; let hi = 0;
  const cmds = {
    ajuda: () => "Comandos: verificar <nome>, sites, status, abrir <app>, editar <site>, tema <windows|mint|mac>, limpar, data",
    data: () => new Date().toLocaleString("pt-BR"),
    limpar: () => { saida.textContent = ""; return ""; },
    tema: (a) => { if (!TEMAS[a]) return "temas: windows, mint, mac"; aplicarTema(a); return "tema: " + a; },
    abrir: (a) => { if (!APPS[a] || a === "editor") return "apps: " + Object.keys(APPS).filter((x) => x !== "editor").join(", "); abrirApp(a); return ""; },
    editar: (a) => { if (!a) return "uso: editar oi"; abrirApp("editor", { site: a }); return ""; },
    verificar: async (a) => { if (!a) return "uso: verificar oi"; const v = await api.get("/verificar?nome=" + encodeURIComponent(a)); return `${v.url}: ${v.disponivel ? "SIM, disponível" : "NÃO. Escolha outra URL"}`; },
    sites: async () => ((await api.get("/meus-sites")).sites || []).map((s) => `${s.nome}\t${s.url}`).join("\n") || "(nenhum site)",
    status: async () => { await api.ping(); const s = api.status; return s ? `render ${s.render_online ? "online" : "offline"} · túnel ${s.tunel.tunel} · ${s.sites} site(s) · ${s.tunel.pedidos_atendidos} pedidos` : "OneMapas OS fora do ar"; },
  };
  inp.addEventListener("keydown", async (e) => {
    if (e.key === "ArrowUp" && hi > 0) { inp.value = hist[--hi]; return; }
    if (e.key === "ArrowDown") { inp.value = hist[++hi] ?? ""; hi = Math.min(hi, hist.length); return; }
    if (e.key !== "Enter") return;
    const linha = inp.value.trim(); inp.value = "";
    escrever("$ " + linha);
    if (!linha) return;
    hist.push(linha); hi = hist.length;
    const [cmd, ...args] = linha.split(/\s+/);
    const f = cmds[cmd];
    const r = f ? await f(args.join(" ")) : `comando não encontrado: ${cmd}`;
    if (r) escrever(r);
  });
  c.addEventListener("click", () => inp.focus());
  setTimeout(() => inp.focus(), 50);
}

/* ---- Configurações ---- */
const TEMAS = {
  windows: { nome: "Windows", prev: "linear-gradient(135deg,#0b3d91,#42a5f5)" },
  mint: { nome: "Linux Mint", prev: "radial-gradient(circle at 30% 20%,#8fbf5a,#2b3a1d)" },
  mac: { nome: "Mac", prev: "linear-gradient(160deg,#2b1055,#7597de,#f5b0c7)" },
};
function aplicarTema(t) {
  if (!TEMAS[t]) t = "windows";
  document.body.classList.remove(...Object.keys(TEMAS).map((k) => "tema-" + k));
  document.body.classList.add("tema-" + t);
  pref.set("tema", t);
}
async function appConfig(c) {
  const tema = pref.get("tema", "windows");
  const cfg = await api.get("/config");
  c.innerHTML = `<h2>Aparência</h2>
    <div class="temas">${Object.entries(TEMAS).map(([k, t]) => `<button data-t="${k}" class="${k === tema ? "ativa" : ""}"><div class="prev" style="background:${t.prev}"></div><div class="nome">${t.nome}</div></button>`).join("")}</div>
    <div class="linha" style="margin-top:10px"><label><input type="checkbox" id="cf-escuro" ${pref.get("escuro", false) ? "checked" : ""}> Janelas no modo escuro</label></div>
    <h2 style="margin-top:22px">Este PC</h2>
    <div class="campo"><label>Nome do dono</label><input type="text" id="cf-dono" value="${esc(cfg.dono)}"></div>
    <div class="campo"><label>Servidor Render</label><input type="text" id="cf-serv" value="${esc(cfg.servidor)}"></div>
    <p class="mudo">Pasta dos sites: ${esc(cfg.pasta)}</p>
    <h3>Chave do PC (prova que os sites são seus)</h3>
    <p class="mudo">Guarde uma cópia. Se trocar de PC, cole a chave no novo PC e copie a pasta dos sites.</p>
    <div class="linha"><input type="password" id="cf-chave" value="${esc(cfg.chave)}" style="flex:1">
      <button class="btn" id="cf-mostrar">Mostrar</button><button class="btn" id="cf-copiar">Copiar</button></div>
    <div class="linha" style="margin-top:12px"><button class="btn prim" id="cf-salvar">Salvar</button></div>`;
  c.querySelectorAll("[data-t]").forEach((b) => b.onclick = () => { aplicarTema(b.dataset.t); c.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("ativa", x === b)); });
  $("#cf-escuro", c).onchange = (e) => { document.body.classList.toggle("escuro", e.target.checked); pref.set("escuro", e.target.checked); };
  $("#cf-mostrar", c).onclick = () => { const i = $("#cf-chave", c); i.type = i.type === "password" ? "text" : "password"; };
  $("#cf-copiar", c).onclick = async () => { try { await navigator.clipboard.writeText($("#cf-chave", c).value); notificar("Chave copiada", "Guarde em lugar seguro."); } catch { $("#cf-chave", c).type = "text"; $("#cf-chave", c).select(); } };
  $("#cf-salvar", c).onclick = async () => {
    const novo = { dono: $("#cf-dono", c).value, servidor: $("#cf-serv", c).value };
    const k = $("#cf-chave", c).value.trim();
    if (k !== cfg.chave) {
      if (!/^[A-Za-z0-9_-]{32,128}$/.test(k)) return notificar("Chave inválida");
      if (!confirm("Trocar a chave deste PC? Os sites registrados com a chave antiga deixam de ser deste PC.")) return;
      novo.chave = k;
    }
    const r = await api.post("/config", novo);
    notificar(r.ok ? "Configurações salvas" : "Não salvo", r.ok ? "" : "Confira o endereço do servidor.");
    api.ping();
  };
}

function appSobre(c) {
  const s = api.status;
  c.innerHTML = `<h2>OneMapas OS</h2><p>Versão ${esc(s?.versao || "?")}</p>
    <p>Sistema da One Mapas Office para criar sites. O site roda <b>neste PC</b> (pasta na Área de Trabalho) e fica no ar em
      <b>&lt;nome&gt;.${esc(DOMINIO_SITES())}</b>. O Render faz a ponte entre os visitantes e este PC.</p>
    <p class="mudo">Visitante → Render → PC → Render → visitante · ISO: Debian 12.</p>
    <p class="mudo">Interface: OneMapas Shell (estilos Windows, Mint e Mac).</p>`;
}

/* ---------------- área de trabalho ---------------- */
async function carregarIcones() {
  const base = [
    { emoji: "🌐", rot: "Criador de Sites", app: "criador" },
    { emoji: "📁", rot: "Meus Sites", app: "arquivos" },
    { emoji: "📰", rot: "Novidades", app: "novidades" },
    { emoji: "💿", rot: "Progresso da ISO", app: "iso" },
  ];
  const sites = (await api.get("/meus-sites")).sites || [];
  $("#icones").innerHTML = [...base, ...sites.map((s) => ({ emoji: "🗂️", rot: s.nome, site: s.nome }))]
    .map((i) => `<div class="icone" ${i.app ? `data-app="${i.app}"` : `data-site="${esc(i.site)}"`}><span class="emoji">${i.emoji}</span><span class="rot">${esc(i.rot)}</span></div>`).join("");
  $("#icones").querySelectorAll(".icone").forEach((el) => el.ondblclick = () => {
    if (el.dataset.app) abrirApp(el.dataset.app); else abrirApp("editor", { site: el.dataset.site });
  });
  for (const [, j] of janelas) if (j.recarregar) j.recarregar();
}

function montarDock() {
  $("#fixados").innerHTML = FIXADOS.map((id) => `<button data-app="${id}" title="${esc(APPS[id].nome)}">${APPS[id].emoji}<span class="lbl">${esc(APPS[id].nome)}</span></button>`).join("");
  $("#fixados").querySelectorAll("button").forEach((b) => b.onclick = () => abrirApp(b.dataset.app));
}
function atualizarDock() {
  const abertos = new Set([...janelas.values()].map((j) => j.app));
  $("#fixados").querySelectorAll("button").forEach((b) => b.classList.toggle("aberto", abertos.has(b.dataset.app)));
}

function montarMenu(filtro = "") {
  $("#menu-apps").innerHTML = Object.entries(APPS).filter(([id, a]) => id !== "editor" && a.nome.toLowerCase().includes(filtro.toLowerCase()))
    .map(([id, a]) => `<button data-app="${id}"><span class="emoji">${a.emoji}</span>${esc(a.nome)}</button>`).join("");
  $("#menu-apps").querySelectorAll("button").forEach((b) => b.onclick = () => { fecharMenu(); abrirApp(b.dataset.app); });
}
function abrirMenu() { $("#menu").hidden = false; $("#menu-busca").value = ""; montarMenu(); $("#menu-busca").focus(); }
function fecharMenu() { $("#menu").hidden = true; }
$("#menu-busca").oninput = (e) => montarMenu(e.target.value);
$("#menu-busca").onkeydown = (e) => { if (e.key === "Enter") $("#menu-apps button")?.click(); if (e.key === "Escape") fecharMenu(); };

document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-acao]")?.dataset.acao;
  if (a === "menu") { $("#menu").hidden ? abrirMenu() : fecharMenu(); return; }
  if (a === "desligar") { fecharMenu(); notificar("Desligar", "Se este PC desligar, os sites saem do ar até ele voltar."); return; }
  if (!e.target.closest("#menu")) fecharMenu();
});

function relogio() {
  const d = new Date();
  const h = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  $("#bandeja-relogio").innerHTML = `${h}<br><small>${d.toLocaleDateString("pt-BR")}</small>`;
  $("#menubar-relogio").textContent = d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "short" }) + " " + h;
  $("#bandeja-rede").textContent = $("#menubar-rede").textContent = navigator.onLine ? "📶" : "🚫";
}

/* ---------------- início ---------------- */
(async function iniciar() {
  aplicarTema(pref.get("tema", "windows"));
  document.body.classList.toggle("escuro", pref.get("escuro", false));
  montarDock();
  relogio(); setInterval(relogio, 15000);
  $("#bandeja-rede").onclick = $("#menubar-rede").onclick = () => abrirApp("rede");
  const on = await api.ping();
  setInterval(() => api.ping(), 15000);
  await carregarIcones();
  if (!pref.get("jaAbriu", false)) { abrirApp("novidades"); pref.set("jaAbriu", true); }
  notificar("Bem-vindo ao OneMapas OS", on ? (api.status.render_online ? "Ligando este PC ao Render…" : "Render sem resposta. Tentando de novo…") : "O programa do PC não está rodando.");
})();
