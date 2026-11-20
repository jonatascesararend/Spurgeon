// App do celular do Studio Spurgeon (Remoto.md, Fase R1: só ver).
// Sem framework nem build: o PC serve estes arquivos como estão.

const $ = (id) => document.getElementById(id);
const tela = $("tela");
let atualizador = null; // o setInterval da tela atual
let sistema = null; // o último /api/sistema

// --- utilidades -----------------------------------------------------------------------------

/** Cria um elemento: h("div", {class: "x"}, "texto", outroElemento). Texto nunca vira HTML. */
function h(tag, atributos = {}, ...filhos) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(atributos || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat()) {
    if (f === null || f === undefined || f === false) continue;
    el.append(f instanceof Node ? f : document.createTextNode(String(f)));
  }
  return el;
}

function avisar(texto) {
  const t = h("div", { class: "toast", role: "status" }, texto);
  document.body.append(t);
  setTimeout(() => t.remove(), 3200);
}

class ErroDaApi extends Error {
  constructor(status, mensagem) { super(mensagem); this.status = status; }
}

async function api(caminho, opcoes = {}) {
  let r;
  try {
    r = await fetch(caminho, { cache: "no-store", ...opcoes });
  } catch (e) {
    marcarPonto("fora");
    throw new ErroDaApi(0, "Sem conexão com o PC. O Tailscale está ligado neste celular?");
  }
  if (r.status === 503) {
    const d = await r.json().catch(() => ({}));
    if (d.erro === "dormindo") mostrarDormindo(true);
    throw new ErroDaApi(503, d.mensagem || "O PC está dormindo.");
  }
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new ErroDaApi(r.status, d.erro || (r.status === 403 ? "Acesso negado." : `Erro ${r.status}`));
  }
  marcarPonto("ok");
  const tipo = r.headers.get("content-type") || "";
  return tipo.includes("json") ? r.json() : r.text();
}

const post = (caminho, corpo) => api(caminho, {
  method: "POST",
  headers: corpo ? { "Content-Type": "application/json" } : {},
  body: corpo ? JSON.stringify(corpo) : undefined,
});

function marcarPonto(estado) {
  const p = $("ponto");
  p.className = "ponto " + estado;
  p.title = { ok: "PC conectado", dormindo: "PC dormindo", fora: "Sem conexão com o PC" }[estado] || "";
}

function mostrarDormindo(dormindo) {
  const faixa = $("faixa");
  faixa.replaceChildren();
  faixa.hidden = !dormindo;
  if (dormindo) {
    marcarPonto("dormindo");
    faixa.append("O PC está dormindo: não processa nem avisa.",
      h("button", { onclick: async () => { await post("/api/sistema/acordar"); mostrarDormindo(false); rotear(); } }, "Acordar"));
  }
}

const enc = encodeURIComponent;
const dataBr = (iso) => (iso ? iso.split("-").reverse().join("/") : "");
function duracao(s) {
  s = Math.round(s);
  const m = Math.floor(s / 60), r = s % 60;
  return m ? `${m} min${r ? ` ${String(r).padStart(2, "0")} s` : ""}` : `${r} s`;
}

function classeDaSituacao(texto) {
  if (/erro/i.test(texto)) return "erro";
  if (/revis/i.test(texto)) return "aviso";
  if (/conclu/i.test(texto)) return "ok";
  if (/andamento|etapas prontas/i.test(texto)) return "azul";
  return "";
}

function cabecalho(titulo, subtitulo = "", voltarPara = null) {
  $("titulo").textContent = titulo;
  $("subtitulo").textContent = subtitulo;
  const v = $("voltar");
  v.hidden = voltarPara === null;
  v.onclick = () => { location.hash = voltarPara || ""; };
  document.title = titulo === "Studio Spurgeon" ? titulo : `${titulo} · Spurgeon`;
}

function repetir(fn, ms) {
  pararRepeticao();
  atualizador = setInterval(() => { if (!document.hidden) fn().catch(() => {}); }, ms);
}
function pararRepeticao() { if (atualizador) clearInterval(atualizador); atualizador = null; }

function mostrarErro(e) {
  tela.replaceChildren(h("div", { class: "vazio-lista" }, e.message || String(e),
    h("button", { class: "botao secundario", onclick: rotear }, "Tentar de novo")));
}

// --- telas ----------------------------------------------------------------------------------

async function telaProjetos() {
  cabecalho("Studio Spurgeon", "Projetos no PC");
  const desenhar = async () => {
    const lista = await api("/api/projetos");
    if (!lista.length) {
      tela.replaceChildren(h("div", { class: "vazio-lista" }, "Nenhum projeto no PC ainda."));
      return;
    }
    tela.replaceChildren(...lista.map((p) => h("a", { class: "cartao", href: `#/p/${enc(p.id)}` },
      h("div", { class: "nome" }, p.nome),
      h("div", { class: "meta" }, `${dataBr(p.data)} · ${p.detalhes}`),
      h("div", { class: "chips" }, h("span", { class: `chip ${classeDaSituacao(p.situacao)}` }, p.situacao)),
    )));
  };
  await desenhar();
  repetir(desenhar, 20000);
}

const ICONE_DA_ETAPA = {
  concluida: "✓", em_andamento: "◐", aguardando_revisao: "✋", erro: "✕", cancelada: "–", pendente: "·",
};

function desenharAndamento(caixa, a) {
  const etapas = a.etapas.map((e) => h("div", { class: `etapa ${e.status}` },
    h("span", { class: "icone" }, ICONE_DA_ETAPA[e.status] || "·"),
    h("div", { class: "nome-etapa" }, e.nome,
      e.status === "em_andamento" && e.progresso > 0
        ? h("div", { class: "barra" }, h("div", { style: `width:${Math.round(e.progresso * 100)}%` }))
        : null,
      e.erro ? h("div", { class: "meta" }, e.erro) : null),
  ));
  const log = h("details", {}, h("summary", {}, "Últimas linhas do log"), h("pre", { class: "log" }, a.log.join("\n") || "(vazio)"));
  caixa.replaceChildren(h("div", { class: "cartao" }, ...etapas, log));
}

function linhaDoShort(idProjeto, s) {
  const chips = [h("span", { class: "chip" }, h("span", { class: "cor", style: `background:${s.categoria.cor}` }), s.categoria.rotulo),
    h("span", { class: "chip" }, duracao(s.duracao))];
  if (s.postado) chips.push(h("span", { class: "chip ok" }, `✔ Postado ${s.postado_rotulo}`.trim()));
  else if (s.agendado_rotulo) chips.push(h("span", { class: "chip azul" }, `📅 ${s.agendado_rotulo}`));
  if (s.renderizado) chips.push(h("span", { class: "chip ok" }, "▶ Renderizado"));
  if (s.origem === "manual") chips.push(h("span", { class: "chip" }, "Shorts livre"));
  return h("a", { class: `cartao short ${s.incluir ? "" : "desmarcado"}`, href: `#/p/${enc(idProjeto)}/s/${s.id}` },
    h("span", { class: `num ${s.numero ? "" : "fora"}` }, s.numero ?? "–"),
    h("div", { class: "corpo" }, h("div", { class: "nome" }, s.titulo), h("div", { class: "chips" }, ...chips)),
  );
}

let filtroDosShorts = "marcados";

async function telaProjeto(id) {
  const caixaAndamento = h("div");
  const caixaShorts = h("div");
  const projeto = await api(`/api/projetos/${enc(id)}`);
  cabecalho(projeto.nome, dataBr(projeto.data), "");
  const situacao = h("div", { class: "chips" });
  const pintarSituacao = (a) => situacao.replaceChildren(
    h("span", { class: `chip ${classeDaSituacao(a.situacao)}` }, a.situacao),
    h("span", { class: "chip" }, projeto.resultados));
  pintarSituacao(projeto.andamento);
  desenharAndamento(caixaAndamento, projeto.andamento);

  const segmentos = h("div", { class: "segmentos" });
  let shorts = [];
  const pintarShorts = () => {
    segmentos.replaceChildren(...[["marcados", "Marcados"], ["renderizados", "Renderizados"], ["todos", "Todos"]].map(([k, r]) =>
      h("button", { class: filtroDosShorts === k ? "ativo" : "", onclick: () => { filtroDosShorts = k; pintarShorts(); } }, r)));
    const visiveis = shorts.filter((s) => filtroDosShorts === "todos" || (filtroDosShorts === "renderizados" ? s.renderizado : s.incluir));
    caixaShorts.replaceChildren(...(visiveis.length
      ? visiveis.map((s) => linhaDoShort(id, s))
      : [h("div", { class: "vazio-lista" }, shorts.length ? "Nenhum Short neste filtro." : "Este projeto ainda não tem Shorts.")]));
  };
  shorts = await api(`/api/projetos/${enc(id)}/shorts`);
  pintarShorts();

  const etapasAbertas = projeto.andamento.em_andamento || projeto.andamento.esperando_revisao;
  const blocoEtapas = h("details", { open: etapasAbertas || null }, h("summary", {}, "Etapas do processamento"), caixaAndamento);
  tela.replaceChildren(situacao, blocoEtapas, h("h2", {}, `Shorts (${shorts.filter((s) => s.incluir).length} marcados)`), segmentos, caixaShorts);

  let andava = projeto.andamento.em_andamento;
  repetir(async () => {
    const a = await api(`/api/projetos/${enc(id)}/andamento`);
    pintarSituacao(a);
    desenharAndamento(caixaAndamento, a);
    if (andava || a.em_andamento) { shorts = await api(`/api/projetos/${enc(id)}/shorts`); pintarShorts(); }
    andava = a.em_andamento;
  }, projeto.andamento.em_andamento ? 4000 : 15000);
}

function lerModo() {
  try { return localStorage.getItem("modoDoShort") || "ouvir"; } catch (_) { return "ouvir"; }
}
function guardarModo(modo) {
  try { localStorage.setItem("modoDoShort", modo); } catch (_) { /* sem armazenamento: tudo bem */ }
}
const minSeg = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Ouvir com a legenda: o PC monta só o áudio do Short; a palavra falada acende. Devolve a limpeza. */
async function montarAudicao(area, base) {
  area.replaceChildren(h("div", { class: "bloco" }, "Carregando a legenda…"));
  const leg = await api(`${base}/legenda`);
  const palavras = leg.frases.flat();
  if (!leg.fonte) {
    area.replaceChildren(h("div", { class: "bloco aviso-bloco" }, leg.aviso));
    return () => {};
  }
  const audio = h("audio", { src: `${base}/audio`, preload: "auto" });
  const estado = h("span", { class: "estado-audio" }, "Preparando o áudio no PC…");
  const tempo = h("span", { class: "tempo" }, `0:00 / ${minSeg(leg.duracao)}`);
  const barra = h("input", { type: "range", min: "0", max: String(leg.duracao), step: "0.05", value: "0", class: "barra-tempo", "aria-label": "Posição" });
  const tocar = h("button", { class: "botao-redondo", "aria-label": "Tocar" }, "▶");
  const VELOCIDADES = [1, 1.25, 1.5, 2];
  let vel = 0;
  const velocidade = h("button", { class: "botao-pequeno", "aria-label": "Velocidade" }, "1×");
  const voltar = h("button", { class: "botao-pequeno", "aria-label": "Voltar 3 segundos" }, "↺ 3 s");

  const spans = [];
  const linhas = leg.frases.map((frase) => h("p", { class: "frase" }, ...frase.flatMap((p, k) => {
    const span = h("span", { class: "palavra", onclick: () => { audio.currentTime = p.i; audio.play().catch(() => {}); } }, p.t);
    spans.push(span);
    return k ? [" ", span] : [span];
  })));
  // a frase de cada palavra (para acender a linha inteira)
  let n = 0;
  const fraseDe = [];
  leg.frases.forEach((frase, i) => { for (let k = 0; k < frase.length; k++) fraseDe[n++] = i; });

  const barraDoAudio = h("div", { class: "audicao-barra" },
    h("div", { class: "audicao-linha" }, tocar, h("div", { class: "audicao-meio" }, barra, h("div", { class: "audicao-info" }, tempo, estado)), voltar, velocidade));
  const aviso = leg.aviso ? h("div", { class: "bloco aviso-bloco" }, leg.aviso) : null;
  area.replaceChildren(barraDoAudio, aviso || "", h("div", { class: "legenda-viva" }, ...linhas), audio);
  barraDoAudio.style.top = `${document.querySelector(".topo").offsetHeight}px`;

  let atual = -1;
  let quadro = 0;
  let arrastando = false;
  const acender = () => {
    const t = audio.currentTime;
    if (!arrastando) barra.value = String(t);
    tempo.textContent = `${minSeg(t)} / ${minSeg(leg.duracao)}`;
    // a última palavra que já começou (busca binária: as palavras estão em ordem)
    let lo = 0, hi = palavras.length - 1, achada = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (palavras[m].i <= t + 0.03) { achada = m; lo = m + 1; } else hi = m - 1; }
    if (achada !== atual) {
      if (atual >= 0) { spans[atual].classList.remove("atual"); linhas[fraseDe[atual]].classList.remove("atual"); }
      if (achada >= 0) {
        spans[achada].classList.add("atual");
        const linha = linhas[fraseDe[achada]];
        if (!linha.classList.contains("atual")) {
          linha.classList.add("atual");
          linha.scrollIntoView({ block: "center", behavior: "smooth" });
        }
      }
      atual = achada;
    }
  };
  const laco = () => { acender(); if (!audio.paused) quadro = requestAnimationFrame(laco); };
  audio.addEventListener("play", () => { tocar.textContent = "❚❚"; tocar.setAttribute("aria-label", "Pausar"); cancelAnimationFrame(quadro); laco(); });
  audio.addEventListener("pause", () => { tocar.textContent = "▶"; tocar.setAttribute("aria-label", "Tocar"); acender(); });
  audio.addEventListener("seeked", acender);
  audio.addEventListener("waiting", () => { estado.textContent = "Carregando…"; });
  audio.addEventListener("canplay", () => { estado.textContent = ""; });
  audio.addEventListener("error", () => { estado.textContent = "O PC não conseguiu montar o áudio."; });
  tocar.addEventListener("click", () => (audio.paused ? audio.play().catch(() => {}) : audio.pause()));
  voltar.addEventListener("click", () => { audio.currentTime = Math.max(0, audio.currentTime - 3); });
  velocidade.addEventListener("click", () => {
    vel = (vel + 1) % VELOCIDADES.length;
    audio.playbackRate = VELOCIDADES[vel];
    velocidade.textContent = `${String(VELOCIDADES[vel]).replace(".", ",")}×`;
  });
  barra.addEventListener("input", () => { arrastando = true; tempo.textContent = `${minSeg(+barra.value)} / ${minSeg(leg.duracao)}`; });
  barra.addEventListener("change", () => { arrastando = false; audio.currentTime = +barra.value; });
  return () => { cancelAnimationFrame(quadro); audio.pause(); audio.removeAttribute("src"); audio.load(); };
}

let limparTela = null; // o que a tela atual precisa desligar ao sair (áudio tocando)

async function telaShort(id, corte) {
  const lista = await api(`/api/projetos/${enc(id)}/shorts`);
  const s = lista.find((x) => String(x.id) === String(corte));
  if (!s) throw new Error("Este Short não existe mais no projeto.");
  cabecalho(s.numero ? `Short ${s.numero}` : "Short desmarcado", duracao(s.duracao), `#/p/${enc(id)}`);
  const base = `/api/projetos/${enc(id)}/shorts/${s.id}`;
  let modo = s.renderizado ? lerModo() : "ouvir";
  const area = h("div");
  const seletor = s.renderizado ? h("div", { class: "segmentos" }) : null;
  const texto = h("div", { class: "bloco" }, h("h3", {}, s.legenda ? "Legenda (corrigida)" : "Texto do trecho"), h("p", {}, s.legenda || s.texto));
  const mostrar = async () => {
    if (limparTela) limparTela();
    limparTela = null;
    if (modo === "video") {
      area.replaceChildren(h("div", { class: "player" },
        h("video", { src: `${base}/video`, controls: true, playsinline: true, preload: "metadata" })), texto);
    } else {
      try { limparTela = await montarAudicao(area, base); } catch (e) {
        area.replaceChildren(h("div", { class: "bloco aviso-bloco" }, e.message || String(e)));
      }
    }
  };
  const pintarSeletor = () => seletor && seletor.replaceChildren(
    ...[["ouvir", "🎧 Ouvir com a legenda"], ["video", "▶ Vídeo pronto"]].map(([k, r]) =>
      h("button", { class: modo === k ? "ativo" : "", onclick: () => {
        if (modo === k) return;
        modo = k; guardarModo(k); pintarSeletor(); mostrar();
      } }, r)));
  const chips = linhaDoShort(id, s).querySelector(".chips");
  const blocos = [
    h("div", { class: "bloco" }, h("p", { class: "titulo-short" }, s.titulo), chips),
    s.descricao ? h("div", { class: "bloco" }, h("h3", {}, "Descrição"), h("p", {}, s.descricao)) : null,
    s.hashtags.length ? h("div", { class: "bloco" }, h("h3", {}, "Hashtags"), h("p", {}, s.hashtags.join(" "))) : null,
  ];
  tela.replaceChildren(...(seletor ? [seletor] : []), area, ...blocos);
  pintarSeletor();
  await mostrar();
}

// --- roteador -------------------------------------------------------------------------------

async function rotear() {
  pararRepeticao();
  if (limparTela) { limparTela(); limparTela = null; }
  window.scrollTo(0, 0);
  const partes = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  try {
    if (partes[0] === "p" && partes[1] && partes[2] === "s" && partes[3]) await telaShort(partes[1], partes[3]);
    else if (partes[0] === "p" && partes[1]) await telaProjeto(partes[1]);
    else await telaProjetos();
  } catch (e) {
    mostrarErro(e);
  }
}

// --- menu: avisos, dormir, encerrar ---------------------------------------------------------

const deB64url = (s) => {
  const b = atob((s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(b, (c) => c.charCodeAt(0));
};
const iguais = (a, b) => a && b && a.byteLength === b.byteLength && new Uint8Array(a).every((x, i) => x === b[i]);

async function ativarAvisos() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) throw new Error("Este navegador não recebe avisos.");
  const permissao = await Notification.requestPermission();
  if (permissao !== "granted") throw new Error("Sem permissão para avisos: libere nas configurações do Chrome.");
  const registro = await navigator.serviceWorker.ready;
  const chave = deB64url(await api("/api/avisos/chave"));
  let inscricao = await registro.pushManager.getSubscription();
  if (inscricao && !iguais(inscricao.options.applicationServerKey, chave)) {
    await inscricao.unsubscribe(); // era de outra chave (ex.: a página de teste)
    inscricao = null;
  }
  inscricao = inscricao || await registro.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: chave });
  await post("/api/avisos/inscrever", inscricao.toJSON());
}

async function abrirMenu() {
  $("fundo").hidden = false;
  $("folha").hidden = false;
  try {
    sistema = await api("/api/sistema");
  } catch (e) {
    $("info-pc").textContent = e.message;
    return;
  }
  $("info-pc").textContent = `Versão ${sistema.versao} · ${sistema.usuario} · ` +
    `${sistema.aparelhos_com_aviso} aparelho(s) com aviso`;
  $("dormir").textContent = sistema.dormindo ? "Acordar o PC" : "Pôr o PC para dormir";
  $("ligar-avisos").textContent = sistema.avisos_ligados ? "Pausar os avisos" : "Voltar a mandar avisos";
}
function fecharMenu() { $("fundo").hidden = true; $("folha").hidden = true; }

function acao(botao, fn) {
  $(botao).addEventListener("click", async (ev) => {
    ev.currentTarget.disabled = true;
    try { await fn(); } catch (e) { avisar(e.message || String(e)); } finally { $(botao).disabled = false; }
  });
}

$("abrir-menu").addEventListener("click", abrirMenu);
$("fechar-menu").addEventListener("click", fecharMenu);
$("fundo").addEventListener("click", fecharMenu);
acao("ativar-avisos", async () => { await ativarAvisos(); avisar("Avisos ativados neste celular."); await abrirMenu(); });
acao("testar-avisos", async () => {
  const r = await post("/api/avisos/teste");
  avisar(r.aparelhos ? `Aviso enviado (${r.entregues} de ${r.aparelhos}).` : "Nenhum aparelho com avisos: ative primeiro.");
});
acao("ligar-avisos", async () => { await post(sistema.avisos_ligados ? "/api/avisos/desligar" : "/api/avisos/ligar"); await abrirMenu(); });
acao("dormir", async () => {
  const r = await post(sistema.dormindo ? "/api/sistema/acordar" : "/api/sistema/dormir");
  mostrarDormindo(r.dormindo);
  fecharMenu();
  if (!r.dormindo) rotear();
});
acao("encerrar", async () => {
  if (!confirm("Encerrar o servidor? Ele só volta no PC (ou quando o Windows reiniciar).")) return;
  await post("/api/sistema/encerrar");
  fecharMenu();
  pararRepeticao();
  marcarPonto("fora");
  tela.replaceChildren(h("div", { class: "vazio-lista" }, "Servidor encerrado. Para usar de novo, abra-o no PC."));
});

// --- início ---------------------------------------------------------------------------------

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
window.addEventListener("hashchange", rotear);
document.addEventListener("visibilitychange", () => { if (!document.hidden) api("/api/sistema").then((s) => mostrarDormindo(s.dormindo)).catch(() => {}); });
api("/api/sistema").then((s) => { sistema = s; mostrarDormindo(s.dormindo); }).catch(() => {});
rotear();
