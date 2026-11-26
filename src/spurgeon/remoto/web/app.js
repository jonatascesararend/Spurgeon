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

/** Uma folha de confirmação com a explicação (parágrafos) e um botão de seguir; resolve true/false. */
function confirmarNaFolha(titulo, paragrafos, rotulo, classe = "perigo") {
  return new Promise((resolver) => {
    const fundo = h("div", { class: "fundo" });
    const fechar = (valor) => { fundo.remove(); folha.remove(); resolver(valor); };
    const folha = h("div", { class: "folha" }, h("h2", {}, titulo),
      ...paragrafos.filter(Boolean).map((p) => h("p", { class: "texto-da-folha" }, p)),
      h("button", { class: `botao ${classe}`, onclick: () => fechar(true) }, rotulo),
      h("button", { class: "botao fechar", onclick: () => fechar(false) }, "Cancelar"));
    fundo.addEventListener("click", () => fechar(false));
    document.body.append(fundo, folha);
  });
}

function tamanhoLegivel(bytes) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1).replace(".", ",")} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** "Espaço no PC": varrer (apagar os vídeos e o original, guardando a análise) ou apagar o projeto. */
function blocoDoEspaco(id) {
  const corpo = h("div", {}, h("p", { class: "nota" }, "Calculando…"));
  const bloco = h("details", { class: "bloco-espaco" }, h("summary", {}, "🧹 Espaço no PC: varrer ou apagar o projeto"), corpo);
  const carregar = async () => {
    let e;
    try { e = await api(`/api/projetos/${enc(id)}/espaco`); } catch (erro) { encher(corpo, h("p", { class: "nota" }, erro.message)); return; }
    const v = e.varrer, a = e.apagar;
    const varrido = e.varrido ? h("p", { class: "nota" }, `Varrido em ${dataBr(e.varrido.quando.slice(0, 10))} às ${e.varrido.quando.slice(11, 16)} (liberou ${tamanhoLegivel(e.varrido.bytes || 0)}).`) : null;
    const botaoVarrer = h("button", { class: "botao secundario", disabled: !v.bytes || Boolean(e.impedimento_varrer) },
      v.bytes ? `🧹 Varrer o projeto (libera ${tamanhoLegivel(v.bytes)})` : "🧹 Nada pesado para varrer");
    const botaoApagar = h("button", { class: "botao perigo", disabled: Boolean(e.impedimento_apagar) },
      `🗑 Apagar o projeto (do app e do PC: ${tamanhoLegivel(a.bytes)})`);
    botaoVarrer.addEventListener("click", async () => {
      const ok = await confirmarNaFolha("Varrer o projeto?", [
        `Libera ${tamanhoLegivel(v.bytes)} no PC.`,
        `Apaga: os Shorts renderizados e o vídeo longo pronto, os arquivos de trabalho grandes${v.original ? ` e o vídeo original (“${v.original}”, ${tamanhoLegivel(v.bytes_do_original)})` : ""}.`,
        v.original_fica,
        "Fica: a transcrição, os cortes, os títulos e as legendas, e o registro do que foi postado e agendado. O projeto continua abrindo e a legenda continua exportável.",
        v.original ? "Sem o vídeo original não dá mais para renderizar nem processar este projeto (seria preciso baixar o vídeo de novo)." : null,
      ], "🧹 Varrer", "perigo");
      if (!ok) return;
      botaoVarrer.disabled = true;
      try {
        const r = await post(`/api/projetos/${enc(id)}/varrer`, { confirmar: id });
        avisar(`Projeto varrido: ${tamanhoLegivel(r.bytes)} liberados.${r.falhas.length ? ` ${r.falhas.length} arquivo(s) em uso ficaram.` : ""}`);
        await rotear();
      } catch (erro) { avisar(erro.message); botaoVarrer.disabled = false; }
    });
    botaoApagar.addEventListener("click", async () => {
      const ok = await confirmarNaFolha("Apagar o projeto?", [
        `Apaga a pasta inteira do projeto (${tamanhoLegivel(a.bytes_do_projeto)}): a transcrição, os cortes, os Shorts e tudo o que foi feito nele${a.original ? `, e o vídeo original (“${a.original}”, ${tamanhoLegivel(a.bytes_do_original)})` : ""}.`,
        a.original_fica,
        "Os vídeos que já estão no YouTube não são mexidos. No PC, não dá para desfazer.",
      ], "Continuar…", "perigo");
      if (!ok) return;
      const certeza = await confirmarNaFolha("Tem certeza?", [`“${projetoAtualNome || id}” vai sumir do app e do PC, de vez.`], "🗑 Apagar de vez", "perigo");
      if (!certeza) return;
      botaoApagar.disabled = true;
      sairDoProjeto(); // solta a trava antes: a renovação não pode recriar a pasta
      try {
        const r = await post(`/api/projetos/${enc(id)}/apagar`, { confirmar: id });
        avisar(`Projeto apagado: ${tamanhoLegivel(r.bytes)} liberados.${r.falhas.length ? " Alguns arquivos estavam em uso e ficaram." : ""}`);
        try { localStorage.removeItem(CHAVE_DA_ULTIMA_TELA); } catch (_) { /* tudo bem */ }
        location.hash = "";
      } catch (erro) { avisar(erro.message); botaoApagar.disabled = false; entrarNoProjeto(id).catch(() => {}); }
    });
    encher(corpo,
      varrido,
      botaoVarrer,
      e.impedimento_varrer ? h("p", { class: "nota" }, e.impedimento_varrer) : null,
      botaoApagar,
      e.impedimento_apagar ? h("p", { class: "nota" }, e.impedimento_apagar) : null);
  };
  bloco.addEventListener("toggle", () => { if (bloco.open) carregar(); });
  return bloco;
}
let projetoAtualNome = "";

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
  const abas = abasDoInicio("projetos");
  const desenhar = async () => {
    const lista = await api("/api/projetos");
    const novo = h("a", { class: "botao", href: "#/canal" }, "✚ Novo projeto de um vídeo do canal");
    if (!lista.length) {
      encher(tela, abas, novo, h("div", { class: "vazio-lista" }, "Nenhum projeto no PC ainda."));
      return;
    }
    encher(tela, abas, novo, ...lista.map((p) => h("a", { class: "cartao", href: `#/p/${enc(p.id)}` },
      h("div", { class: "nome" }, p.nome),
      h("div", { class: "meta" }, `${dataBr(p.data)} · ${p.detalhes}`),
      h("div", { class: "chips" }, h("span", { class: `chip ${classeDaSituacao(p.situacao)}` }, p.situacao),
        p.varrido ? h("span", { class: "chip varrido" }, `🧹 Varrido em ${dataBr(p.varrido.quando.slice(0, 10)).slice(0, 5)}${p.varrido.bytes ? ` · ${tamanhoLegivel(p.varrido.bytes)} liberados` : ""}`) : null),
      publicacaoDoProjeto(p.publicacao),
    )));
  };
  await desenhar();
  repetir(desenhar, 20000);
}

/** O resumo dos Shorts do projeto: postados, agendados (com o próximo horário) e os que faltam postar. */
function publicacaoDoProjeto(pub) {
  if (!pub || !(pub.postados || pub.agendados || pub.a_postar)) return null;
  const chips = [];
  if (pub.postados) chips.push(h("span", { class: "chip ok" }, `✔ ${plural(pub.postados, "postado", "postados")}`));
  if (pub.agendados) {
    const proximo = pub.proximo ? ` · próximo ${dataBr(pub.proximo.slice(0, 10)).slice(0, 5)} às ${pub.proximo.slice(11, 16)}` : "";
    chips.push(h("span", { class: "chip azul" }, `📅 ${plural(pub.agendados, "agendado", "agendados")}${proximo}`));
  }
  if (pub.a_postar) chips.push(h("span", { class: "chip" }, `${plural(pub.a_postar, "Short", "Shorts")} a postar`));
  return h("div", { class: "chips" }, ...chips);
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
  const chips = [];
  if (s.fila) chips.push(h("span", { class: `chip fila ${s.fila.situacao}` }, s.fila.rotulo));
  chips.push(h("span", { class: "chip" }, h("span", { class: "cor", style: `background:${s.categoria.cor}` }), s.categoria.rotulo),
    h("span", { class: "chip" }, duracao(s.duracao)));
  if (s.postado) chips.push(h("span", { class: "chip ok" }, `✔ Postado ${s.postado_rotulo}`.trim()));
  else if (s.agendado_rotulo) chips.push(h("span", { class: "chip azul" }, `📅 ${s.agendado_rotulo}`));
  if (s.situacao === "alterado") chips.push(h("span", { class: "chip aviso" }, "✎ Mudou: renderizar de novo"));
  else if (s.renderizado) chips.push(h("span", { class: "chip ok" }, "▶ Renderizado"));
  else if (s.incluir) chips.push(h("span", { class: "chip" }, "Não renderizado"));
  if (s.opiniao === "bom") chips.push(h("span", { class: "chip ok" }, "👍 Bom"));
  else if (s.opiniao === "ruim") chips.push(h("span", { class: "chip erro" }, "👎 Ruim"));
  if (s.gemini) {
    const classe = { bom: "ok", ajustar: "aviso", ruim: "erro" }[s.gemini.veredito] || "";
    chips.push(h("span", { class: `chip ${classe}` }, `✨ Gemini: ${s.gemini.veredito} ${s.gemini.nota}/10`));
  }
  if (s.reserva && !s.incluir) chips.push(h("span", { class: "chip" }, "Reserva"));
  if (s.origem === "manual") chips.push(h("span", { class: "chip" }, "Shorts livre"));
  if (s.youtube) {
    const rotulo = { agendado: "YouTube: programado", enviado: "YouTube: publicado", rascunho: "YouTube: rascunho", erro: "YouTube: falhou" };
    chips.push(h("span", { class: `chip ${s.youtube.estado === "erro" ? "erro" : s.youtube.estado === "rascunho" ? "aviso" : "ok"}` }, rotulo[s.youtube.estado] || "YouTube"));
  }
  return h("div", { class: "chips" }, ...chips);
}

/** "12:05 → 13:02": onde o Short começa e termina no sermão. */
const trechoDoShort = (s) => `⏱ ${textoDoTempo(s.inicio)} → ${textoDoTempo(s.fim)}`;

function linhaDoShort(idProjeto, s) {
  return h("a", { class: `cartao short ${s.incluir ? "" : "desmarcado"}`, href: `#/p/${enc(idProjeto)}/s/${s.id}` },
    h("span", { class: `num ${s.numero ? "" : "fora"}` }, s.numero ?? "–"),
    h("div", { class: "corpo" }, h("div", { class: "nome" }, s.titulo),
      h("div", { class: "meta" }, trechoDoShort(s)), chipsDoShort(s)),
  );
}

const estaProgramado = (s) => !s.postado && (Boolean(s.agendado_rotulo) || (s.youtube && s.youtube.estado === "agendado"));
const estaPostado = (s) => Boolean(s.postado) || Boolean(s.youtube && s.youtube.estado === "enviado");

/** Os filtros da lista de Shorts do projeto: [chave, rótulo, quem entra]. */
const FILTROS_DOS_SHORTS = [
  ["todos", "Todos", () => true],
  ["aprovados", "Aprovados", (s) => s.incluir],
  ["renderizados", "Renderizados", (s) => s.renderizado],
  ["nao_renderizados", "Não renderizados", (s) => s.incluir && !s.renderizado],
  ["programados", "Programados", estaProgramado],
  ["postados", "Postados", estaPostado],
  ["bons", "👍 Bons", (s) => s.opiniao === "bom"],
  ["ruins", "👎 Ruins", (s) => s.opiniao === "ruim"],
  ["gemini", "✨ Revisados", (s) => Boolean(s.gemini)],
  ["fila", "⏳ Na fila", (s) => Boolean(s.fila)],
];

// os filtros voltam a "Todos" (e a todas as categorias) cada vez que um projeto é aberto; voltar
// de um Short do mesmo projeto mantém o que estava
let filtroDosShorts = "todos";
let categoriaDosShorts = "";
let rotaAtual = "";
let rotaAnterior = "";

/** O que dá para fazer no projeto agora: aprovar a revisão, acompanhar o processamento, renderizar. */
function acoesDoProjeto(id, a, atualizar) {
  const itens = [];
  const fila = a.fila && a.fila.rodando ? a.fila : null;
  const revisao = a.etapas.find((e) => e.status === "aguardando_revisao");
  if (revisao && !fila) {
    if (revisao.id === "aprovar_cortes") {
      itens.push(h("div", { class: "cartao destaque" },
        h("div", { class: "nome" }, "✋ Os cortes esperam a sua aprovação"),
        h("div", { class: "meta" }, "Aprove os cortes que viram Shorts e continue: o PC segue o processamento."),
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
  const naoComecou = a.etapas.every((e) => e.status === "pendente"); // o "Começar" fica nas opções
  if (!fila && !a.em_andamento && !revisao && !naoComecou && !/conclu/i.test(a.situacao)) {
    itens.push(h("button", { class: "botao", onclick: async (ev) => {
      if (!confirm("Processar este projeto no PC (continua de onde parou, com as opções dele)?")) return;
      ev.currentTarget.disabled = true;
      try { await post(`/api/projetos/${enc(id)}/processar`); avisar("Processamento iniciado no PC."); await atualizar(); }
      catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
    } }, "▶ Processar (continua de onde parou)"));
  }
  if (!fila && !a.em_andamento && !revisao && a.a_renderizar > 0) {
    itens.push(h("button", { class: "botao", onclick: async (ev) => {
      const n = a.a_renderizar;
      if (!confirm(`Renderizar ${plural(n, "Short", "Shorts")} no PC (os aprovados que são novos ou mudaram)? O celular avisa quando terminar.`)) return;
      ev.currentTarget.disabled = true;
      try { await post(`/api/projetos/${enc(id)}/renderizar`); avisar("Renderização iniciada no PC."); await atualizar(); }
      catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
    } }, `▶ Renderizar ${plural(a.a_renderizar, "Short", "Shorts")}`));
  }
  return itens;
}

/** "12:05" ou "1:02:03" (o 0 vira "0:00"). */
function textoDoTempo(segundos) {
  const s = Math.max(0, Math.round(Number(segundos) || 0));
  const horas = Math.floor(s / 3600), minutos = Math.floor((s % 3600) / 60), resto = s % 60;
  return horas ? `${horas}:${doisDigitos(minutos)}:${doisDigitos(resto)}` : `${minutos}:${doisDigitos(resto)}`;
}

/** "1:02:03", "62:03" ou "45" → segundos (NaN se não deu para ler). */
function segundosDoTexto(texto) {
  const partes = String(texto).trim().split(":");
  if (!partes.length || partes.length > 3 || partes.some((p) => !/^\d+$/.test(p))) return NaN;
  return partes.reduce((total, p) => total * 60 + Number(p), 0);
}

/** As opções do processamento do projeto e o "Começar" (baixa o vídeo do canal, se preciso, e processa). */
function blocoDasOpcoes(id, projeto, aoComecar) {
  const bloco = h("div", { class: "bloco bloco-opcoes" });
  const andamento = h("div");
  let config = null;
  let tarefa = projeto.tarefa;

  const salvar = async (campo, valor) => {
    try { config = await post(`/api/projetos/${enc(id)}/opcoes`, { [campo]: valor }); }
    catch (e) { avisar(e.message); }
    desenhar();
  };

  // a trilha de áudio de fora: o arquivo vai inteiro no corpo (XMLHttpRequest mostra o envio)
  const enviarTrilha = (arquivo, situacao, botoes) => {
    const pedido = new XMLHttpRequest();
    pedido.open("POST", `/api/projetos/${enc(id)}/trilha?nome=${enc(arquivo.name)}`);
    botoes.forEach((b) => { b.disabled = true; });
    situacao.textContent = "Enviando o áudio… 0%";
    pedido.upload.addEventListener("progress", (ev) => {
      if (ev.lengthComputable) situacao.textContent = `Enviando o áudio… ${Math.floor((100 * ev.loaded) / ev.total)}%`;
    });
    pedido.addEventListener("load", () => {
      let resposta = {};
      try { resposta = JSON.parse(pedido.responseText); } catch (_) { /* resposta sem JSON */ }
      if (pedido.status === 200) { config = resposta; avisar("Trilha de áudio enviada: ela substitui o som do vídeo."); }
      else avisar(resposta.erro || `Não deu para enviar o áudio (erro ${pedido.status}).`);
      desenhar();
    });
    pedido.addEventListener("error", () => { avisar("A conexão com o PC caiu durante o envio."); desenhar(); });
    pedido.send(arquivo);
  };

  const campoDaTrilha = (c, pode) => {
    const escolher = h("input", { type: "file", accept: "audio/*,video/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus", hidden: true });
    const situacao = h("p", { class: "nota" }, c.valor ? `🎵 ${c.valor}` : "O som do próprio vídeo.");
    const enviar = h("button", { class: "botao secundario", disabled: !pode || null, onclick: () => escolher.click() },
      c.valor ? "Trocar o áudio…" : "⬆ Enviar um áudio…");
    const doVideo = c.valor ? h("button", { class: "botao secundario", disabled: !pode || null, onclick: () => {
      if (confirm("Voltar a usar o som do próprio vídeo? O áudio enviado é apagado do PC.")) salvar(c.campo, "");
    } }, "Usar o som do vídeo") : null;
    escolher.addEventListener("change", () => {
      const arquivo = escolher.files && escolher.files[0];
      if (arquivo) enviarTrilha(arquivo, situacao, [enviar, doVideo].filter(Boolean));
    });
    return h("div", { class: "campo-opcao" }, h("div", { class: "rotulo-opcao" }, c.rotulo), situacao,
      h("div", { class: "linha-botoes" }, enviar, doVideo), escolher,
      c.ajuda ? h("p", { class: "nota" }, c.ajuda) : null);
  };

  const campoDaOpcao = (c, pode) => {
    if (c.tipo === "trilha") return campoDaTrilha(c, pode);
    const desligado = !pode || (c.desligado && !c.valor);
    const ajuda = c.desligado && !c.valor ? c.desligado : c.ajuda;
    let controle;
    if (c.tipo === "sim_nao") {
      const caixa = h("input", { type: "checkbox", checked: c.valor || null, disabled: desligado || null });
      caixa.addEventListener("change", () => salvar(c.campo, caixa.checked));
      return h("div", { class: "campo-opcao" }, h("label", { class: "opcao" }, caixa, ` ${c.rotulo}`),
        ajuda ? h("p", { class: "nota" }, ajuda) : null);
    }
    if (c.tipo === "escolha") {
      controle = h("div", { class: "segmentos" }, ...c.opcoes.map(([valor, rotulo]) =>
        h("button", { class: String(c.valor) === String(valor) ? "ativo" : "", disabled: !pode || null,
          onclick: () => { if (String(c.valor) !== String(valor)) salvar(c.campo, valor); } }, rotulo)));
    } else if (c.tipo === "numero") {
      const entrada = h("input", { type: "number", class: "campo", inputmode: "decimal", min: c.min, max: c.max,
        step: c.passo, value: c.valor, disabled: !pode || null });
      entrada.addEventListener("change", () => {
        const valor = Number(entrada.value.replace(",", "."));
        if (Number.isNaN(valor)) { avisar("Escreva um número."); desenhar(); return; }
        salvar(c.campo, valor);
      });
      controle = h("div", { class: "linha-campo" }, entrada, c.unidade ? h("span", { class: "unidade" }, c.unidade) : null);
    } else if (c.tipo === "tempo") {
      const entrada = h("input", { type: "text", class: "campo", inputmode: "numeric", placeholder: "0:00",
        value: textoDoTempo(c.valor), disabled: !pode || null });
      entrada.addEventListener("change", () => {
        const valor = segundosDoTexto(entrada.value);
        if (Number.isNaN(valor)) { avisar("Escreva o tempo como 1:02:03 ou 12:05."); desenhar(); return; }
        salvar(c.campo, valor);
      });
      controle = entrada;
    }
    return h("div", { class: "campo-opcao" }, h("div", { class: "rotulo-opcao" }, c.rotulo), controle,
      ajuda ? h("p", { class: "nota" }, ajuda) : null);
  };

  const acompanhar = async () => {
    while (tarefa && tarefa.rodando) {
      await new Promise((r) => setTimeout(r, 2500));
      if (!document.body.contains(bloco)) return; // saiu da tela: o aviso chega
      try { tarefa = await api("/api/canal/tarefa"); } catch (_) { continue; }
      desenharAndamento();
      if (tarefa && !tarefa.rodando) {
        const soBaixou = tarefa.processar === false;
        if (tarefa.erro) avisar(`${soBaixou ? "Não deu para baixar" : "Não deu para começar"}: ${tarefa.erro}`);
        else { avisar(tarefa.texto); if (soBaixou) await bloco.recarregar(); else await aoComecar(); }
      }
    }
  };

  const desenharAndamento = () => {
    if (!tarefa) { encher(andamento); return; }
    const soBaixar = tarefa.processar === false;
    if (soBaixar && !tarefa.rodando && !tarefa.erro) { encher(andamento); return; } // o vídeo já veio: a prévia mostra
    encher(andamento, h("div", { class: `cartao ${tarefa.erro ? "aviso-bloco" : "destaque"}` },
      h("div", { class: "nome" }, tarefa.rodando ? (soBaixar ? "⏳ Baixando o vídeo…" : "⏳ Começando…")
        : tarefa.erro ? (soBaixar ? "Não deu para baixar o vídeo" : "Não deu para começar") : "Começou"),
      h("p", { class: "nota" }, tarefa.erro || tarefa.texto)));
  };

  // --- o tratamento de imagem, com a prévia de um quadro do vídeo (como o cartão do PC) -----
  let quadroDoTratamento = 0; // "Outro quadro" passa para o próximo
  let divisaoDoTratamento = 50; // onde está a linha entre o antes e o depois, em %
  const salvarVarios = async (mudancas, redesenhar = true) => {
    try { config = await post(`/api/projetos/${enc(id)}/opcoes`, mudancas); }
    catch (e) { avisar(e.message); redesenhar = true; }
    if (redesenhar) desenhar();
  };
  const blocoDoTratamento = (g, pode) => {
    const ajustes = g.campos.filter((c) => c.tipo === "ajuste");
    const outros = g.campos.filter((c) => c.tipo !== "ajuste");
    const caixas = outros.map((c) => campoDaOpcao(c, pode));
    if (!config.video_no_pc) {
      const baixando = tarefa && tarefa.rodando;
      return [h("p", { class: "nota" }, "A prévia mostra um quadro do vídeo com os ajustes, mas o vídeo ainda não está no PC."),
        h("button", { class: "botao secundario", disabled: (!config.pode_mudar || baixando) || null, onclick: async (ev) => {
          if (!confirm("Baixar o vídeo agora pelo YouTube Studio (sem começar o processamento), para ver a prévia?")) return;
          ev.currentTarget.disabled = true;
          try { tarefa = await post(`/api/projetos/${enc(id)}/baixar`); desenhar(); acompanhar(); }
          catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
        } }, baixando ? "⏳ Baixando o vídeo…" : "⬇ Baixar o vídeo para ver a prévia"),
        ...caixas, ...ajustes.map((c) => reguaDoAjuste(c, pode, () => {}))];
    }
    const valores = Object.fromEntries(ajustes.map((c) => [c.campo.replace("tratamento_", ""), c.valor]));
    const base = `/api/projetos/${enc(id)}/tratamento/quadro?i=${quadroDoTratamento}`;
    const aviso = h("p", { class: "nota" }, "Carregando um quadro do vídeo…");
    const original = h("img", { class: "quadro-original", alt: "Quadro original", loading: "lazy" });
    const tratado = h("img", { class: "quadro-tratado", alt: "O mesmo quadro com os ajustes", loading: "lazy" });
    const comparacao = h("div", { class: "comparacao", style: `--divisao:${divisaoDoTratamento}%` }, original, tratado,
      h("div", { class: "divisoria" }), h("span", { class: "rotulo-antes" }, "Antes"), h("span", { class: "rotulo-depois" }, "Depois"));
    const linha = h("input", { type: "range", class: "regua-divisao", min: "0", max: "100", value: String(divisaoDoTratamento),
      "aria-label": "Linha entre o antes e o depois" });
    linha.addEventListener("input", () => { divisaoDoTratamento = Number(linha.value); comparacao.style.setProperty("--divisao", `${divisaoDoTratamento}%`); });
    let espera = null;
    const pedirPrevia = () => {
      clearTimeout(espera);
      espera = setTimeout(() => {
        tratado.src = `${base}&${new URLSearchParams(Object.entries(valores).map(([k, v]) => [k, String(v)]))}`;
      }, 250);
    };
    original.addEventListener("load", () => { aviso.textContent = "Arraste a régua abaixo do quadro para comparar o antes e o depois."; });
    original.addEventListener("error", () => { aviso.textContent = "Não deu para ler um quadro do vídeo."; });
    original.src = `${base}&original=1`;
    pedirPrevia();
    const ligado = (outros.find((c) => c.campo === "tratamento_ativo") || {}).valor;
    return [comparacao, linha, aviso,
      ligado ? null : h("p", { class: "nota" }, "Desligado: o vídeo sai sem o tratamento (a prévia mostra como ficaria)."),
      h("div", { class: "linha-botoes" },
        h("button", { class: "botao secundario", disabled: !pode || null, onclick: async (ev) => {
          ev.currentTarget.disabled = true;
          aviso.textContent = "Medindo a imagem do vídeo…";
          try {
            config = await post(`/api/projetos/${enc(id)}/tratamento/sugerir`);
            avisar(config.sugestao_neutra ? "A imagem já está equilibrada: a sugestão não mudou nada." : "Ajuste automático aplicado: é um ponto de partida, ajuste a seu gosto.");
          } catch (e) { avisar(e.message); }
          desenhar();
        } }, "✨ Ajuste automático"),
        h("button", { class: "botao secundario", onclick: () => { quadroDoTratamento += 1; desenhar(); } }, "Outro quadro")),
      ...caixas,
      ...ajustes.map((c) => reguaDoAjuste(c, pode, (v) => { valores[c.campo.replace("tratamento_", "")] = v; pedirPrevia(); })),
      h("button", { class: "botao secundario", disabled: !pode || null, onclick: () => {
        if (!confirm("Zerar todos os ajustes do tratamento?")) return;
        salvarVarios(Object.fromEntries(ajustes.map((c) => [c.campo, 0])));
      } }, "Zerar os ajustes")];
  };
  // uma régua de −100 a 100: mexer atualiza a prévia; soltar grava no projeto
  const reguaDoAjuste = (c, pode, aoMexer) => {
    const numero = h("span", { class: "valor-ajuste" }, String(Math.round(c.valor)));
    const regua = h("input", { type: "range", min: String(c.min), max: String(c.max), step: String(c.passo),
      value: String(c.valor), disabled: !pode || null, "aria-label": c.rotulo });
    regua.addEventListener("input", () => { numero.textContent = regua.value; aoMexer(Number(regua.value)); });
    regua.addEventListener("change", () => salvarVarios({ [c.campo]: Number(regua.value) }, false));
    regua.addEventListener("dblclick", () => { regua.value = "0"; numero.textContent = "0"; aoMexer(0); salvarVarios({ [c.campo]: 0 }, false); });
    return h("div", { class: "campo-opcao ajuste" },
      h("div", { class: "rotulo-opcao linha-ajuste" }, h("span", {}, c.rotulo), numero), regua,
      c.ajuda ? h("p", { class: "nota" }, c.ajuda) : null);
  };

  const desenhar = () => {
    if (!config) { encher(bloco, h("p", { class: "nota" }, "Carregando as opções…")); return; }
    const pode = config.pode_mudar && !(tarefa && tarefa.rodando);
    const baixado = config.video_no_pc ?? projeto.baixado;
    const comecar = config.pode_mudar ? h("button", { class: "botao", disabled: (tarefa && tarefa.rodando) || null, onclick: async (ev) => {
      const texto = baixado
        ? "Começar o processamento no PC com estas opções?"
        : "Baixar o vídeo pelo YouTube Studio e começar o processamento no PC com estas opções?";
      if (!confirm(texto)) return;
      ev.currentTarget.disabled = true;
      try { tarefa = await post(`/api/projetos/${enc(id)}/comecar`); desenhar(); acompanhar(); }
      catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
    } }, baixado ? "▶ Começar o processamento" : "▶ Começar (baixa o vídeo e processa)") : null;
    encher(bloco,
      h("h3", {}, config.pode_mudar ? "Opções do processamento" : "Opções usadas"),
      config.pode_mudar
        ? h("p", { class: "nota" }, `Ajuste antes de começar: cada mudança já fica salva no projeto.${baixado ? "" : " O vídeo só é baixado quando você tocar em Começar (ou no botão da prévia do tratamento)."}`)
        : h("p", { class: "nota" }, config.motivo),
      ...config.grupos.map((g) => h("details", { class: "grupo-opcoes", open: config.pode_mudar || null },
        h("summary", {}, g.titulo), ...(g.tratamento ? blocoDoTratamento(g, pode) : g.campos.map((c) => campoDaOpcao(c, pode))))),
      andamento, comecar);
    desenharAndamento();
  };

  desenhar();
  api(`/api/projetos/${enc(id)}/opcoes`).then((c) => { config = c; desenhar(); acompanhar(); })
    .catch((e) => encher(bloco, h("p", { class: "nota" }, e.message)));
  bloco.recarregar = async () => { try { config = await api(`/api/projetos/${enc(id)}/opcoes`); desenhar(); } catch (_) { /* fica como está */ } };
  return bloco;
}

async function telaProjeto(id) {
  await entrarNoProjeto(id);
  voltarDoShort = `#/p/${enc(id)}`;
  const caixaAndamento = h("div");
  const caixaAcoes = h("div");
  const caixaShorts = h("div");
  const projeto = await api(`/api/projetos/${enc(id)}`);
  cabecalho(projeto.nome, dataBr(projeto.data), "");
  projetoAtualNome = projeto.nome;
  const situacao = h("div", { class: "chips" });
  const pintarSituacao = (a) => encher(situacao, 
    h("span", { class: `chip ${classeDaSituacao(a.situacao)}` }, a.situacao),
    h("span", { class: "chip" }, projeto.resultados));
  pintarSituacao(projeto.andamento);
  desenharAndamento(caixaAndamento, projeto.andamento);

  if (!rotaAnterior.startsWith(`#/p/${enc(id)}/`)) { filtroDosShorts = "todos"; categoriaDosShorts = ""; }
  const segmentos = h("div", { class: "filtros" });
  const titulo = h("h2");
  // a categoria: um botão que flutua no canto da tela e abre a lista das cores
  const flutuante = h("div", { class: "seletor-flutuante" });
  let listaAberta = false;
  let shorts = [];
  const pintarCategorias = () => {
    const contagem = new Map();
    for (const s of shorts) {
      const c = contagem.get(s.categoria.chave) || { ...s.categoria, n: 0 };
      c.n += 1;
      contagem.set(s.categoria.chave, c);
    }
    const categorias = [...contagem.values()].sort((a, b) => a.rotulo.localeCompare(b.rotulo, "pt-BR"));
    if (categoriaDosShorts && !contagem.has(categoriaDosShorts)) categoriaDosShorts = "";
    flutuante.hidden = categorias.length < 2;
    const atual = contagem.get(categoriaDosShorts);
    const escolher = (chave) => { categoriaDosShorts = chave; listaAberta = false; pintarShorts(); };
    const opcao = (chave, cor, rotulo, n) => h("button", {
      class: `opcao-categoria ${categoriaDosShorts === chave ? "ativo" : ""}`, role: "option",
      "aria-selected": String(categoriaDosShorts === chave), onclick: () => escolher(chave),
    }, h("span", { class: `cor ${cor ? "" : "todas"}`, style: cor ? `background:${cor}` : null }), h("span", { class: "rotulo" }, rotulo), h("span", { class: "conta" }, n));
    encher(flutuante,
      listaAberta ? h("div", { class: "lista-categorias", role: "listbox", "aria-label": "Categoria do Short" },
        opcao("", "", "Todas as categorias", shorts.length),
        ...categorias.map((c) => opcao(c.chave, c.cor, c.rotulo, c.n))) : null,
      h("button", { class: `botao-categoria ${atual ? "filtrando" : ""}`, "aria-expanded": String(listaAberta),
        onclick: () => { listaAberta = !listaAberta; pintarCategorias(); } },
      h("span", { class: `cor ${atual ? "" : "todas"}`, style: atual ? `background:${atual.cor}` : null }),
      atual ? atual.rotulo : "Categoria", h("span", { class: "seta" }, listaAberta ? "▾" : "▴")));
  };
  const pintarShorts = () => {
    titulo.textContent = `Shorts (${shorts.filter((s) => s.incluir).length} aprovados)`;
    const daCategoria = shorts.filter((s) => !categoriaDosShorts || s.categoria.chave === categoriaDosShorts);
    encher(segmentos, ...FILTROS_DOS_SHORTS.map(([k, r, entra]) =>
      h("button", { class: filtroDosShorts === k ? "ativo" : "", onclick: () => { filtroDosShorts = k; pintarShorts(); } },
        r, h("span", { class: "conta" }, daCategoria.filter(entra).length))));
    const entra = (FILTROS_DOS_SHORTS.find(([k]) => k === filtroDosShorts) || FILTROS_DOS_SHORTS[0])[2];
    const visiveis = daCategoria.filter(entra);
    encher(caixaShorts, ...(visiveis.length
      ? visiveis.map((s) => linhaDoShort(id, s))
      : [h("div", { class: "vazio-lista" }, shorts.length ? "Nenhum Short neste filtro." : "Este projeto ainda não tem Shorts.")]));
    pintarCategorias();
  };
  shorts = await api(`/api/projetos/${enc(id)}/shorts`);
  pintarShorts();
  const livre = shorts.length
    ? h("a", { class: "botao secundario", href: `#/p/${enc(id)}/livre` }, "✚ Shorts livre: criar um Short de qualquer trecho")
    : null;
  const exportar = projeto.tem_transcricao
    ? h("a", { class: "botao secundario exportar-legenda", href: `/api/projetos/${enc(id)}/legenda.txt`, download: "", onclick: (ev) => {
      if (!confirm("Exportar a legenda do sermão? O celular baixa um arquivo de texto com cada frase e o tempo dela.")) ev.preventDefault();
    } }, "⬇ Exportar a legenda do sermão (texto com os tempos)")
    : null;
  const baixarAudio = projeto.audio_pronto
    ? h("a", { class: "botao secundario exportar-legenda", href: `/api/projetos/${enc(id)}/audio`, download: "" },
      `⬇ Baixar o áudio do sermão (${projeto.audio_pronto.split(".").pop().toUpperCase()})`)
    : null;

  const etapasAbertas = projeto.andamento.em_andamento || projeto.andamento.esperando_revisao;
  const blocoEtapas = h("details", { open: etapasAbertas || null }, h("summary", {}, "Etapas do processamento"), caixaAndamento);
  let atualizar = null;
  const naoComecou = projeto.nao_comecou;
  const opcoes = blocoDasOpcoes(id, projeto, async () => { if (atualizar) await atualizar(); });
  // antes de começar, as opções vêm primeiro; depois, ficam recolhidas lá embaixo
  const opcoesDepois = naoComecou ? null : h("details", { class: "bloco-espaco" }, h("summary", {}, "⚙ Opções usadas no processamento"), opcoes);
  encher(tela, situacao, caixaAcoes, baixarAudio, naoComecou ? opcoes : null, blocoEtapas, titulo, segmentos, livre, caixaShorts, exportar, opcoesDepois, blocoDoEspaco(id), flutuante);

  let andava = projeto.andamento.em_andamento;
  let comecou = !naoComecou;
  atualizar = async () => {
    const a = await api(`/api/projetos/${enc(id)}/andamento`);
    if (!comecou && !a.etapas.every((e) => e.status === "pendente")) { comecou = true; opcoes.recarregar(); }
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
    const aprovados = shorts.filter((s) => s.incluir).length;
    aprovar.textContent = `Aprovar e continuar (${plural(aprovados, "aprovado", "aprovados")})`;
    aprovar.disabled = aprovados === 0;
    encher(lista, ...shorts.map((s) => {
      const caixa = h("button", { class: `marcador ${s.incluir ? "ligado" : ""}`, "aria-label": s.incluir ? "Tirar a aprovação" : "Aprovar", "aria-pressed": String(s.incluir),
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
          h("div", { class: "nome" }, s.titulo), h("div", { class: "meta" }, `Nota ${Math.round(s.nota)} · ${trechoDoShort(s)}`), chipsDoShort(s)));
    }));
  };
  aprovar.addEventListener("click", async () => {
    if (!confirm("Confirmar os cortes aprovados e continuar o processamento no PC?")) return;
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

/** Ouvir com a legenda: o PC monta só o áudio do Short e a palavra falada acende. No modo correção
 * (o "✎ Legenda"), tocar numa frase a abre ali mesmo para corrigir, com o áudio tocando ao lado. */
async function montarAudicao(area, base, { aoMudar = async () => {}, aoTextoInteiro = () => {} } = {}) {
  encher(area, h("div", { class: "bloco" }, "Carregando a legenda…"));
  let leg = await api(`${base}/legenda`);
  if (!leg.fonte) {
    const aviso = h("div", { class: "bloco aviso-bloco" }, leg.aviso);
    encher(area, aviso);
    return { legenda: aviso, limpar: () => {}, corrigir: () => {} };
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
  const barraDoAudio = h("div", { class: "audicao-barra" },
    h("div", { class: "audicao-linha" }, tocar, h("div", { class: "audicao-meio" }, barra, h("div", { class: "audicao-info" }, tempo, estado)), voltar, velocidade));
  const aviso = leg.aviso ? h("div", { class: "bloco aviso-bloco" }, leg.aviso) : null;
  const avisoDaCorrecao = h("div", { class: "bloco nota-correcao" });
  const legendaViva = h("div", { class: "legenda-viva" });

  let palavras = [];
  let spans = []; // alinhado com `palavras` (null nas palavras da frase em edição)
  let fraseDe = [];
  let linhas = [];
  let corrigindo = false;
  let editando = -1;
  let ate = null; // "Ouvir a frase": para sozinho neste tempo
  let atual = -1;

  const editorDaFrase = (k) => {
    const frase = leg.frases[k];
    const antes = frase.map((w) => w.t).join(" ");
    const caixa = h("textarea", { class: "caixa-frase", rows: "3", spellcheck: "true", lang: "pt-BR", autocapitalize: "sentences" });
    caixa.value = antes;
    const salvar = h("button", { class: "botao", onclick: async () => {
      salvar.disabled = true;
      try {
        const novo = await editar(`${base}/legenda`, { frase: k, antes, texto: caixa.value });
        editando = -1;
        leg = await api(`${base}/legenda`);
        desenhar();
        await aoMudar(novo);
        avisar("Frase corrigida no PC.");
      } catch (e) { avisar(e.message); salvar.disabled = false; }
    } }, "✓ Salvar");
    const ouvir = h("button", { class: "botao secundario", onclick: () => {
      audio.currentTime = Math.max(0, frase[0].i - 0.25);
      ate = frase[frase.length - 1].f + 0.35;
      audio.play().catch(() => {});
    } }, "▶ Ouvir");
    const cancelar = h("button", { class: "botao secundario", "aria-label": "Cancelar", onclick: () => { editando = -1; desenhar(); } }, "✕");
    setTimeout(() => caixa.focus(), 60);
    return h("div", { class: "frase editando" }, caixa, h("div", { class: "linha-botoes" }, ouvir, salvar, cancelar));
  };

  const abrirFrase = (k) => {
    if (editando === k) return;
    editando = k;
    desenhar();
    const frase = leg.frases[k];
    audio.pause();
    audio.currentTime = Math.max(0, frase[0].i - 0.25);
  };

  function desenhar() {
    palavras = leg.frases.flat();
    spans = [];
    fraseDe = [];
    atual = -1;
    leg.frases.forEach((frase, k) => { for (let j = 0; j < frase.length; j++) fraseDe.push(k); });
    linhas = leg.frases.map((frase, k) => {
      if (k === editando) {
        for (let j = 0; j < frase.length; j++) spans.push(null);
        return editorDaFrase(k);
      }
      const linha = h("p", { class: `frase${corrigindo ? " corrigivel" : ""}` }, ...frase.flatMap((w, j) => {
        const span = h("span", { class: "palavra", onclick: (ev) => {
          if (corrigindo) { ev.stopPropagation(); abrirFrase(k); return; }
          ate = null;
          audio.currentTime = w.i;
          audio.play().catch(() => {});
        } }, w.t);
        spans.push(span);
        return j ? [" ", span] : [span];
      }));
      if (corrigindo) linha.addEventListener("click", () => abrirFrase(k));
      return linha;
    });
    encher(legendaViva, ...linhas);
    encher(avisoDaCorrecao,
      h("p", { class: "nota" }, "Toque numa frase para corrigi-la aqui mesmo; o áudio continua acendendo as palavras."),
      h("button", { class: "botao secundario", onclick: aoTextoInteiro }, "Editar o texto inteiro de uma vez"));
    avisoDaCorrecao.hidden = !corrigindo;
    acender();
  }

  function acender() {
    const t = audio.currentTime;
    if (ate !== null && t >= ate) { ate = null; audio.pause(); }
    barra.value = String(t);
    tempo.textContent = `${minSeg(t)} / ${minSeg(leg.duracao)}`;
    // a última palavra que já começou (busca binária: as palavras estão em ordem)
    let lo = 0, hi = palavras.length - 1, achada = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (palavras[m].i <= t + 0.03) { achada = m; lo = m + 1; } else hi = m - 1; }
    if (achada === atual) return;
    if (atual >= 0) {
      if (spans[atual]) spans[atual].classList.remove("atual");
      if (linhas[fraseDe[atual]]) linhas[fraseDe[atual]].classList.remove("atual");
    }
    if (achada >= 0) {
      if (spans[achada]) spans[achada].classList.add("atual");
      const linha = linhas[fraseDe[achada]];
      if (linha && !linha.classList.contains("atual")) {
        linha.classList.add("atual");
        if (!legendaViva.hidden && editando < 0) linha.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
    atual = achada;
  }

  encher(area, barraDoAudio, aviso, avisoDaCorrecao, legendaViva, audio);
  barraDoAudio.style.top = `${document.querySelector(".topo").offsetHeight}px`;
  desenhar();

  let quadro = 0;
  let arrastando = false;
  const laco = () => { if (!arrastando) acender(); if (!audio.paused) quadro = requestAnimationFrame(laco); };
  audio.addEventListener("play", () => { tocar.textContent = "❚❚"; tocar.setAttribute("aria-label", "Pausar"); cancelAnimationFrame(quadro); laco(); });
  audio.addEventListener("pause", () => { tocar.textContent = "▶"; tocar.setAttribute("aria-label", "Tocar"); acender(); });
  audio.addEventListener("seeked", acender);
  audio.addEventListener("waiting", () => { estado.textContent = "Carregando…"; });
  audio.addEventListener("canplay", () => { estado.textContent = ""; });
  audio.addEventListener("error", () => { estado.textContent = "O PC não conseguiu montar o áudio."; });
  tocar.addEventListener("click", () => { ate = null; if (audio.paused) audio.play().catch(() => {}); else audio.pause(); });
  voltar.addEventListener("click", () => { audio.currentTime = Math.max(0, audio.currentTime - 3); });
  velocidade.addEventListener("click", () => {
    vel = (vel + 1) % VELOCIDADES.length;
    audio.playbackRate = VELOCIDADES[vel];
    velocidade.textContent = `${String(VELOCIDADES[vel]).replace(".", ",")}×`;
  });
  barra.addEventListener("input", () => { arrastando = true; tempo.textContent = `${minSeg(+barra.value)} / ${minSeg(leg.duracao)}`; });
  barra.addEventListener("change", () => { arrastando = false; ate = null; audio.currentTime = +barra.value; });
  return {
    legenda: legendaViva,
    limpar: () => { cancelAnimationFrame(quadro); audio.pause(); audio.removeAttribute("src"); audio.load(); },
    corrigir: (sim) => { if (corrigindo === sim) return; corrigindo = sim; editando = -1; desenhar(); },
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

/** O ajuste fino da legenda: cada palavra do Short com o início e o fim, ouvindo o sermão. Nada vai
 * ao PC até "Salvar no PC". Devolve `{ pendente(), limpar() }` para a tela do Short perguntar antes
 * de sair com mudanças e parar o som. */
async function editorDoAjusteFino(painel, base, aoSalvar, aoFechar) {
  const ed = await api(`${base}/ajuste_fino`);
  const original = ed.palavras.map((p) => [p.texto, p.inicio, p.fim]);
  let itens = original.map((p) => [...p]);
  let atual = 0;
  let tocando = null; // índice da palavra que toca agora
  let paraEm = null; // segundos (do vídeo) em que o som para sozinho ("Ouvir esta palavra")
  let quadro = 0;
  const MINIMA = 0.04;
  const som = h("audio", { preload: "auto", src: `${base}/ajuste_fino/som` });
  let semSom = "";
  som.addEventListener("error", () => { semSom = "Não deu para tocar o som do trecho (o vídeo original ainda está no PC?)."; pintarControles(); });
  const seguintes = h("input", { type: "checkbox", id: "fino-seguintes" });
  const lista = h("div", { class: "fino-lista" });
  const controles = h("div", { class: "fino-controles" });
  const editor = h("div", { class: "fino-editor" });
  const nota = h("p", { class: "nota" });

  const tempo = (s) => {
    const c = Math.max(0, Math.round(s * 100));
    const m = Math.floor(c / 6000), seg = Math.floor((c % 6000) / 100), cent = c % 100;
    return `${m}:${doisDigitos(seg)},${doisDigitos(cent)}`;
  };
  const posicao = () => ed.som_de + som.currentTime;
  const pendente = () => JSON.stringify(itens) !== JSON.stringify(original);
  const limpar = () => { som.pause(); cancelAnimationFrame(quadro); };

  const palavraEm = (s) => {
    for (let k = itens.length - 1; k >= 0; k--) {
      if (itens[k][1] <= s) return s <= itens[k][2] + 0.05 ? k : null;
    }
    return null;
  };
  const acompanhar = () => {
    if (som.paused) { tocando = null; pintarLista(); pintarControles(); return; }
    const agora = posicao();
    if (paraEm !== null && agora >= paraEm) { som.pause(); paraEm = null; }
    const k = palavraEm(agora);
    if (k !== tocando) {
      tocando = k; pintarLista();
      const el = lista.children[k];
      if (el && paraEm === null) el.scrollIntoView({ block: "nearest" });
    }
    const rotulo = controles.querySelector(".fino-posicao");
    if (rotulo) rotulo.textContent = tempo(agora);
    quadro = requestAnimationFrame(acompanhar);
  };
  const tocar = async (desde, ate = null) => {
    document.querySelectorAll("audio").forEach((a) => { if (a !== som) a.pause(); });
    som.currentTime = Math.max(0, desde - ed.som_de);
    paraEm = ate;
    try { await som.play(); } catch (e) { avisar("Toque de novo para ouvir."); return; }
    cancelAnimationFrame(quadro);
    quadro = requestAnimationFrame(acompanhar);
    pintarControles();
  };

  const mover = (ponta, delta) => {
    if (!delta) return;
    const item = itens[atual];
    if (seguintes.checked) { // pelo início anda a palavra inteira; pelo fim, só o fim dela
      if (ponta === "inicio") item[1] += delta;
      item[2] += delta;
      for (const p of itens.slice(atual + 1)) { p[1] += delta; p[2] += delta; }
    } else if (ponta === "inicio") item[1] = Math.min(item[1] + delta, item[2] - MINIMA);
    else item[2] = Math.max(item[2] + delta, item[1] + MINIMA);
    for (const p of itens.slice(atual)) {
      p[1] = Math.min(Math.max(p[1], ed.inicio), ed.fim);
      p[2] = Math.min(Math.max(p[2], p[1]), ed.fim);
    }
    pintarTudo();
  };
  const agora = (ponta) => {
    if (som.paused) { avisar("Toque em ▶ Tocar e aperte ⏱ quando a palavra começar (ou terminar)."); return; }
    const instante = posicao() - 0.12 * som.playbackRate; // desconta o tempo de reação
    mover(ponta, instante - itens[atual][ponta === "inicio" ? 1 : 2]);
  };
  const acrescentar = () => {
    const [, inicio, fim] = itens[atual];
    const seguinte = atual + 1 < itens.length ? itens[atual + 1][1] : ed.fim;
    let nova;
    if (seguinte - fim >= 0.15) nova = ["…", fim, Math.min(seguinte, fim + 0.6)];
    else { const meio = (inicio + fim) / 2; itens[atual][2] = meio; nova = ["…", meio, fim]; }
    itens.splice(atual + 1, 0, nova);
    atual += 1;
    pintarTudo();
    const campo = editor.querySelector("input.campo");
    if (campo) { campo.focus(); campo.select(); }
  };
  const tirar = () => {
    if (itens.length <= 1) return;
    itens.splice(atual, 1);
    atual = Math.min(atual, itens.length - 1);
    pintarTudo();
  };

  const pintarControles = () => {
    const velocidade = (v, r) => h("button", { class: som.playbackRate === v ? "ativo" : "", onclick: () => { som.playbackRate = v; pintarControles(); } }, r);
    encher(controles,
      h("button", { class: "botao", disabled: semSom ? true : null, onclick: () => {
        if (!som.paused) { som.pause(); return; }
        tocar(itens[atual][1] - 0.3);
      } }, som.paused ? "▶ Tocar daqui" : "⏸ Parar"),
      h("div", { class: "segmentos fino-velocidade" }, velocidade(1, "1×"), velocidade(0.75, "0,75×"), velocidade(0.5, "0,5×")),
      h("span", { class: "fino-posicao nota" }, som.paused ? "" : tempo(posicao())),
      semSom ? h("p", { class: "nota erro" }, semSom) : null);
  };
  const pintarLista = () => encher(lista, ...itens.map(([texto], k) => {
    const mudou = !original[k] || JSON.stringify(original[k]) !== JSON.stringify(itens[k]);
    return h("button", { class: `fino-palavra ${k === atual ? "atual" : ""} ${k === tocando ? "tocando" : ""} ${mudou ? "mudou" : ""}`,
      onclick: () => { atual = k; pintarTudo(); } }, texto || "∅");
  }));
  const linhaDaPonta = (ponta, rotulo) => {
    const i = ponta === "inicio" ? 1 : 2;
    const botao = (d) => h("button", { class: "botao-ferramenta", onclick: () => mover(ponta, d) }, `${d > 0 ? "+" : "−"}${String(Math.abs(d)).replace(".", ",")}`);
    return h("div", { class: "fino-ponta" }, h("span", { class: "fino-rotulo" }, rotulo),
      botao(-0.1), botao(-0.02), h("span", { class: "fino-tempo" }, tempo(itens[atual][i])), botao(0.02), botao(0.1),
      h("button", { class: "botao-ferramenta destaque", onclick: () => agora(ponta) }, "⏱ Agora"));
  };
  const pintarEditor = () => {
    const [texto, inicio, fim] = itens[atual];
    const campo = h("input", { class: "campo", value: texto, autocapitalize: "off", spellcheck: "true", lang: "pt-BR",
      "aria-label": "Texto da palavra" });
    campo.addEventListener("change", () => { itens[atual][0] = campo.value.trim().split(/\s+/).join(" "); pintarLista(); });
    encher(editor,
      h("div", { class: "fino-cabeca" }, h("strong", {}, `Palavra ${atual + 1} de ${itens.length}`),
        h("span", { class: "nota" }, `dura ${String((fim - inicio).toFixed(2)).replace(".", ",")} s`)),
      campo,
      linhaDaPonta("inicio", "Começa"),
      linhaDaPonta("fim", "Termina"),
      h("label", { class: "opcao", for: "fino-seguintes" }, seguintes, " Mover as seguintes junto"),
      h("div", { class: "linha-botoes" },
        h("button", { class: "botao secundario", disabled: semSom ? true : null, onclick: () => tocar(inicio - 0.25, fim + 0.25) }, "▶ Ouvir"),
        h("button", { class: "botao secundario", onclick: acrescentar }, "＋ Depois"),
        h("button", { class: "botao secundario", onclick: tirar }, "✕ Tirar")),
      h("div", { class: "linha-botoes" },
        h("button", { class: "botao secundario", disabled: atual === 0 || null, onclick: () => { atual -= 1; pintarTudo(); } }, "◀ Anterior"),
        h("button", { class: "botao secundario", disabled: atual >= itens.length - 1 || null, onclick: () => { atual += 1; pintarTudo(); } }, "Próxima ▶")));
  };
  const pintarTudo = () => { pintarLista(); pintarEditor(); pintarControles(); };

  const salvar = h("button", { class: "botao", onclick: async () => {
    const campo = editor.querySelector("input.campo");
    if (campo) itens[atual][0] = campo.value.trim().split(/\s+/).join(" ");
    salvar.disabled = true;
    try {
      const novo = await editar(`${base}/ajuste_fino`, { palavras: itens.map(([texto, inicio, fim]) => ({ texto, inicio, fim })) });
      limpar();
      avisar("Ajuste fino salvo no PC.");
      await aoSalvar(novo);
    } catch (e) { avisar(e.message); salvar.disabled = false; }
  } }, "Salvar no PC");
  const desfazer = ed.ajustado ? h("button", { class: "botao secundario", onclick: async () => {
    if (!confirm("Voltar os tempos das palavras deste Short aos da transcrição? O texto corrigido continua.")) return;
    try { const novo = await editar(`${base}/ajuste_fino/desfazer`, {}); limpar(); avisar("Ajuste fino desfeito."); await aoSalvar(novo); }
    catch (e) { avisar(e.message); }
  } }, "Desfazer o ajuste fino") : null;
  const fechar = h("button", { class: "botao secundario", onclick: () => {
    if (pendente() && !confirm("Fechar sem salvar o ajuste fino?")) return;
    limpar(); aoFechar();
  } }, "Fechar");

  nota.textContent = "Toque numa palavra, ouça e acerte o texto, o começo e o fim. ⏱ Agora marca o instante que está tocando (mais fácil em 0,5×). As palavras em laranja mudaram.";
  encher(painel, h("div", { class: "bloco fino" }, h("h3", {}, "🎯 Ajuste fino da legenda"), nota, controles, lista, editor,
    h("div", { class: "linha-botoes" }, fechar, salvar), desfazer, som));
  pintarTudo();
  return { pendente, limpar };
}

/** Início, fim e borracha do corte como RASCUNHO no celular: nada vai ao PC (nem o áudio é refeito)
 * até "Salvar alterações", trocar de ferramenta ou de aba, ou sair do Short. Devolve
 * `{ pendente(), salvar() }` para a tela do Short salvar sozinha nessas horas. */
async function editorDasPontas(painel, base, aoSalvar, aoFechar) {
  // o texto em volta: ~600 palavras de cada lado (uns 5 minutos); "Mostrar mais" pede mais ao PC
  // sem perder o rascunho (ele guarda as posições das palavras na transcrição, que não mudam)
  let lados = { antes: 600, depois: 600 };
  let ed = await api(`${base}/edicao`);
  let porN = new Map(ed.contexto.map((w) => [w.n, w]));
  let original = new Set(ed.contexto.filter((w) => w.dentro).map((w) => w.n));
  let incl = new Set(original);
  let ferramenta = "seta"; // "seta", "borracha" (uma palavra) ou "trecho" (dois toques)
  let ancora = null;
  let salvando = false;

  const ordenadas = () => [...incl].sort((a, b) => a - b);
  const pendente = () => incl.size !== original.size || [...incl].some((n) => !original.has(n));
  const faixas = () => {
    const lista = ordenadas();
    const resultado = [];
    for (const n of lista) {
      const ultima = resultado[resultado.length - 1];
      if (ultima && n === ultima[1] + 1) ultima[1] = n; else resultado.push([n, n]);
    }
    return resultado;
  };
  const duracaoEstimada = () => faixas().reduce((s, [a, b]) => s + (porN.get(b).f - porN.get(a).i), 0);
  const faixaDe = (a, b) => { const r = []; for (let n = Math.min(a, b); n <= Math.max(a, b); n++) if (porN.has(n)) r.push(n); return r; };

  const resumo = h("div");
  const texto = h("div", { class: "contexto-palavras" });
  async function carregarMais(lado) {
    const ancoraDaVista = lado === "antes" ? ed.contexto[0] : null;
    lados = { ...lados, [lado]: lados[lado] + 1200 };
    try {
      ed = await api(`${base}/edicao?antes=${lados.antes}&depois=${lados.depois}`);
    } catch (e) { avisar(e.message); return; }
    porN = new Map(ed.contexto.map((w) => [w.n, w]));
    pintar();
    if (ancoraDaVista) { const el = texto.querySelector(`[data-n="${ancoraDaVista.n}"]`); if (el) el.scrollIntoView({ block: "start" }); }
  }
  const ferramentas = h("div", { class: "segmentos" });
  const acoes = h("div");

  async function salvar() {
    if (!pendente() || salvando) return null;
    const lista = ordenadas();
    if (!lista.length) { avisar("O corte ficaria sem nenhuma palavra."); return null; }
    salvando = true;
    try {
      const novo = await editar(`${base}/pontas`, { indices: lista });
      original = new Set(incl);
      avisar("Alterações salvas no PC.");
      return novo;
    } catch (e) { avisar(e.message); return null; }
    finally { salvando = false; }
  }

  function pintar() {
    const lista = ordenadas();
    const primeira = lista[0], ultima = lista[lista.length - 1];
    const dur = lista.length ? duracaoEstimada() : 0;
    const curto = dur < ed.minimo_s;
    encher(resumo,
      h("p", { class: "nota" }, `Duração ≈ ${duracao(dur)}${faixas().length > 1 ? ` (${faixas().length - 1} pedaço(s) cortado(s) no meio)` : ""}.`),
      curto ? h("p", { class: "nota aviso-bloco" }, `Um Short precisa de pelo menos ${ed.minimo_s} s.`) : null,
      dur > 180 ? h("p", { class: "nota aviso-bloco" }, "Acima de 3 minutos o corte não pode ser um Short.") : null,
      h("p", { class: "nota" }, {
        seta: "Toque numa palavra para começar ou terminar nela.",
        borracha: "Toque numa palavra do meio para cortá-la (toque de novo para devolver).",
        trecho: ancora === null ? "Toque na primeira palavra do trecho a cortar…" : "…e agora na última: o trecho todo sai (ou volta).",
      }[ferramenta]));
    encher(ferramentas, ...[["seta", "↔ Início e fim"], ["borracha", "✂ Borracha"], ["trecho", "✂ Trecho"]].map(([k, r]) =>
      h("button", { class: ferramenta === k ? "ativo" : "", onclick: () => { ferramenta = k; ancora = null; pintar(); } }, r)));
    encher(texto,
      ed.tem_mais_antes ? h("button", { class: "botao-pequeno mais-texto", onclick: () => carregarMais("antes") }, "⬆ Mostrar mais texto antes") : null,
      ...ed.contexto.flatMap((w) => {
      const dentroDoCorte = lista.length && w.n >= primeira && w.n <= ultima;
      const classe = incl.has(w.n) ? "dentro" : dentroDoCorte ? "cortada" : "fora";
      const pedacos = [];
      if (w.n === primeira) pedacos.push(h("span", { class: "marca-ponta", id: "marca-inicio" }, "▶"));
      pedacos.push(h("span", { class: `palavra-ctx ${classe}${ancora === w.n ? " ancora" : ""}`, "data-n": String(w.n), onclick: () => tocar(w) }, w.t));
      if (w.n === ultima) pedacos.push(h("span", { class: "marca-ponta" }, "◀"));
      pedacos.push(" ");
      return pedacos;
    }),
      ed.tem_mais_depois ? h("button", { class: "botao-pequeno mais-texto", onclick: () => carregarMais("depois") }, "⬇ Mostrar mais texto depois") : null);
    const mudou = pendente();
    encher(acoes,
      mudou ? h("p", { class: "nota aviso-bloco" }, "Alterações ainda não salvas (o áudio é refeito ao salvar).") : null,
      h("div", { class: "linha-botoes" },
        h("button", { class: "botao", disabled: !mudou || curto || null, onclick: async () => { const novo = await salvar(); if (novo) await aoSalvar(novo); } }, "💾 Salvar alterações"),
        mudou ? h("button", { class: "botao secundario", onclick: () => { incl = new Set(original); ancora = null; pintar(); } }, "Descartar") : null),
      h("button", { class: "botao secundario", onclick: aoFechar }, "Fechar"));
  }

  async function tocar(w) {
    const lista = ordenadas();
    const primeira = lista[0], ultima = lista[lista.length - 1];
    if (ferramenta === "borracha") {
      if (w.n < primeira || w.n > ultima) { avisar("Fora do corte: use “Início e fim” para aumentar."); return; }
      if (incl.has(w.n)) incl.delete(w.n); else incl.add(w.n);
    } else if (ferramenta === "trecho") {
      if (ancora === null) { ancora = w.n; pintar(); return; }
      const trecho = faixaDe(ancora, w.n);
      const devolver = !incl.has(ancora);
      for (const n of trecho) { if (devolver) incl.add(n); else incl.delete(n); }
      ancora = null;
    } else {
      const opcoes = [];
      if (!lista.length || w.n <= ultima) opcoes.push(["inicio", "▶ Começar nesta palavra"]);
      if (!lista.length || w.n >= primeira) opcoes.push(["fim", "Terminar nesta palavra ◀"]);
      const escolha = await escolher(`“${w.t}”`, opcoes);
      if (escolha === "inicio") {
        for (const n of [...incl]) if (n < w.n) incl.delete(n);
        if (w.n < primeira) for (const n of faixaDe(w.n, primeira - 1)) incl.add(n);
        incl.add(w.n);
      } else if (escolha === "fim") {
        for (const n of [...incl]) if (n > w.n) incl.delete(n);
        if (w.n > ultima) for (const n of faixaDe(ultima + 1, w.n)) incl.add(n);
        incl.add(w.n);
      } else return;
    }
    pintar();
  }

  encher(painel, h("div", { class: "bloco" }, h("h3", {}, "Início, fim e borracha"), ferramentas, resumo, texto, acoes));
  pintar();
  const marca = document.getElementById("marca-inicio");
  if (marca) marca.scrollIntoView({ block: "center" });
  return { pendente, salvar };
}

const doisDigitos = (n) => String(n).padStart(2, "0");
const dataLocal = (d) => `${d.getFullYear()}-${doisDigitos(d.getMonth() + 1)}-${doisDigitos(d.getDate())}`;

/** `25/12/2026` → `2026-12-25` ("" se não é uma data que existe). */
function isoDeDataBr(texto) {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(texto.trim());
  if (!m) return "";
  const [dia, mes, ano] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const d = new Date(ano, mes - 1, dia);
  if (d.getFullYear() !== ano || d.getMonth() !== mes - 1 || d.getDate() !== dia) return "";
  return dataLocal(d);
}

/** Campo de data sempre em dia/mês/ano (o `<input type="date">` segue o idioma do celular e pode
 * mostrar mês/dia/ano). As barras entram sozinhas; o 📅 abre o calendário do celular. */
function campoDeData(inicial) {
  const campo = h("input", { type: "text", class: "campo", inputmode: "numeric", placeholder: "dd/mm/aaaa",
    maxlength: "10", autocomplete: "off", value: dataBr(dataLocal(inicial)) });
  campo.addEventListener("input", () => {
    const n = campo.value.replace(/\D/g, "").slice(0, 8);
    campo.value = [n.slice(0, 2), n.slice(2, 4), n.slice(4)].filter(Boolean).join("/");
  });
  const nativo = h("input", { type: "date", class: "data-escondida", tabindex: "-1", "aria-hidden": "true" });
  nativo.addEventListener("change", () => { if (nativo.value) campo.value = dataBr(nativo.value); });
  const calendario = h("button", { type: "button", class: "botao-icone", "aria-label": "Abrir o calendário", onclick: () => {
    nativo.value = isoDeDataBr(campo.value) || dataLocal(new Date());
    try { nativo.showPicker(); } catch (_) { nativo.click(); }
  } }, "📅");
  return { caixa: h("div", { class: "campo-data" }, campo, calendario, nativo), iso: () => isoDeDataBr(campo.value) };
}

/** Postar no YouTube pelo PC (o YouTube Studio, sem a API), pela fila do PC: programar ou
 * publicar agora (renderiza antes, se o Short ainda não está pronto) e trocar o horário de um
 * Short já programado (até 5 minutos antes dele sair). */
async function editorDaPostagem(painel, base, s, aoTerminar, aoFechar) {
  const prep = await api(`${base}/postar`);
  const yt = prep.youtube;
  const estado = yt ? h("div", { class: `bloco ${yt.estado === "erro" || yt.estado === "rascunho" ? "aviso-bloco" : ""}` },
    h("h3", {}, "No YouTube"),
    h("p", {}, { agendado: "📅 Programado", enviado: "✔ Publicado", rascunho: "⚠ Ficou como rascunho privado (a próxima tentativa o retoma)", erro: "✕ A última tentativa falhou" }[yt.estado] || yt.estado,
      prep.youtube_quando ? ` para ${dataBr(prep.youtube_quando.slice(0, 10))} às ${prep.youtube_quando.slice(11, 16)}` : ""),
    yt.url ? h("p", {}, h("a", { href: yt.url, target: "_blank", rel: "noopener" }, yt.url)) : null,
    yt.detalhe ? h("p", { class: "nota" }, yt.detalhe) : null) : null;
  const naFila = prep.fila ? h("div", { class: `bloco ${prep.fila.situacao === "erro" ? "aviso-bloco" : ""}` },
    h("div", { class: "cabeca-do-bloco" }, h("h3", {}, "Na fila do PC"), h("span", { class: `chip fila ${prep.fila.situacao}` }, prep.fila.rotulo)),
    prep.fila.detalhe ? h("p", { class: "nota" }, prep.fila.detalhe) : null) : null;
  const publicado = yt && yt.estado === "enviado";
  const reagendar = Boolean(prep.youtube_quando);
  const pronto = s.renderizado && s.situacao === "pronto";
  const ocupado = prep.fila && prep.fila.situacao === "programando";

  let modo = reagendar ? "reagendar" : "programar";
  const modos = h("div", { class: "segmentos" });
  let sugestao = new Date(Date.now() + 60 * 60 * 1000);
  sugestao.setMinutes(sugestao.getMinutes() < 30 ? 30 : 60, 0, 0);
  if (reagendar) sugestao = new Date(`${prep.youtube_quando}:00`);
  const dia = campoDeData(sugestao);
  const hora = h("input", { type: "time", class: "campo", value: `${doisDigitos(sugestao.getHours())}:${doisDigitos(sugestao.getMinutes())}`, step: "300" });
  const quando = h("div", { class: "linha-botoes" }, dia.caixa, hora);
  const botao = h("button", { class: "botao", disabled: ocupado || null });
  const pintarModos = () => {
    if (!reagendar) {
      encher(modos, ...[["programar", "📅 Programar"], ["agora", "Publicar agora"]].map(([k, r]) =>
        h("button", { class: modo === k ? "ativo" : "", onclick: () => { modo = k; pintarModos(); } }, r)));
    }
    quando.hidden = modo === "agora";
    botao.textContent = modo === "reagendar" ? "🕒 Reagendar no YouTube"
      : !pronto ? (modo === "agora" ? "⚙ Renderizar e publicar" : "⚙ Renderizar e programar")
        : modo === "agora" ? "📤 Publicar agora" : "📤 Programar no YouTube";
  };
  pintarModos();
  const vincular = h("input", { type: "checkbox", checked: true, id: "vincular" });
  const relacionado = h("input", { type: "text", class: "campo", value: prep.relacionado });

  botao.addEventListener("click", async () => {
    const corpo = {};
    let resumo;
    if (modo !== "agora") {
      if (!dia.iso()) { avisar("Escreva o dia como dd/mm/aaaa (por exemplo, 05/12/2026)."); return; }
      if (!hora.value) { avisar("Escolha a hora."); return; }
      corpo.quando = `${dia.iso()}T${hora.value}`;
    }
    if (modo === "reagendar") {
      if (corpo.quando === prep.youtube_quando) { avisar("Escolha um horário diferente do que está programado."); return; }
      resumo = `Reagendar no YouTube: de ${dataBr(prep.youtube_quando.slice(0, 10))} às ${prep.youtube_quando.slice(11, 16)} para ${dataBr(dia.iso())} às ${hora.value}?\n\nO PC abre o YouTube Studio e troca o horário (pela fila, quando chegar a vez).`;
      if (!confirm(resumo)) return;
      botao.disabled = true;
      try { await editar(`${base}/reagendar`, corpo); avisar("O reagendamento entrou na fila do PC."); await aoTerminar(); aoFechar(); }
      catch (e) { avisar(e.message); botao.disabled = false; }
      return;
    }
    corpo.relacionado = vincular.checked ? relacionado.value.trim() : "";
    resumo = modo === "agora" ? "Publicar agora no YouTube" : `Programar no YouTube para ${dataBr(dia.iso())} às ${hora.value}`;
    if (corpo.relacionado) resumo += `, vinculado a “${corpo.relacionado}”`;
    if (!pronto) resumo += ".\n\nO Short ainda não está renderizado: o PC o renderiza antes (pela fila) e só depois posta";
    if (!confirm(`${resumo}?`)) return;
    botao.disabled = true;
    try {
      await editar(`${base}/postar`, corpo);
      avisar(pronto ? "Entrou na fila do PC para ir ao YouTube." : "Entrou na fila do PC: renderiza e depois vai ao YouTube.");
      await aoTerminar(); aoFechar();
    } catch (e) { avisar(e.message); botao.disabled = false; }
  });

  const partes = [h("h3", {}, "Postar no YouTube"), estado, naFila];
  if (publicado) {
    partes.push(h("p", { class: "nota" }, "Este Short já saiu no YouTube."));
  } else {
    partes.push(
      h("p", { class: "nota" }, reagendar
        ? "Troque a data ou a hora: o PC abre o YouTube Studio e muda o horário de postagem (só até 5 minutos antes dele sair)."
        : "O PC posta pelo YouTube Studio da igreja (uma janela do Chrome abre lá e faz tudo sozinha). Tudo passa pela fila do PC: primeiro as renderizações, depois o YouTube, um Short por vez."),
      modos, quando);
    if (!reagendar) partes.push(h("label", { class: "opcao", for: "vincular" }, vincular, " Vincular ao vídeo do sermão (vídeo relacionado)"), relacionado);
    partes.push(botao);
    if (ocupado) partes.push(h("p", { class: "nota" }, "Ele está sendo feito no YouTube agora: espere terminar."));
  }
  partes.push(h("button", { class: "botao secundario", onclick: aoFechar }, "Fechar"));
  encher(painel, h("div", { class: "bloco" }, ...partes));
}

async function telaShort(id, corte) {
  await entrarNoProjeto(id);
  const pegar = async () => (await api(`/api/projetos/${enc(id)}/shorts`)).find((x) => String(x.id) === String(corte));
  let s = await pegar();
  if (!s) throw new ErroDaApi(404, "Este Short não existe mais no projeto.");
  const voltarPara = voltarDoShort.startsWith(`#/p/${enc(id)}`) ? voltarDoShort : `#/p/${enc(id)}`;
  const base = `/api/projetos/${enc(id)}/shorts/${s.id}`;
  let modo = s.renderizado ? lerModo() : "ouvir";
  let edicao = ""; // "", "legenda" (corrigir na audição), "texto" (o texto inteiro), "pontas" ou "postar"
  let audicao = null;
  const area = h("div");
  const painel = h("div");
  const seletor = h("div", { class: "segmentos" });
  const ferramentas = h("div", { class: "ferramentas" });
  const info = h("div");

  const pintarCabecalho = () => cabecalho(s.titulo || (s.numero ? `Short ${s.numero}` : "Corte não aprovado"),
    [s.numero ? `Short ${s.numero}` : "Corte não aprovado", duracao(s.duracao)].join(" · "), voltarPara);
  // título, descrição e hashtags: cada caixa com o ✎ Editar (grava no PC ao tocar em Salvar)
  let editandoTexto = ""; // "", "titulo", "descricao" ou "hashtags"
  // `cabeca`: o que vai ao lado do ✎ Editar (o título vai ali mesmo; os outros, o rótulo)
  const caixaDeTexto = (campo, rotulo, valor, mostrar, cabeca = null) => {
    if (editandoTexto !== campo) {
      return h("div", { class: "bloco" },
        h("div", { class: "cabeca-do-bloco" }, cabeca || h("h3", {}, rotulo),
          h("button", { class: "botao-editar", onclick: () => { editandoTexto = campo; pintarInfo(); } }, "✎ Editar")),
        mostrar(valor));
    }
    const multilinha = campo === "descricao";
    const caixa = h(multilinha ? "textarea" : "input", { class: "campo", rows: multilinha ? "6" : null,
      maxlength: campo === "titulo" ? "100" : null,
      placeholder: campo === "hashtags" ? "#shorts #fé (separadas por espaço)" : "" });
    caixa.value = valor;
    const contador = campo === "titulo" ? h("p", { class: "nota" }, `${valor.length}/100`) : null;
    if (contador) caixa.addEventListener("input", () => { contador.textContent = `${caixa.value.length}/100`; });
    const salvar = h("button", { class: "botao", onclick: async () => {
      salvar.disabled = true;
      try {
        s = await editar(`${base}/textos`, { [campo]: caixa.value });
        editandoTexto = "";
        avisar({ titulo: "Título salvo no PC.", descricao: "Descrição salva no PC.", hashtags: "Hashtags salvas no PC." }[campo]);
        pintarCabecalho(); pintarInfo(); pintarFerramentas();
      } catch (e) { avisar(e.message); salvar.disabled = false; }
    } }, "Salvar");
    const cancelar = h("button", { class: "botao secundario", onclick: () => { editandoTexto = ""; pintarInfo(); } }, "Cancelar");
    setTimeout(() => caixa.focus(), 0);
    return h("div", { class: "bloco editando" }, h("h3", {}, rotulo), caixa, contador,
      h("div", { class: "linha-botoes" }, cancelar, salvar));
  };
  // o corte das pausas deste Short (como a caixa do Studio do PC): grava na hora e refaz o som
  let pausaLembrada = s.pausa_s || 1;
  const numeroBr = (v) => String(Math.round(v * 10) / 10).replace(".", ",");
  const blocoDasPausas = () => {
    const ligado = Boolean(s.pausa_s);
    const caixa = h("input", { type: "checkbox", id: "cortar-pausas", checked: ligado || null });
    const campo = h("input", { type: "number", class: "campo", inputmode: "decimal", min: "0.1", max: "10",
      step: "0.1", value: String(ligado ? s.pausa_s : pausaLembrada), "aria-label": "Tamanho da pausa, em segundos" });
    const gravar = async () => {
      const segundos = Number(String(campo.value).replace(",", "."));
      if (!Number.isFinite(segundos) || segundos < 0.1 || segundos > 10) {
        avisar("Escreva um tamanho de 0,1 a 10 segundos."); pintarInfo(); return;
      }
      pausaLembrada = Math.round(segundos * 10) / 10;
      const nova = caixa.checked ? pausaLembrada : 0;
      if (Math.abs(nova - (s.pausa_s || 0)) < 1e-9) return;
      caixa.disabled = true; campo.disabled = true;
      try {
        const novo = await editar(`${base}/pausas`, { pausa_s: nova });
        avisar(nova ? `Pausas de ${numeroBr(nova)} s ou mais cortadas neste Short.` : "Este Short volta a manter as pausas.");
        await depoisDeMudar(novo);
      } catch (e) { avisar(e.message); pintarInfo(); }
    };
    caixa.addEventListener("change", gravar);
    campo.addEventListener("change", gravar);
    const explicacao = ligado
      ? `Pausas de ${numeroBr(s.pausa_s)} s ou mais ficam com ${numeroBr(s.pausa_sobra_s)} s.${modo === "ouvir" ? " O som acima já está assim." : s.renderizado ? " O vídeo pronto só muda quando o PC renderizar de novo." : ""}`
      : "Desligado: este Short mantém todas as pausas da fala.";
    return h("div", { class: "bloco" },
      h("h3", {}, "Corte das pausas"),
      h("div", { class: "linha-campo linha-pausas" },
        h("label", { class: "opcao", for: "cortar-pausas" }, caixa, " Cortar as pausas maiores que"),
        campo, h("span", { class: "unidade" }, "s")),
      h("p", { class: "nota" }, explicacao));
  };
  // 👍 / 👎: só um lembrete de quais Shorts ficaram bons ou ruins (tocar de novo tira)
  const blocoDaOpiniao = () => {
    const botao = (valor, simbolo, rotulo) => h("button", {
      class: `botao-opiniao ${valor} ${s.opiniao === valor ? "ligado" : ""}`, "aria-pressed": String(s.opiniao === valor),
      onclick: async (ev) => {
        ev.currentTarget.disabled = true;
        try { s = await editar(`${base}/opiniao`, { opiniao: s.opiniao === valor ? "" : valor }); }
        catch (e) { avisar(e.message); }
        pintarInfo();
      } }, `${simbolo} ${rotulo}`);
    return h("div", { class: "bloco" }, h("h3", {}, "O que achou deste Short?"),
      h("div", { class: "linha-botoes" }, botao("bom", "👍", "Ficou bom"), botao("ruim", "👎", "Ficou ruim")));
  };
  // ✨ o Gemini revisa este Short: se o corte ficou bom e se o título e a descrição combinam
  let revisandoGemini = false;
  let erroDoGemini = "";
  const blocoDoGemini = () => {
    const r = s.gemini;
    const pedir = h("button", { class: "botao secundario", disabled: revisandoGemini || null, onclick: async () => {
      revisandoGemini = true; erroDoGemini = ""; pintarInfo();
      try {
        s = await editar(`${base}/gemini`, {});
        avisar("O Gemini revisou este Short.");
      } catch (e) { erroDoGemini = e.message; }
      revisandoGemini = false; pintarInfo();
    } }, revisandoGemini ? "O Gemini está lendo…" : r ? "↻ Revisar de novo" : "✨ Revisar com o Gemini");
    const usar = (campo, valor, rotulo) => {
      const emUso = (campo === "titulo" ? s.titulo : s.descricao).trim() === valor.trim();
      return h("div", { class: "sugestao-gemini" },
        h("p", {}, h("strong", {}, `${rotulo}: `), valor),
        h("button", { class: "botao secundario pequeno", disabled: emUso || null, onclick: async (ev) => {
          ev.currentTarget.disabled = true;
          try {
            s = await editar(`${base}/textos`, { [campo]: valor });
            avisar(campo === "titulo" ? "Título trocado pelo do Gemini." : "Descrição trocada pela do Gemini.");
            pintarCabecalho();
          } catch (e) { avisar(e.message); }
          pintarInfo();
        } }, emUso ? "✔ Em uso" : campo === "titulo" ? "Usar este título" : "Usar esta descrição"));
    };
    const marca = (sim, textoSim, textoNao) => (sim ? `✔ ${textoSim}` : `✖ ${textoNao}`);
    const classe = r ? ({ bom: "ok", ajustar: "aviso", ruim: "erro" }[r.veredito] || "") : "";
    return h("div", { class: "bloco" },
      h("div", { class: "cabeca-do-bloco" }, h("h3", {}, "Revisão do Gemini"),
        r ? h("span", { class: `chip ${classe}` }, `✨ ${r.rotulo} · ${r.nota}/10`) : null),
      r ? h("p", { class: "nota" }, `Revisado em ${r.quando_rotulo} (${r.modelo}).`
        + (r.desatualizada ? " O título ou a descrição mudaram depois disso." : "")) : h("p", { class: "nota" }, "Ainda não revisado pelo Gemini."),
      r && r.comentario ? h("p", {}, r.comentario) : null,
      r ? h("p", { class: "nota" }, [marca(r.corte_completo, "ideia completa", "depende do contexto"),
        marca(r.titulo_combina, "título combina", "título não combina"),
        marca(r.descricao_combina, "descrição combina", "descrição não combina")].join(" · ")) : null,
      r && r.titulo_sugerido ? usar("titulo", r.titulo_sugerido, "Título sugerido") : null,
      r && r.descricao_sugerida ? usar("descricao", r.descricao_sugerida, "Descrição sugerida") : null,
      erroDoGemini ? h("p", { class: "nota erro" }, erroDoGemini) : null,
      h("div", { class: "linha-botoes" }, pedir));
  };
  // renderizar só este Short (sem desmarcar os outros) ou refazer o que já está pronto
  const blocoDoRender = () => {
    if (!s.incluir) return h("div", { class: "bloco" }, h("h3", {}, "Renderizar"), h("p", { class: "nota" }, "Aprove este Short para poder renderizá-lo."));
    const refazer = s.renderizado && s.situacao === "pronto";
    const texto = refazer ? "↻ Refazer este Short" : "▶ Renderizar só este Short";
    const explicacao = refazer
      ? "O vídeo pronto é renderizado de novo do zero (os outros Shorts não mudam)."
      : s.renderizado ? "Este Short mudou depois do vídeo pronto: renderiza só ele, com as mudanças."
        : "Renderiza só este Short no PC, sem esperar pelos outros.";
    return h("div", { class: "bloco" }, h("h3", {}, "Renderizar"), h("p", { class: "nota" }, explicacao),
      h("button", { class: "botao", onclick: async (ev) => {
        if (!confirm(refazer ? "Refazer este Short no PC? O vídeo pronto dele é substituído." : "Renderizar só este Short no PC?")) return;
        ev.currentTarget.disabled = true;
        try {
          const r = await post(`${base}/renderizar`, { refazer });
          avisar(r.na_fila ? "O PC está ocupado: este Short entrou na fila para renderizar." : "Renderização deste Short começou no PC. O celular avisa quando terminar.");
          s = await pegar(); pintarInfo();
        } catch (e) { avisar(e.message); ev.currentTarget.disabled = false; }
      } }, texto));
  };
  const blocoDaFila = () => {
    if (!s.fila) return null;
    const classe = s.fila.situacao === "erro" ? "aviso-bloco" : "";
    return h("div", { class: `bloco ${classe}` },
      h("div", { class: "cabeca-do-bloco" }, h("h3", {}, "Fila do PC"), h("span", { class: `chip fila ${s.fila.situacao}` }, s.fila.rotulo)),
      s.fila.detalhe ? h("p", { class: "nota" }, s.fila.detalhe) : null,
      s.fila.pode_tirar ? h("button", { class: "botao secundario", onclick: async (ev) => {
        if (!confirm("Tirar este Short da fila?")) return;
        ev.currentTarget.disabled = true;
        try { await post(`${base}/fila/tirar`, {}); avisar("Tirado da fila."); } catch (e) { avisar(e.message); }
        s = await pegar(); pintarInfo();
      } }, s.fila.situacao === "erro" ? "Dispensar" : "Tirar da fila") : null);
  };
  // enquanto o Short está na fila, a marca acompanha (renderizando → na fila de programação → …)
  let marcaDaFila = JSON.stringify(s.fila);
  repetir(async () => {
    if (!s.fila) return;
    const novo = await pegar();
    if (!novo || JSON.stringify(novo.fila) === marcaDaFila) return;
    marcaDaFila = JSON.stringify(novo.fila);
    const ficouPronto = novo.renderizado !== s.renderizado || novo.situacao !== s.situacao;
    s = novo; pintarCabecalho(); pintarInfo(); pintarFerramentas();
    if (ficouPronto) pintarSeletor();
  }, 4000);
  const pintarInfo = () => encher(info,
    blocoDaFila(),
    blocoDaOpiniao(),
    blocoDoGemini(),
    !s.renderizado ? blocoDoRender() : null,
    blocoDasPausas(),
    caixaDeTexto("titulo", "Título", s.titulo, () => chipsDoShort(s), h("p", { class: "titulo-short" }, s.titulo)),
    caixaDeTexto("descricao", "Descrição", s.descricao, (v) => h("p", { class: v ? "texto-longo" : "nota" }, v || "Sem descrição.")),
    caixaDeTexto("hashtags", "Hashtags", s.hashtags.join(" "), (v) => h("p", { class: v ? "" : "nota" }, v || "Sem hashtags.")));
  const pintarFerramentas = () => {
    ferramentas.hidden = modo !== "ouvir";
    encher(ferramentas, 
      h("button", { class: `botao-ferramenta ${edicao === "legenda" || edicao === "texto" ? "ativo" : ""}`, onclick: () => abrir(edicao ? "" : "legenda") }, edicao === "legenda" || edicao === "texto" ? "✓ Pronto" : "✎ Corrigir a legenda"),
      h("button", { class: `botao-ferramenta ${edicao === "pontas" ? "ativo" : ""}`, onclick: () => abrir(edicao === "pontas" ? "" : "pontas") }, "⇤ Início e fim ⇥"),
      h("button", { class: `botao-ferramenta ${edicao === "fino" ? "ativo" : ""}`, onclick: () => abrir(edicao === "fino" ? "" : "fino") }, "🎯 Ajuste fino"),
      h("button", { class: `botao-ferramenta ${edicao === "postar" ? "ativo" : ""}`, onclick: () => abrir(edicao === "postar" ? "" : "postar") }, "📤 Postar"),
      h("button", { class: `botao-ferramenta ${s.incluir ? "marcado" : ""}`, onclick: async (ev) => {
        ev.currentTarget.disabled = true;
        try { s = await editar(`${base}/marcar`, { incluir: !s.incluir }); avisar(s.incluir ? "Corte aprovado." : "Aprovação retirada."); }
        catch (e) { avisar(e.message); }
        pintarCabecalho(); pintarInfo(); pintarFerramentas();
      } }, s.incluir ? "✓ Aprovado" : "Aprovar"));
  };
  const pintarSeletor = () => {
    seletor.hidden = !s.renderizado;
    encher(seletor, ...[["ouvir", "🎧 Ouvir e editar"], ["video", "▶ Vídeo pronto"]].map(([k, r]) =>
      h("button", { class: modo === k ? "ativo" : "", onclick: async () => {
        if (modo === k) return;
        if (rascunho && rascunho.pendente()) { const salvo = await rascunho.salvar(); if (salvo) s = salvo; }
        rascunho = null;
        modo = k; guardarModo(k); edicao = ""; pintarCabecalho(); pintarInfo(); pintarSeletor(); mostrar();
      } }, r)));
  };

  // depois de uma mudança: o Short novo (duração, legenda) e o áudio refeito
  const depoisDeMudar = async (novo) => {
    s = novo;
    pintarCabecalho(); pintarInfo(); pintarSeletor();
    await mostrar();
  };
  const fecharEditor = () => { abrir(""); };
  let rascunho = null; // o editor de início e fim com mudanças que ainda não foram ao PC
  let ajuste = null; // o ajuste fino aberto (pergunta antes de sair com mudanças)

  async function abrir(qual) {
    if (ajuste && qual !== "fino") { // sair do ajuste fino: pergunta se há o que salvar e para o som
      if (ajuste.pendente() && !confirm("Sair do ajuste fino sem salvar?")) { pintarFerramentas(); return; }
      ajuste.limpar(); ajuste = null;
    }
    // trocar de ferramenta ou de aba salva o rascunho de início, fim e borracha (o áudio é refeito uma vez)
    if (rascunho && rascunho.pendente() && qual !== "pontas") {
      const salvo = await rascunho.salvar();
      rascunho = null;
      if (salvo) { s = salvo; edicao = qual; pintarCabecalho(); pintarInfo(); pintarSeletor(); await mostrar(); return; }
    }
    if (qual !== "pontas") rascunho = null;
    edicao = qual;
    pintarFerramentas();
    if (audicao) {
      audicao.corrigir(qual === "legenda");
      audicao.legenda.hidden = Boolean(qual) && qual !== "legenda";
    }
    try {
      if (qual === "legenda") encher(painel);
      else if (qual === "texto") {
        await editorDaLegenda(painel, base, async (novo) => { edicao = "legenda"; await depoisDeMudar(novo); }, () => abrir("legenda"));
      }
      else if (qual === "pontas") rascunho = await editorDasPontas(painel, base, depoisDeMudar, fecharEditor);
      else if (qual === "fino") {
        if (audicao && audicao.pausar) audicao.pausar();
        ajuste = await editorDoAjusteFino(painel, base, async (novo) => { ajuste = null; edicao = ""; await depoisDeMudar(novo); }, () => { ajuste = null; fecharEditor(); });
      }
      else if (qual === "postar") await editorDaPostagem(painel, base, s, async () => { s = await pegar(); marcaDaFila = JSON.stringify(s.fila); pintarCabecalho(); pintarInfo(); pintarFerramentas(); }, fecharEditor);
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
        blocoDoRender(),
        h("div", { class: "bloco" }, h("h3", {}, s.legenda ? "Legenda (corrigida)" : "Texto do trecho"), h("p", {}, s.legenda || s.texto)));
      return;
    }
    try {
      audicao = await montarAudicao(area, base, {
        aoMudar: async (novo) => { s = novo; pintarCabecalho(); pintarInfo(); },
        aoTextoInteiro: () => abrir("texto"),
      });
      const limparAudicao = audicao.limpar;
      limparTela = () => { // sair do Short: o rascunho de início e fim vai para o PC
        limparAudicao();
        if (ajuste) { ajuste.limpar(); ajuste = null; }
        if (rascunho && rascunho.pendente()) rascunho.salvar();
      };
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

// --- começar pelo celular (Fase R5) ---------------------------------------------------------

async function telaCanal() {
  sairDoProjeto();
  cabecalho("Novo projeto", "Vídeos do canal", "");
  const lista = h("div");
  const topo = h("div");
  const tarefaEl = h("div");
  const carregar = async (atualizar) => {
    encher(lista, h("div", { class: "vazio-lista" }, atualizar
      ? "Lendo o canal no YouTube Studio (uns 15 segundos)…"
      : "Carregando a lista guardada (na primeira vez, o PC lê o canal: uns 15 segundos)…"));
    let r;
    try { r = await api(`/api/canal/videos${atualizar ? "?atualizar=1" : ""}`); }
    catch (e) { avisar(e.message); if (atualizar && ultima) { desenhar(ultima); return; } throw e; }
    ultima = r;
    desenhar(r);
  };
  let ultima = null;
  const desenhar = (r) => {
    encher(topo,
      h("div", { class: "bloco" }, h("h3", {}, "O projeto novo começa com as opções salvas (dá para mudar na tela dele)"),
        h("div", { class: "chips" }, ...r.opcoes.map((o) => h("span", { class: "chip" }, o)))),
      r.ocupado ? h("div", { class: "bloco aviso-bloco" }, r.ocupado) : null,
      h("p", { class: "nota" }, r.lido_em ? `Lista lida do canal em ${dataBr(r.lido_em.slice(0, 10))} às ${r.lido_em.slice(11, 16)}.` : ""),
      h("button", { class: "botao secundario", onclick: () => carregar(true) }, "↻ Atualizar a lista do canal"));
    encher(lista, ...r.videos.map((v) => {
      const chips = [h("span", { class: "chip" }, duracao(v.duracao_s)), h("span", { class: "chip" }, v.visibilidade || v.situacao)];
      if (v.projeto) chips.push(h("span", { class: "chip ok" }, "✔ Já é um projeto"));
      return h("button", { class: "cartao", onclick: () => escolherVideo(v) },
        h("div", { class: "nome" }, v.titulo),
        h("div", { class: "meta" }, dataBr(v.publicado)),
        h("div", { class: "chips" }, ...chips));
    }));
    if (r.tarefa && r.tarefa.rodando) acompanhar();
  };
  const acompanhar = async () => {
    for (;;) {
      let t;
      try { t = await api("/api/canal/tarefa"); } catch (_) { await new Promise((r) => setTimeout(r, 3000)); continue; }
      if (!t) return;
      encher(tarefaEl, h("div", { class: `cartao ${t.erro ? "aviso-bloco" : "destaque"}` },
        h("div", { class: "nome" }, t.titulo), h("p", { class: "nota" }, t.erro ? `Não deu: ${t.erro}` : t.texto)));
      if (!t.rodando) {
        if (!t.erro && t.projeto) { avisar(t.texto); location.hash = `#/p/${enc(t.projeto)}`; }
        return;
      }
      if (location.hash !== "#/canal") return; // saiu da tela: o aviso chega
      await new Promise((r) => setTimeout(r, 2500));
    }
  };
  const escolherVideo = async (v) => {
    if (v.projeto) {
      const ir = await escolher(v.titulo, [["abrir", "Abrir o projeto que já existe"]]);
      if (ir === "abrir") location.hash = `#/p/${enc(v.projeto)}`;
      return;
    }
    const ok = await escolher(v.titulo, [["criar", "✚ Criar o projeto (você ajusta as opções antes de começar)", "botao"]]);
    if (ok !== "criar") return;
    try {
      const r = await post("/api/canal/novo", { id: v.id });
      avisar("Projeto criado. Ajuste as opções e toque em Começar.");
      location.hash = `#/p/${enc(r.projeto)}`;
    } catch (e) { avisar(e.message); }
  };
  encher(tela, tarefaEl, topo, lista);
  try { await carregar(false); } catch (e) { encher(lista, h("div", { class: "vazio-lista" }, e.message)); }
}

// --- cronograma do canal -----------------------------------------------------------------

/** As abas da tela inicial: os projetos do PC e o cronograma do canal. */
function abasDoInicio(ativa) {
  return h("div", { class: "segmentos abas-inicio" },
    h("a", { class: ativa === "projetos" ? "ativo" : "", href: "#/" }, "📁 Projetos"),
    h("a", { class: ativa === "cronograma" ? "ativo" : "", href: "#/cronograma" }, "📅 Cronograma"));
}

const NOMES_DOS_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
  "setembro", "outubro", "novembro", "dezembro"];
const DIAS_DA_SEMANA = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
const diaIso = (d) => `${d.getFullYear()}-${doisDigitos(d.getMonth() + 1)}-${doisDigitos(d.getDate())}`;
const deIso = (iso) => { const [a, m, d] = iso.slice(0, 10).split("-").map(Number); return new Date(a, m - 1, d); };
const diaDaSemana = (iso) => DIAS_DA_SEMANA[deIso(iso).getDay()];

function linhaDoItem(item, aoMesclar = null) {
  const tipo = item.tipo === "short" ? "Short" : "Vídeo";
  const podeMesclar = aoMesclar && item.origem === "spurgeon" && item.corte !== null && item.corte !== undefined;
  return h("div", { class: `item-crono ${item.tipo} ${item.situacao}` },
    h("span", { class: "hora-crono" }, item.quando.slice(11, 16)),
    h("span", { class: `marca-tipo ${item.tipo}` }, tipo),
    h("span", { class: "titulo-crono" }, item.titulo,
      h("span", { class: "nota" }, ` · ${item.situacao === "programado" ? "programado" : "publicado"}${item.duracao_s ? ` · ${duracao(item.duracao_s)}` : ""}`),
      relacionadoDoItem(item),
      item.origem === "spurgeon" ? h("span", { class: "origem-crono" }, "Marcado no Spurgeon (ainda não conferido no YouTube)") : null,
      podeMesclar ? h("button", { class: "botao secundario pequeno mesclar-crono", onclick: () => aoMesclar(item) },
        item.parecidos && item.parecidos.length ? "🔗 Mesclar com o do YouTube…" : "🔗 Mesclar…") : null));
}

/** O sermão do Short (o vídeo relacionado no YouTube), para saber de onde ele é. */
function relacionadoDoItem(item) {
  if (item.tipo !== "short") return null;
  if (!item.relacionado_id) {
    if (item.relacionado) return h("span", { class: "relacionado-crono" }, `🔗 Sermão: ${item.relacionado}`);
    return item.origem === "spurgeon" ? null : h("span", { class: "relacionado-crono sem" }, "Sem vídeo relacionado");
  }
  return h("a", { class: "relacionado-crono", href: `https://www.youtube.com/watch?v=${encodeURIComponent(item.relacionado_id)}`, target: "_blank", rel: "noopener" },
    `🔗 Sermão: ${item.relacionado || "vídeo relacionado (título não encontrado)"}`);
}

function contagem(itens) {
  const shorts = itens.filter((i) => i.tipo === "short").length;
  const videos = itens.length - shorts;
  return [shorts ? plural(shorts, "Short", "Shorts") : "", videos ? plural(videos, "vídeo", "vídeos") : ""].filter(Boolean).join(" e ");
}

/** Um mês em grade (domingo primeiro): cada dia mostra quantos Shorts e vídeos tem. */
function mesDoCronograma(ano, mes, porDia, hoje, aoEscolher, escolhido) {
  const primeiro = new Date(ano, mes, 1);
  const dias = new Date(ano, mes + 1, 0).getDate();
  const celulas = [];
  for (let i = 0; i < primeiro.getDay(); i++) celulas.push(h("div", { class: "dia-crono vazio" }));
  for (let d = 1; d <= dias; d++) {
    const iso = `${ano}-${doisDigitos(mes + 1)}-${doisDigitos(d)}`;
    const itens = porDia.get(iso) || [];
    const marcas = [];
    for (const tipo of ["short", "video"]) {
      const doTipo = itens.filter((i) => i.tipo === tipo);
      if (!doTipo.length) continue;
      const programados = doTipo.some((i) => i.situacao === "programado");
      marcas.push(h("span", { class: `qt ${tipo} ${programados ? "programado" : "publicado"}` }, `${tipo === "short" ? "S" : "V"}${doTipo.length}`));
    }
    const classes = ["dia-crono", iso === hoje ? "hoje" : "", iso < hoje ? "passado" : "", iso === escolhido ? "escolhido" : "", itens.length ? "com-itens" : ""];
    celulas.push(h("button", { class: classes.filter(Boolean).join(" "), disabled: !itens.length,
      "aria-label": `${d} de ${NOMES_DOS_MESES[mes]}: ${itens.length ? contagem(itens) : "nada"}`,
      onclick: () => aoEscolher(iso) },
      h("span", { class: "num-dia" }, String(d)), h("span", { class: "marcas-dia" }, ...marcas)));
  }
  return h("div", { class: "mes-crono" },
    h("h3", {}, `${NOMES_DOS_MESES[mes][0].toUpperCase()}${NOMES_DOS_MESES[mes].slice(1)} de ${ano}`),
    h("div", { class: "grade-crono" }, ...DIAS_DA_SEMANA.map((n) => h("div", { class: "semana-crono" }, n)), ...celulas));
}

async function telaCronograma() {
  sairDoProjeto();
  cabecalho("Studio Spurgeon", "Cronograma do canal");
  const situacao = h("div");
  const corpo = h("div");
  let escolhido = "";
  let dados = null;

  const desenhar = () => {
    const r = dados;
    const lido = r.lido_em ? `Lido do YouTube Studio em ${dataBr(r.lido_em.slice(0, 10))} às ${r.lido_em.slice(11, 16)}.` : "O canal ainda não foi lido.";
    encher(situacao, h("div", { class: "bloco" },
      r.rodando ? h("p", { class: "nota" }, "⏳ Lendo o canal no YouTube Studio (uns 20 segundos)…") : h("p", { class: "nota" }, lido),
      r.erro ? h("p", { class: "nota erro-texto" }, r.erro) : null,
      r.acertados && r.acertados.length ? h("p", { class: "nota" }, `🔄 Horário do YouTube trazido para o Spurgeon: ${r.acertados.join("; ")}.`) : null,
      h("button", { class: "botao secundario", disabled: r.rodando, onclick: async () => {
        try { dados = await post("/api/canal/cronograma/atualizar"); desenhar(); acompanhar(); } catch (e) { avisar(e.message); }
      } }, "↻ Atualizar agora")));
    if (!r.itens.length) {
      encher(corpo, h("div", { class: "vazio-lista" }, r.rodando ? "Lendo o canal…" : "Nada programado nem publicado desde o mês passado."));
      return;
    }
    const porDia = new Map();
    for (const item of r.itens) {
      const dia = item.quando.slice(0, 10);
      if (!porDia.has(dia)) porDia.set(dia, []);
      porDia.get(dia).push(item);
    }
    const futuros = r.itens.filter((i) => i.situacao === "programado");
    const resumo = h("div", { class: "bloco" },
      h("h3", {}, "Programados"),
      h("p", { class: "resumo-crono" }, futuros.length ? contagem(futuros) : "Nada programado no canal."),
      h("div", { class: "legenda-crono" },
        h("span", { class: "qt short programado" }, "S"), " Shorts ",
        h("span", { class: "qt video programado" }, "V"), " vídeos longos ",
        h("span", { class: "nota" }, "· cor forte = programado, clara = já publicado")));
    // do mês passado até o último mês com algo programado (no mínimo, o mês que vem)
    const inicio = deIso(r.desde);
    const hojeData = deIso(r.hoje);
    let fim = new Date(hojeData.getFullYear(), hojeData.getMonth() + 1, 1);
    const ultimo = deIso(r.itens[r.itens.length - 1].quando);
    if (ultimo > fim) fim = new Date(ultimo.getFullYear(), ultimo.getMonth(), 1);
    const meses = [];
    for (let d = new Date(inicio.getFullYear(), inicio.getMonth(), 1); d <= fim; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
      const ano = d.getFullYear(), mes = d.getMonth();
      const grade = mesDoCronograma(ano, mes, porDia, r.hoje, (iso) => { escolhido = escolhido === iso ? "" : iso; desenhar(); }, escolhido);
      const doDia = escolhido && escolhido.startsWith(`${ano}-${doisDigitos(mes + 1)}`) ? porDia.get(escolhido) || [] : [];
      meses.push(h("div", { class: "bloco" }, grade,
        doDia.length ? h("div", { class: "dia-escolhido" },
          h("h3", {}, `${diaDaSemana(escolhido)}, ${dataBr(escolhido)} · ${contagem(doDia)}`), ...doDia.map((i) => linhaDoItem(i, mesclar))) : null));
    }
    const dias = [...new Set(futuros.map((i) => i.quando.slice(0, 10)))];
    const proximos = futuros.length ? h("div", { class: "bloco" }, h("h3", {}, "Próximos agendados"),
      ...dias.map((dia) => h("div", { class: "grupo-crono" },
        h("div", { class: "dia-grupo" }, `${diaDaSemana(dia)}, ${dataBr(dia)}`),
        ...futuros.filter((i) => i.quando.startsWith(dia)).map((i) => linhaDoItem(i, mesclar))))) : null;
    encher(corpo, resumo, ...meses, proximos);
  };

  // o mesmo Short marcado no Spurgeon e postado no YouTube com outro nome: junta os dois (vale o
  // horário do YouTube, e o Short do Spurgeon passa a acompanhar o do canal)
  const mesclar = async (item) => {
    const doCanal = dados.itens.filter((i) => i.tipo === "short" && i.origem !== "spurgeon");
    const porId = new Map(doCanal.map((i) => [i.id, i]));
    const sugeridos = (item.parecidos || []).map((id) => porId.get(id)).filter(Boolean);
    const perto = doCanal.filter((i) => !sugeridos.includes(i))
      .sort((a, b) => Math.abs(deIso(a.quando) - deIso(item.quando)) - Math.abs(deIso(b.quando) - deIso(item.quando))).slice(0, 8);
    const rotulo = (i) => `${dataBr(i.quando.slice(0, 10))} ${i.quando.slice(11, 16)} · ${i.titulo}`;
    const opcoes = [...sugeridos.map((i) => [i.id, `★ ${rotulo(i)}`, "secundario"]), ...perto.map((i) => [i.id, rotulo(i), "secundario"])];
    if (!opcoes.length) { avisar("Nenhum Short do canal na última leitura: atualize o cronograma."); return; }
    const escolhido = await escolher(`Qual Short do YouTube é “${item.titulo}”?`, opcoes);
    if (!escolhido) return;
    const doYoutube = porId.get(escolhido);
    if (!confirm(`Mesclar “${item.titulo}” com “${doYoutube.titulo}”?

O Spurgeon fica com o horário do YouTube (${dataBr(doYoutube.quando.slice(0, 10))} às ${doYoutube.quando.slice(11, 16)}).`)) return;
    try {
      dados = await post("/api/canal/cronograma/mesclar", { projeto: item.projeto, corte: item.corte, canal: escolhido });
      avisar("Mesclado: vale o horário do YouTube.");
      desenhar();
    } catch (e) { avisar(e.message); }
  };

  const acompanhar = async () => {
    while (dados && dados.rodando) {
      await new Promise((r) => setTimeout(r, 3000));
      if (location.hash !== "#/cronograma") return; // saiu da tela
      try { dados = await api("/api/canal/cronograma"); } catch (_) { continue; }
      desenhar();
    }
  };

  encher(tela, abasDoInicio("cronograma"), situacao, corpo);
  dados = await api("/api/canal/cronograma?auto=1");
  desenhar();
  acompanhar();
}

// --- roteador -------------------------------------------------------------------------------

// A última tela aberta (projeto, Short, aprovação, Shorts livre) fica guardada no celular: abrir o
// app de novo volta a ela. Um aviso com link abre a tela dele (o endereço já traz a tela).
const CHAVE_DA_ULTIMA_TELA = "ultimaTela";
let restaurando = false;

function guardarUltimaTela() {
  try { localStorage.setItem(CHAVE_DA_ULTIMA_TELA, location.hash || "#/"); } catch (_) { /* sem armazenamento */ }
}

function restaurarUltimaTela() {
  if (location.hash && location.hash !== "#" && location.hash !== "#/") return;
  let ultima = "";
  try { ultima = localStorage.getItem(CHAVE_DA_ULTIMA_TELA) || ""; } catch (_) { return; }
  if (ultima.startsWith("#/p/") || ultima === "#/canal" || ultima === "#/cronograma") {
    restaurando = true;
    history.replaceState(null, "", ultima); // sem disparar o hashchange: o rotear() de início já lê o endereço
  }
}

async function rotear() {
  pararRepeticao();
  if (limparTela) { limparTela(); limparTela = null; }
  window.scrollTo(0, 0);
  const partes = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const eraRestauracao = restaurando;
  restaurando = false;
  rotaAnterior = rotaAtual;
  rotaAtual = location.hash;
  guardarUltimaTela();
  try {
    if (partes[0] === "canal") await telaCanal();
    else if (partes[0] === "cronograma") await telaCronograma();
    else if (partes[0] === "p" && partes[1] && partes[2] === "s" && partes[3]) await telaShort(partes[1], partes[3]);
    else if (partes[0] === "p" && partes[1] && partes[2] === "aprovar") await telaAprovar(partes[1]);
    else if (partes[0] === "p" && partes[1] && partes[2] === "livre") await telaLivre(partes[1]);
    else if (partes[0] === "p" && partes[1]) await telaProjeto(partes[1]);
    else await telaProjetos();
  } catch (e) {
    if (eraRestauracao && e.status === 404) { // o projeto ou o Short lembrado não existe mais
      try { localStorage.removeItem(CHAVE_DA_ULTIMA_TELA); } catch (_) { /* tudo bem */ }
      location.hash = "";
      return;
    }
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
acao("reiniciar", async () => {
  if (!confirm("Reiniciar o servidor no PC? Leva alguns segundos; o app reconecta sozinho.")) return;
  const voltar = location.hash;
  const antes = sistema && sistema.versao;
  await post("/api/sistema/reiniciar"); // recusado (com o motivo) no meio de um trabalho
  fecharMenu();
  pararRepeticao();
  marcarPonto("fora");
  encher(tela, h("div", { class: "vazio-lista" }, "Reiniciando o servidor no PC…"));
  await new Promise((r) => setTimeout(r, 3000)); // o servidor velho ainda responde por um instante
  for (let i = 0; i < 40; i++) {
    try {
      const s = await api("/api/sistema");
      marcarPonto(s.dormindo ? "dormindo" : "ok");
      avisar(antes && s.versao !== antes ? `Servidor reiniciado (versão ${s.versao}).` : "Servidor reiniciado.");
      if (location.hash === voltar) rotear(); else location.hash = voltar;
      return;
    } catch (_) { await new Promise((r) => setTimeout(r, 2000)); }
  }
  encher(tela, h("div", { class: "vazio-lista" }, "O servidor não voltou. No PC, use o atalho “Controle remoto - ligar”."));
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
restaurarUltimaTela();
rotear();
