// App do celular do Studio Spurgeon (Remoto.md: Fase R1 "só ver" e Fase R2 "editar").
// Sem framework nem build: o PC serve estes arquivos como estão.

const $ = (id) => document.getElementById(id);
const tela = $("tela");
let atualizador = null; // o setInterval da tela atual
let sistema = null; // o último /api/sistema
let limparTela = null; // o que a tela atual precisa desligar ao sair (áudio tocando)
let voltarDoShort = ""; // de onde se abriu o Short (o projeto ou a aprovação dos cortes)

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

/** Troca o conteúdo de `el` pelos filhos dados, ignorando null/false/undefined (o
 * `replaceChildren` do navegador os escreveria como texto: "null"). */
function encher(el, ...filhos) {
  el.replaceChildren(...filhos.flat().filter((f) => f !== null && f !== undefined && f !== false));
}

function avisar(texto) {
  const t = h("div", { class: "toast", role: "status" }, texto);
  document.body.append(t);
  setTimeout(() => t.remove(), 3600);
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
  encher(faixa);
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
const minSeg = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const plural = (n, um, varios) => `${n} ${n === 1 ? um : varios}`;

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
  encher(tela, h("div", { class: "vazio-lista" }, e.message || String(e),
    h("button", { class: "botao secundario", onclick: rotear }, "Tentar de novo")));
}

/** Uma folha de escolhas na parte de baixo da tela; resolve com o valor escolhido (ou null). */
function escolher(titulo, opcoes) {
  return new Promise((resolver) => {
    const fundo = h("div", { class: "fundo" });
    const fechar = (valor) => { fundo.remove(); folha.remove(); resolver(valor); };
    const folha = h("div", { class: "folha" }, h("h2", {}, titulo),
      ...opcoes.map(([valor, rotulo, classe]) => h("button", { class: `botao ${classe || "secundario"}`, onclick: () => fechar(valor) }, rotulo)),
      h("button", { class: "botao fechar", onclick: () => fechar(null) }, "Cancelar"));
    fundo.addEventListener("click", () => fechar(null));
    document.body.append(fundo, folha);
  });
}

// --- a trava do projeto: o celular abriu, o PC salva e trava (Remoto.md, R8) ---------------

let sessao = { projeto: "", timer: null };

async function tomarATrava(id) {
  return post(`/api/projetos/${enc(id)}/sessao`);
}

async function entrarNoProjeto(id) {
  if (sessao.projeto === id) return;
  sairDoProjeto();
  sessao.projeto = id;
  const r = await tomarATrava(id).catch(() => null);
  if (r && r.pc === "salvou") avisar("O PC salvou o que estava aberto no Studio e travou a edição lá.");
  if (r && r.pc === "sem_resposta") avisar("A janela do PC não respondeu: confira se o Studio de lá não tinha mudanças abertas.");
  sessao.timer = setInterval(() => { if (!document.hidden && sessao.projeto) tomarATrava(sessao.projeto).catch(() => {}); }, 30000);
}

function sairDoProjeto() {
  if (sessao.timer) clearInterval(sessao.timer);
  if (sessao.projeto) post(`/api/projetos/${enc(sessao.projeto)}/sessao/soltar`).catch(() => {});
  sessao = { projeto: "", timer: null };
}

/** Uma mudança no projeto: se a trava venceu (app em segundo plano), toma de novo e repete. */
async function editar(caminho, corpo) {
  try {
    return await post(caminho, corpo);
  } catch (e) {
    if (e.status === 409 && e.message === "sem_trava" && sessao.projeto) {
      await tomarATrava(sessao.projeto);
      return post(caminho, corpo);
    }
    throw e;
  }
}

// --- telas ----------------------------------------------------------------------------------

async function telaProjetos() {
  sairDoProjeto();
  cabecalho("Studio Spurgeon", "Projetos no PC");
  const desenhar = async () => {
    const lista = await api("/api/projetos");
    if (!lista.length) {
      encher(tela, h("div", { class: "vazio-lista" }, "Nenhum projeto no PC ainda."));
      return;
    }
    encher(tela, ...lista.map((p) => h("a", { class: "cartao", href: `#/p/${enc(p.id)}` },
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
  encher(caixa, h("div", { class: "cartao" }, ...etapas, log));
}

function chipsDoShort(s) {
  const chips = [h("span", { class: "chip" }, h("span", { class: "cor", style: `background:${s.categoria.cor}` }), s.categoria.rotulo),
    h("span", { class: "chip" }, duracao(s.duracao))];
  if (s.postado) chips.push(h("span", { class: "chip ok" }, `✔ Postado ${s.postado_rotulo}`.trim()));
  else if (s.agendado_rotulo) chips.push(h("span", { class: "chip azul" }, `📅 ${s.agendado_rotulo}`));
  if (s.situacao === "alterado") chips.push(h("span", { class: "chip aviso" }, "✎ Mudou: renderizar de novo"));
  else if (s.renderizado) chips.push(h("span", { class: "chip ok" }, "▶ Renderizado"));
  else if (s.incluir) chips.push(h("span", { class: "chip" }, "Não renderizado"));
  if (s.reserva && !s.incluir) chips.push(h("span", { class: "chip" }, "Reserva"));
  if (s.origem === "manual") chips.push(h("span", { class: "chip" }, "Shorts livre"));
  if (s.youtube) {
    const rotulo = { agendado: "YouTube: programado", enviado: "YouTube: publicado", rascunho: "YouTube: rascunho", erro: "YouTube: falhou" };
    chips.push(h("span", { class: `chip ${s.youtube.estado === "erro" ? "erro" : s.youtube.estado === "rascunho" ? "aviso" : "ok"}` }, rotulo[s.youtube.estado] || "YouTube"));
  }
  return h("div", { class: "chips" }, ...chips);
}

function linhaDoShort(idProjeto, s) {
  return h("a", { class: `cartao short ${s.incluir ? "" : "desmarcado"}`, href: `#/p/${enc(idProjeto)}/s/${s.id}` },
    h("span", { class: `num ${s.numero ? "" : "fora"}` }, s.numero ?? "–"),
    h("div", { class: "corpo" }, h("div", { class: "nome" }, s.titulo), chipsDoShort(s)),
  );
}

let filtroDosShorts = "marcados";

/** O que dá para fazer no projeto agora: aprovar a revisão, acompanhar o processamento, renderizar. */
function acoesDoProjeto(id, a, atualizar) {
  const itens = [];
  const fila = a.fila && a.fila.rodando ? a.fila : null;
  const revisao = a.etapas.find((e) => e.status === "aguardando_revisao");
  if (revisao && !fila) {
    if (revisao.id === "aprovar_cortes") {
      itens.push(h("div", { class: "cartao destaque" },
        h("div", { class: "nome" }, "✋ Os cortes esperam a sua aprovação"),
        h("div", { class: "meta" }, "Marque os que viram Shorts e aprove: o PC segue o processamento."),
        h("a", { class: "botao", href: `#/p/${enc(id)}/aprovar` }, "Revisar e aprovar os cortes")));
    } else {
      itens.push(h("div", { class: "cartao destaque" },
        h("div", { class: "nome" }, `✋ ${revisao.nome}: esperando a sua revisão`),
        h("div", { class: "meta" }, "Pelo celular dá para aprovar como está; para corrigir, use o PC."),
        h("button", { class: "botao", onclick: async (ev) => {
          if (!confirm("Aprovar sem mexer e continuar o processamento no PC?")) return;
          ev.currentTarget.disabled = true;
          try { await post(`/api/projetos/${enc(id)}/aprovar`, { etapa: revisao.id }); avisar("Aprovado: o PC continua."); await atualizar(); }
          catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
        } }, "Aprovar e continuar")));
    }
  }
  if (fila) {
    const etapa = a.etapas.find((e) => e.id === fila.etapa);
    itens.push(h("div", { class: "cartao" },
      h("div", { class: "nome" }, fila.pausado ? "⏸ Em pausa (pedida pelo celular)" : "◐ Processando (pedido pelo celular)"),
      h("div", { class: "meta" }, `${etapa ? etapa.nome : "Preparando…"} · ${Math.round(fila.fracao * 100)}% do total`),
      h("div", { class: "linha-botoes" },
        h("button", { class: "botao secundario", onclick: async () => {
          try { await post(`/api/processamento/${fila.pausado ? "retomar" : "pausar"}`); avisar(fila.pausado ? "Retomada pedida." : "Pausa pedida: vale no próximo ponto seguro."); await atualizar(); }
          catch (e) { avisar(e.message); }
        } }, fila.pausado ? "Retomar" : "Pausar"),
        h("button", { class: "botao perigo", onclick: async () => {
          if (!confirm("Cancelar o processamento? O projeto fica salvo e pode ser retomado.")) return;
          try { await post("/api/processamento/cancelar"); avisar("Cancelamento pedido."); await atualizar(); }
          catch (e) { avisar(e.message); }
        } }, "Cancelar"))));
  } else if (a.fila && a.fila.ultimo_resultado === "erro") {
    itens.push(h("div", { class: "cartao aviso-bloco" }, `O último processamento parou com erro: ${a.fila.ultimo_erro || "veja o log"}.`));
  }
  if (!fila && !a.em_andamento && !revisao && a.a_renderizar > 0) {
    itens.push(h("button", { class: "botao", onclick: async (ev) => {
      const n = a.a_renderizar;
      if (!confirm(`Renderizar ${plural(n, "Short", "Shorts")} no PC (os marcados que são novos ou mudaram)? O celular avisa quando terminar.`)) return;
      ev.currentTarget.disabled = true;
      try { await post(`/api/projetos/${enc(id)}/renderizar`); avisar("Renderização iniciada no PC."); await atualizar(); }
      catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
    } }, `▶ Renderizar ${plural(a.a_renderizar, "Short", "Shorts")}`));
  }
  return itens;
}

async function telaProjeto(id) {
  await entrarNoProjeto(id);
  voltarDoShort = `#/p/${enc(id)}`;
  const caixaAndamento = h("div");
  const caixaAcoes = h("div");
  const caixaShorts = h("div");
  const projeto = await api(`/api/projetos/${enc(id)}`);
  cabecalho(projeto.nome, dataBr(projeto.data), "");
  const situacao = h("div", { class: "chips" });
  const pintarSituacao = (a) => encher(situacao, 
    h("span", { class: `chip ${classeDaSituacao(a.situacao)}` }, a.situacao),
    h("span", { class: "chip" }, projeto.resultados));
  pintarSituacao(projeto.andamento);
  desenharAndamento(caixaAndamento, projeto.andamento);

  const segmentos = h("div", { class: "segmentos" });
  const titulo = h("h2");
  let shorts = [];
  const pintarShorts = () => {
    titulo.textContent = `Shorts (${shorts.filter((s) => s.incluir).length} marcados)`;
    encher(segmentos, ...[["marcados", "Marcados"], ["renderizados", "Renderizados"], ["todos", "Todos"]].map(([k, r]) =>
      h("button", { class: filtroDosShorts === k ? "ativo" : "", onclick: () => { filtroDosShorts = k; pintarShorts(); } }, r)));
    const visiveis = shorts.filter((s) => filtroDosShorts === "todos" || (filtroDosShorts === "renderizados" ? s.renderizado : s.incluir));
    encher(caixaShorts, ...(visiveis.length
      ? visiveis.map((s) => linhaDoShort(id, s))
      : [h("div", { class: "vazio-lista" }, shorts.length ? "Nenhum Short neste filtro." : "Este projeto ainda não tem Shorts.")]));
  };
  shorts = await api(`/api/projetos/${enc(id)}/shorts`);
  pintarShorts();
  const livre = shorts.length
    ? h("a", { class: "botao secundario", href: `#/p/${enc(id)}/livre` }, "✚ Shorts livre: criar um Short de qualquer trecho")
    : null;

  const etapasAbertas = projeto.andamento.em_andamento || projeto.andamento.esperando_revisao;
  const blocoEtapas = h("details", { open: etapasAbertas || null }, h("summary", {}, "Etapas do processamento"), caixaAndamento);
  encher(tela, situacao, caixaAcoes, blocoEtapas, titulo, segmentos, livre || "", caixaShorts);

  let andava = projeto.andamento.em_andamento;
  const atualizar = async () => {
    const a = await api(`/api/projetos/${enc(id)}/andamento`);
    pintarSituacao(a);
    desenharAndamento(caixaAndamento, a);
    encher(caixaAcoes, ...acoesDoProjeto(id, a, atualizar));
    if (andava || a.em_andamento) { shorts = await api(`/api/projetos/${enc(id)}/shorts`); pintarShorts(); }
    andava = a.em_andamento;
  };
  await atualizar();
  repetir(atualizar, 5000);
}

async function telaAprovar(id) {
  await entrarNoProjeto(id);
  voltarDoShort = `#/p/${enc(id)}/aprovar`;
  const projeto = await api(`/api/projetos/${enc(id)}`);
  cabecalho("Aprovar os cortes", projeto.nome, `#/p/${enc(id)}`);
  let shorts = await api(`/api/projetos/${enc(id)}/shorts`);
  const lista = h("div");
  const aprovar = h("button", { class: "botao" });
  const pintar = () => {
    const marcados = shorts.filter((s) => s.incluir).length;
    aprovar.textContent = `Aprovar e continuar (${plural(marcados, "marcado", "marcados")})`;
    aprovar.disabled = marcados === 0;
    encher(lista, ...shorts.map((s) => {
      const caixa = h("button", { class: `marcador ${s.incluir ? "ligado" : ""}`, "aria-label": s.incluir ? "Desmarcar" : "Marcar", "aria-pressed": String(s.incluir),
        onclick: async (ev) => {
          ev.currentTarget.disabled = true;
          try {
            const novo = await editar(`/api/projetos/${enc(id)}/shorts/${s.id}/marcar`, { incluir: !s.incluir });
            shorts = shorts.map((x) => (x.id === s.id ? novo : x));
            shorts = await api(`/api/projetos/${enc(id)}/shorts`); // os números mudam com a marcação
            pintar();
          } catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
        } }, s.incluir ? "✓" : "");
      return h("div", { class: `cartao short ${s.incluir ? "" : "desmarcado"}` }, caixa,
        h("a", { class: "corpo", href: `#/p/${enc(id)}/s/${s.id}` },
          h("div", { class: "nome" }, s.titulo), h("div", { class: "meta" }, `Nota ${Math.round(s.nota)}`), chipsDoShort(s)));
    }));
  };
  aprovar.addEventListener("click", async () => {
    if (!confirm("Aprovar os cortes marcados e continuar o processamento no PC?")) return;
    aprovar.disabled = true;
    try {
      await post(`/api/projetos/${enc(id)}/aprovar`, { etapa: "aprovar_cortes" });
      avisar("Aprovado: o PC continua o processamento.");
      location.hash = `#/p/${enc(id)}`;
    } catch (e) { avisar(e.message); aprovar.disabled = false; }
  });
  pintar();
  encher(tela, 
    h("p", { class: "nota" }, "Toque no quadrado para marcar ou desmarcar; toque no título para ouvir e editar o corte."),
    lista, h("div", { class: "barra-fixa" }, aprovar));
}

function lerModo() {
  try { return localStorage.getItem("modoDoShort") || "ouvir"; } catch (_) { return "ouvir"; }
}
function guardarModo(modo) {
  try { localStorage.setItem("modoDoShort", modo); } catch (_) { /* sem armazenamento: tudo bem */ }
}

/** Ouvir com a legenda: o PC monta só o áudio do Short; a palavra falada acende. */
async function montarAudicao(area, base) {
  encher(area, h("div", { class: "bloco" }, "Carregando a legenda…"));
  const leg = await api(`${base}/legenda`);
  const palavras = leg.frases.flat();
  if (!leg.fonte) {
    const aviso = h("div", { class: "bloco aviso-bloco" }, leg.aviso);
    encher(area, aviso);
    return { limpar: () => {}, legenda: aviso };
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
  const legendaViva = h("div", { class: "legenda-viva" }, ...linhas);
  encher(area, barraDoAudio, aviso || "", legendaViva, audio);
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
          if (!legendaViva.hidden) linha.scrollIntoView({ block: "center", behavior: "smooth" });
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
  return {
    legenda: legendaViva,
    limpar: () => { cancelAnimationFrame(quadro); audio.pause(); audio.removeAttribute("src"); audio.load(); },
  };
}

/** Corrigir a legenda inteira e mandar ao PC (R9). */
async function editorDaLegenda(painel, base, aoSalvar, aoFechar) {
  const ed = await api(`${base}/edicao`);
  const caixa = h("textarea", { class: "caixa-legenda", rows: "12", spellcheck: "true", lang: "pt-BR", autocapitalize: "sentences" });
  caixa.value = ed.legenda;
  const salvar = async (texto, botao) => {
    botao.disabled = true;
    try { await aoSalvar(await editar(`${base}/legenda`, { texto })); avisar("Legenda salva no PC."); }
    catch (e) { avisar(e.message); botao.disabled = false; }
  };
  const botaoSalvar = h("button", { class: "botao", onclick: () => salvar(caixa.value, botaoSalvar) }, "Salvar a legenda");
  const botaoTranscricao = ed.corrigida ? h("button", { class: "botao secundario", onclick: () => {
    if (confirm("Desfazer as correções e voltar ao texto da transcrição?")) salvar("", botaoTranscricao);
  } }, "Voltar ao texto da transcrição") : null;
  encher(painel, h("div", { class: "bloco" },
    h("h3", {}, "Corrigir a legenda"),
    h("p", { class: "nota" }, "Escreva a legenda inteira como deve aparecer e ouça acima para conferir. As quebras de linha não importam: o PC refaz a divisão."),
    caixa,
    h("div", { class: "linha-botoes" }, botaoSalvar, h("button", { class: "botao secundario", onclick: aoFechar }, "Fechar")),
    botaoTranscricao));
}

/** Mudar o início e o fim do corte, palavra por palavra. */
async function editorDasPontas(painel, base, aoMudar, aoFechar) {
  const ed = await api(`${base}/edicao`);
  const mudar = async (corpo) => {
    try { await aoMudar(await editar(`${base}/pontas`, corpo)); }
    catch (e) { avisar(e.message); }
  };
  const palavras = ed.contexto.map((w) => {
    const classes = ["palavra-ctx", w.dentro ? "dentro" : "fora"];
    const span = h("span", { class: classes.join(" "), onclick: async () => {
      const opcoes = [];
      if (w.n <= ed.ultima) opcoes.push(["inicio", "▶ Começar nesta palavra"]);
      if (w.n >= ed.primeira) opcoes.push(["fim", "Terminar nesta palavra ◀"]);
      const escolha = await escolher(`“${w.t}”`, opcoes);
      if (escolha === "inicio") await mudar({ primeira: w.n });
      if (escolha === "fim") await mudar({ ultima: w.n });
    } }, w.t);
    const pedacos = [];
    if (w.n === ed.primeira) pedacos.push(h("span", { class: "marca-ponta", id: "marca-inicio" }, "▶"));
    pedacos.push(span);
    if (w.n === ed.ultima) pedacos.push(h("span", { class: "marca-ponta" }, "◀"));
    return pedacos;
  });
  const passo = (ponta, n, rotulo) => h("button", { class: "botao-pequeno", onclick: () => mudar({ ponta, passo: n }) }, rotulo);
  encher(painel, h("div", { class: "bloco" },
    h("h3", {}, "Início e fim do corte"),
    h("p", { class: "nota" }, `Duração: ${duracao(ed.duracao)}. Toque numa palavra para começar ou terminar nela; as setas mudam uma palavra.`),
    ed.acima_do_limite ? h("p", { class: "aviso-bloco nota" }, "Acima de 3 minutos o corte não pode ser um Short: encurte.") : null,
    h("div", { class: "linha-pontas" }, h("span", {}, "Início"), passo("inicio", -1, "‹ uma antes"), passo("inicio", 1, "uma depois ›")),
    h("div", { class: "linha-pontas" }, h("span", {}, "Fim"), passo("fim", -1, "‹ uma antes"), passo("fim", 1, "uma depois ›")),
    h("div", { class: "contexto-palavras" }, ...palavras.flatMap((p) => [...p, " "])),
    h("button", { class: "botao secundario", onclick: aoFechar }, "Fechar")));
  const marca = document.getElementById("marca-inicio");
  if (marca) marca.scrollIntoView({ block: "center" });
}

const doisDigitos = (n) => String(n).padStart(2, "0");
const dataLocal = (d) => `${d.getFullYear()}-${doisDigitos(d.getMonth() + 1)}-${doisDigitos(d.getDate())}`;

/** Postar no YouTube pelo PC (o YouTube Studio, sem a API): programar ou publicar agora (Fase R4). */
async function editorDaPostagem(painel, base, idProjeto, aoTerminar, aoFechar) {
  const prep = await api(`${base}/postar`);
  const yt = prep.youtube;
  const estado = yt ? h("div", { class: `bloco ${yt.estado === "erro" || yt.estado === "rascunho" ? "aviso-bloco" : ""}` },
    h("h3", {}, "No YouTube"),
    h("p", {}, { agendado: "📅 Programado", enviado: "✔ Publicado", rascunho: "⚠ Ficou como rascunho privado (a próxima tentativa o retoma)", erro: "✕ A última tentativa falhou" }[yt.estado] || yt.estado,
      yt.quando && yt.estado === "agendado" ? ` para ${new Date(yt.quando).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}` : ""),
    yt.url ? h("p", {}, h("a", { href: yt.url, target: "_blank", rel: "noopener" }, yt.url)) : null,
    yt.detalhe ? h("p", { class: "nota" }, yt.detalhe) : null) : null;
  const jaFeito = yt && (yt.estado === "agendado" || yt.estado === "enviado");

  let modo = "programar";
  const modos = h("div", { class: "segmentos" });
  const sugestao = new Date(Date.now() + 60 * 60 * 1000);
  sugestao.setMinutes(sugestao.getMinutes() < 30 ? 30 : 60, 0, 0);
  const dia = h("input", { type: "date", class: "campo", value: dataLocal(sugestao) });
  const hora = h("input", { type: "time", class: "campo", value: `${doisDigitos(sugestao.getHours())}:${doisDigitos(sugestao.getMinutes())}`, step: "300" });
  const quando = h("div", { class: "linha-botoes" }, dia, hora);
  const pintarModos = () => {
    encher(modos, ...[["programar", "📅 Programar"], ["agora", "Publicar agora"]].map(([k, r]) =>
      h("button", { class: modo === k ? "ativo" : "", onclick: () => { modo = k; pintarModos(); } }, r)));
    quando.hidden = modo !== "programar";
  };
  pintarModos();
  const vincular = h("input", { type: "checkbox", checked: true, id: "vincular" });
  const relacionado = h("input", { type: "text", class: "campo", value: prep.relacionado });
  const andamento = h("p", { class: "nota" });
  const botao = h("button", { class: "botao" }, jaFeito ? "Postar de novo no YouTube" : "📤 Postar no YouTube");

  const acompanhar = async () => {
    botao.disabled = true;
    for (;;) {
      let e;
      try { e = await api(`/api/projetos/${enc(idProjeto)}/postagem`); } catch (_) { await new Promise((r) => setTimeout(r, 3000)); continue; }
      if (!e) break;
      andamento.textContent = e.texto;
      if (!e.rodando) {
        if (e.erro) { avisar(`Não deu para postar: ${e.erro}`); andamento.textContent = `Não deu para postar: ${e.erro}`; botao.disabled = false; }
        else { avisar(e.texto); await aoTerminar(); aoFechar(); }
        return;
      }
      if (!document.body.contains(andamento)) return; // saiu da tela: o aviso chega
      await new Promise((r) => setTimeout(r, 2500));
    }
    botao.disabled = false;
  };
  botao.addEventListener("click", async () => {
    let corpo = { relacionado: vincular.checked ? relacionado.value.trim() : "", forcar: Boolean(jaFeito) };
    let resumo;
    if (modo === "programar") {
      if (!dia.value || !hora.value) { avisar("Escolha o dia e a hora."); return; }
      corpo.quando = `${dia.value}T${hora.value}`;
      resumo = `Programar no YouTube para ${dia.value.split("-").reverse().join("/")} às ${hora.value}`;
    } else {
      resumo = "Publicar agora no YouTube";
    }
    if (corpo.relacionado) resumo += `, vinculado a “${corpo.relacionado}”`;
    if (jaFeito) resumo += ". ATENÇÃO: ele já está no YouTube; isto cria outro vídeo";
    if (!confirm(`${resumo}?`)) return;
    try { await editar(`${base}/postar`, corpo); acompanhar(); }
    catch (e) { avisar(e.message); }
  });

  encher(painel, h("div", { class: "bloco" },
    h("h3", {}, "Postar no YouTube"),
    estado,
    h("p", { class: "nota" }, "O PC posta pelo YouTube Studio da igreja (uma janela do Chrome abre lá e faz tudo sozinha). Leva alguns minutos."),
    modos, quando,
    h("label", { class: "opcao", for: "vincular" }, vincular, " Vincular ao vídeo do sermão (vídeo relacionado)"),
    relacionado,
    botao, andamento,
    h("button", { class: "botao secundario", onclick: aoFechar }, "Fechar")));
  if (prep.envio && prep.envio.rodando) acompanhar();
}

async function telaShort(id, corte) {
  await entrarNoProjeto(id);
  const pegar = async () => (await api(`/api/projetos/${enc(id)}/shorts`)).find((x) => String(x.id) === String(corte));
  let s = await pegar();
  if (!s) throw new Error("Este Short não existe mais no projeto.");
  const voltarPara = voltarDoShort.startsWith(`#/p/${enc(id)}`) ? voltarDoShort : `#/p/${enc(id)}`;
  const base = `/api/projetos/${enc(id)}/shorts/${s.id}`;
  let modo = s.renderizado ? lerModo() : "ouvir";
  let edicao = ""; // "", "legenda" ou "pontas"
  let audicao = null;
  const area = h("div");
  const painel = h("div");
  const seletor = h("div", { class: "segmentos" });
  const ferramentas = h("div", { class: "ferramentas" });
  const info = h("div");

  const pintarCabecalho = () => cabecalho(s.numero ? `Short ${s.numero}` : "Corte desmarcado", duracao(s.duracao), voltarPara);
  const pintarInfo = () => encher(info, 
    h("div", { class: "bloco" }, h("p", { class: "titulo-short" }, s.titulo), chipsDoShort(s)),
    s.descricao ? h("div", { class: "bloco" }, h("h3", {}, "Descrição"), h("p", {}, s.descricao)) : null,
    s.hashtags.length ? h("div", { class: "bloco" }, h("h3", {}, "Hashtags"), h("p", {}, s.hashtags.join(" "))) : null);
  const pintarFerramentas = () => {
    ferramentas.hidden = modo !== "ouvir";
    encher(ferramentas, 
      h("button", { class: `botao-ferramenta ${edicao === "legenda" ? "ativo" : ""}`, onclick: () => abrir(edicao === "legenda" ? "" : "legenda") }, "✎ Legenda"),
      h("button", { class: `botao-ferramenta ${edicao === "pontas" ? "ativo" : ""}`, onclick: () => abrir(edicao === "pontas" ? "" : "pontas") }, "⇤ Início e fim ⇥"),
      s.renderizado && s.situacao === "pronto"
        ? h("button", { class: `botao-ferramenta ${edicao === "postar" ? "ativo" : ""}`, onclick: () => abrir(edicao === "postar" ? "" : "postar") }, "📤 Postar")
        : null,
      h("button", { class: `botao-ferramenta ${s.incluir ? "marcado" : ""}`, onclick: async (ev) => {
        ev.currentTarget.disabled = true;
        try { s = await editar(`${base}/marcar`, { incluir: !s.incluir }); avisar(s.incluir ? "Corte marcado." : "Corte desmarcado."); }
        catch (e) { avisar(e.message); }
        pintarCabecalho(); pintarInfo(); pintarFerramentas();
      } }, s.incluir ? "✓ Marcado" : "Desmarcado"));
  };
  const pintarSeletor = () => {
    seletor.hidden = !s.renderizado;
    encher(seletor, ...[["ouvir", "🎧 Ouvir e editar"], ["video", "▶ Vídeo pronto"]].map(([k, r]) =>
      h("button", { class: modo === k ? "ativo" : "", onclick: () => {
        if (modo === k) return;
        modo = k; guardarModo(k); edicao = ""; pintarSeletor(); mostrar();
      } }, r)));
  };

  // depois de uma mudança: o Short novo (duração, legenda) e o áudio refeito
  const depoisDeMudar = async (novo) => {
    s = novo;
    pintarCabecalho(); pintarInfo(); pintarSeletor();
    await mostrar();
  };
  const fecharEditor = () => { abrir(""); };

  async function abrir(qual) {
    edicao = qual;
    pintarFerramentas();
    if (audicao) audicao.legenda.hidden = Boolean(qual);
    try {
      if (qual === "legenda") await editorDaLegenda(painel, base, depoisDeMudar, fecharEditor);
      else if (qual === "pontas") await editorDasPontas(painel, base, depoisDeMudar, fecharEditor);
      else if (qual === "postar") await editorDaPostagem(painel, base, id, async () => { s = await pegar(); pintarCabecalho(); pintarInfo(); pintarFerramentas(); }, fecharEditor);
      else encher(painel);
    } catch (e) {
      encher(painel, h("div", { class: "bloco aviso-bloco" }, e.message || String(e)));
    }
  }

  async function mostrar() {
    if (limparTela) limparTela();
    limparTela = null;
    audicao = null;
    pintarFerramentas();
    if (modo === "video") {
      encher(painel);
      encher(area, h("div", { class: "player" },
        h("video", { src: `${base}/video`, controls: true, playsinline: true, preload: "metadata" })),
        h("div", { class: "bloco" }, h("h3", {}, s.legenda ? "Legenda (corrigida)" : "Texto do trecho"), h("p", {}, s.legenda || s.texto)));
      return;
    }
    try {
      audicao = await montarAudicao(area, base);
      limparTela = audicao.limpar;
    } catch (e) {
      encher(area, h("div", { class: "bloco aviso-bloco" }, e.message || String(e)));
    }
    await abrir(edicao);
  }

  pintarCabecalho(); pintarInfo(); pintarSeletor();
  encher(tela, seletor, ferramentas, area, painel, info);
  await mostrar();
}

// --- Shorts livre (Fase R3) -----------------------------------------------------------------

async function telaLivre(id) {
  await entrarNoProjeto(id);
  const base = `/api/projetos/${enc(id)}/livre`;
  const projeto = await api(`/api/projetos/${enc(id)}`);
  cabecalho("Shorts livre", projeto.nome, `#/p/${enc(id)}`);
  encher(tela, h("div", { class: "vazio-lista" }, "Carregando o texto do sermão…"));
  const dados = await api(`${base}/texto`);
  const cats = dados.categorias;

  // o texto: uma linha por frase, com a hora e a cor da categoria; palavras com o índice
  const spans = [];
  const fraseDe = [];
  const linhas = dados.frases.map((f, k) => {
    const cor = f.cat && cats[f.cat] ? cats[f.cat].cor : "transparent";
    return h("p", { class: "frase-livre", style: `border-left-color:${cor}`, title: f.cat && cats[f.cat] ? cats[f.cat].rotulo : null },
      h("span", { class: "hora" }, minSeg(f.i)), " ",
      ...f.palavras.flatMap(([t, sid], j) => {
        const n = f.de + j;
        const feito = sid ? dados.shorts[String(sid)] : null;
        const corDoShort = feito && cats[feito.cat] ? cats[feito.cat].cor : "var(--texto-2)";
        const span = h("span", { class: `pl${sid ? " em-short" : ""}`, "data-n": String(n),
          style: sid ? `text-decoration-color:${corDoShort}` : null, title: feito ? `Já está no Short “${feito.titulo}”` : null }, t);
        spans[n] = span;
        fraseDe[n] = k;
        return j ? [" ", span] : [span];
      }));
  });
  const textoEl = h("div", { class: "texto-livre" }, ...linhas);
  const legenda = Object.values(cats).length
    ? h("details", { class: "cores" }, h("summary", {}, "Cores das categorias"),
      h("div", { class: "chips" }, ...Object.values(cats).map((c) => h("span", { class: "chip" }, h("span", { class: "cor", style: `background:${c.cor}` }), c.rotulo)),
        h("span", { class: "chip" }, h("span", { class: "sublinhado" }, "palavra"), " já está num Short")))
    : null;

  // a busca
  const consulta = h("input", { type: "search", class: "campo", placeholder: "Buscar palavras ou um tema", enterkeyhint: "search" });
  let modoDaBusca = "palavras";
  const modos = h("div", { class: "segmentos" });
  const pintarModos = () => encher(modos, ...[["palavras", "Palavras"], ["tema", "Tema"]].map(([k, r]) =>
    h("button", { class: modoDaBusca === k ? "ativo" : "", onclick: () => { modoDaBusca = k; pintarModos(); if (consulta.value.trim()) buscar(); } }, r)));
  const achados = h("div");
  const buscar = async () => {
    const q = consulta.value.trim();
    if (!q) { encher(achados); return; }
    encher(achados, h("p", { class: "nota" }, modoDaBusca === "tema" ? "Procurando o tema (a primeira vez demora uns segundos)…" : "Procurando…"));
    try {
      const lista = await api(`${base}/buscar?q=${enc(q)}&modo=${modoDaBusca}`);
      encher(achados, ...(lista.length ? lista.map((a) => h("button", { class: "cartao achado", onclick: () => {
        encher(achados);
        selecionar(a.de, a.ate);
        spans[a.de].scrollIntoView({ block: "center" });
      } }, h("div", { class: "meta" }, `${minSeg(a.i)} · ${Math.round(a.pontos)} pontos`), h("div", {}, a.texto.length > 220 ? `${a.texto.slice(0, 220)}…` : a.texto)))
        : [h("p", { class: "nota" }, "Nada parecido no sermão.")]),
      h("button", { class: "botao fechar", onclick: () => encher(achados) }, "Fechar a busca"));
    } catch (e) { encher(achados, h("p", { class: "nota" }, e.message)); }
  };
  consulta.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); consulta.blur(); buscar(); } });
  pintarModos();

  // o trecho escolhido
  let sel = null; // {de, ate, fora: Set}
  let ancora = null;
  let pintadas = [];
  const painel = h("div", { class: "painel-livre" });
  const audio = h("audio", { preload: "none" });
  const pintarSelecao = () => {
    for (const n of pintadas) spans[n].classList.remove("sel", "fora-sel", "ancora");
    pintadas = [];
    if (ancora !== null) { spans[ancora].classList.add("ancora"); pintadas.push(ancora); }
    if (!sel) return;
    for (let n = sel.de; n <= sel.ate; n++) {
      spans[n].classList.add(sel.fora.has(n) ? "fora-sel" : "sel");
      pintadas.push(n);
    }
  };
  const consultaDoTrecho = () => `de=${sel.de}&ate=${sel.ate}&fora=${[...sel.fora].join(",")}`;
  let pedido = 0;
  const atualizarTrecho = async () => {
    pintarSelecao();
    audio.pause();
    audio.removeAttribute("src");
    if (!sel) {
      encher(painel, h("p", { class: "nota" }, ancora === null
        ? "Toque na primeira palavra do trecho que você quer (ou busque acima)."
        : "Agora toque na última palavra do trecho."));
      return;
    }
    const meu = ++pedido;
    encher(painel, h("p", { class: "nota" }, "Conferindo o trecho…"));
    let r;
    try { r = await api(`${base}/trecho?${consultaDoTrecho()}`); } catch (e) { encher(painel, h("p", { class: "nota" }, e.message)); return; }
    if (meu !== pedido) return;
    const tocar = h("button", { class: "botao secundario", disabled: !r.tem_som || null, onclick: () => {
      if (!audio.getAttribute("src")) audio.src = `${base}/audio?${consultaDoTrecho()}`;
      if (audio.paused) audio.play().catch(() => avisar("Não deu para tocar o trecho.")); else audio.pause();
    } }, r.tem_som ? "🎧 Ouvir o trecho" : "Sem som (o vídeo original não está no PC)");
    audio.onplay = () => { tocar.textContent = "❚❚ Pausar"; };
    audio.onpause = () => { tocar.textContent = "🎧 Ouvir o trecho"; };
    audio.onwaiting = () => { tocar.textContent = "Preparando o som no PC…"; };
    encher(painel, 
      h("div", { class: "nome" }, `Trecho de ${duracao(r.duracao)}${sel.fora.size ? " (com pedaços tirados)" : ""}`),
      h("p", { class: "nota" }, r.pontas),
      r.problema ? h("p", { class: "nota aviso-bloco" }, r.problema) : null,
      h("div", { class: "linha-botoes" }, tocar,
        h("button", { class: "botao secundario", onclick: () => { sel = null; ancora = null; atualizarTrecho(); } }, "Limpar")),
      h("button", { class: "botao", disabled: r.problema ? true : null, onclick: criar }, "✚ Criar Short deste trecho"),
      audio);
  };
  const selecionar = (de, ate) => { sel = { de: Math.min(de, ate), ate: Math.max(de, ate), fora: new Set() }; ancora = null; atualizarTrecho(); };

  textoEl.addEventListener("click", async (ev) => {
    const alvo = ev.target.closest(".pl");
    if (!alvo || criando) return;
    const n = Number(alvo.dataset.n);
    if (!sel) {
      if (ancora === null) { ancora = n; pintarSelecao(); atualizarTrecho(); } else selecionar(ancora, n);
      return;
    }
    const dentro = n > sel.de && n < sel.ate;
    const opcoes = [];
    if (n <= sel.ate) opcoes.push(["inicio", "▶ Começar nesta palavra"]);
    if (n >= sel.de) opcoes.push(["fim", "Terminar nesta palavra ◀"]);
    if (dentro && sel.fora.has(n)) opcoes.push(["incluir", "Voltar a incluir esta frase"]);
    if (dentro && !sel.fora.has(n)) opcoes.push(["tirar-frase", "Tirar esta frase do trecho"], ["tirar", "Tirar só esta palavra"]);
    opcoes.push(["novo", "Começar um trecho novo aqui"]);
    const escolha = await escolher(`“${alvo.textContent}”`, opcoes);
    const daFrase = () => { const k = fraseDe[n]; const f = dados.frases[k]; return Array.from({ length: f.palavras.length }, (_, j) => f.de + j).filter((m) => m > sel.de && m < sel.ate); };
    if (escolha === "inicio") { sel.de = n; for (const m of [...sel.fora]) if (m <= n) sel.fora.delete(m); }
    else if (escolha === "fim") { sel.ate = n; for (const m of [...sel.fora]) if (m >= n) sel.fora.delete(m); }
    else if (escolha === "tirar-frase") for (const m of daFrase()) sel.fora.add(m);
    else if (escolha === "tirar") sel.fora.add(n);
    else if (escolha === "incluir") for (const m of daFrase()) sel.fora.delete(m);
    else if (escolha === "novo") { sel = null; ancora = n; }
    else return;
    atualizarTrecho();
  });

  // criar (a IA, cerca de 2 minutos)
  let criando = false;
  const acompanhar = async () => {
    criando = true;
    const caixa = h("div", { class: "cartao destaque" }, h("div", { class: "nome" }, "A IA está criando o Short…"), h("p", { class: "nota" }, "Leva cerca de 2 minutos no PC. Pode sair do app: o celular avisa quando ficar pronto."));
    encher(painel, caixa);
    for (;;) {
      let c;
      try { c = await api(`${base}/criacao`); } catch (e) { await new Promise((r) => setTimeout(r, 3000)); continue; }
      if (!c) break;
      caixa.querySelector(".nota").textContent = c.texto;
      if (!c.rodando) {
        criando = false;
        if (c.corte_id !== null) { avisar(c.texto); location.hash = `#/p/${enc(id)}/s/${c.corte_id}`; }
        else { encher(painel, h("div", { class: "cartao aviso-bloco" }, `Não deu para criar o Short: ${c.erro}`)); setTimeout(atualizarTrecho, 6000); }
        return;
      }
      if (!location.hash.endsWith("/livre")) return; // a pessoa saiu da tela: o aviso chega
      await new Promise((r) => setTimeout(r, 2000));
    }
    criando = false;
  };
  async function criar(ev) {
    if (!sel) return;
    if (!confirm("A IA vai avaliar o trecho e criar título, descrição e hashtags (cerca de 2 minutos no PC). Criar o Short?")) return;
    ev.currentTarget.disabled = true;
    try {
      await editar(`${base}/criar`, { de: sel.de, ate: sel.ate, fora: [...sel.fora] });
      sel = null; ancora = null; pintarSelecao();
      acompanhar();
    } catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
  }

  encher(tela, 
    h("div", { class: "busca" }, consulta, modos), achados,
    legenda || "", textoEl, painel);
  limparTela = () => { audio.pause(); audio.removeAttribute("src"); };
  if (dados.criacao && dados.criacao.rodando) acompanhar(); else atualizarTrecho();
}

// --- roteador -------------------------------------------------------------------------------

async function rotear() {
  pararRepeticao();
  if (limparTela) { limparTela(); limparTela = null; }
  window.scrollTo(0, 0);
  const partes = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  try {
    if (partes[0] === "p" && partes[1] && partes[2] === "s" && partes[3]) await telaShort(partes[1], partes[3]);
    else if (partes[0] === "p" && partes[1] && partes[2] === "aprovar") await telaAprovar(partes[1]);
    else if (partes[0] === "p" && partes[1] && partes[2] === "livre") await telaLivre(partes[1]);
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
  sairDoProjeto();
  await post("/api/sistema/encerrar");
  fecharMenu();
  pararRepeticao();
  marcarPonto("fora");
  encher(tela, h("div", { class: "vazio-lista" }, "Servidor encerrado. Para usar de novo, abra-o no PC."));
});

// --- início ---------------------------------------------------------------------------------

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
window.addEventListener("hashchange", rotear);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  api("/api/sistema").then((s) => mostrarDormindo(s.dormindo)).catch(() => {});
  if (sessao.projeto) tomarATrava(sessao.projeto).catch(() => {}); // voltou ao app: o projeto é dele de novo
});
// o app foi fechado: devolve o projeto ao PC na hora (senão a trava vence sozinha em 90 s)
window.addEventListener("pagehide", () => {
  if (sessao.projeto && navigator.sendBeacon) navigator.sendBeacon(`/api/projetos/${enc(sessao.projeto)}/sessao/soltar`);
});
api("/api/sistema").then((s) => { sistema = s; mostrarDormindo(s.dormindo); }).catch(() => {});
rotear();
