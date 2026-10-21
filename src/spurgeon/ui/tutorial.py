"""O passo a passo do programa: uma janelinha que explica cada etapa, na ordem de uso.

`Tutorial` mostra os `PASSOS` numa janela que não bloqueia a principal e fica à frente dela, no
canto onde não cobre o que está sendo explicado. A cada passo ele pede à janela principal, pelo
callback `ir_para`, para mostrar a página e a aba certas e devolver o widget a explicar; um
`Destaque` (um contorno colorido que pisca duas vezes e depois fica fixo) o marca na tela. Nada do
que está na tela é alterado: o contorno são quatro tiras por cima, que somem ao fechar.

`OfertaDoTutorial` é a janelinha da primeira abertura ("Quer ver o passo a passo?").
"""

from __future__ import annotations

import contextlib
import logging
import tkinter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import customtkinter as ctk

from spurgeon.ui import dicas

#: onde o passo acontece: a aba Processamento, o Studio Shorts (lista de cortes) ou o Shorts livre
VISTA_PROCESSAMENTO = "processamento"
VISTA_STUDIO = "studio"
VISTA_LIVRE = "livre"

COR_DO_DESTAQUE = "#ffb020"
_LARGURA_DA_JANELA = 400
_LARGURA_DO_TEXTO = 350
_INTERVALO_DO_DESTAQUE_MS = 90
_CICLOS_PISCANDO = 8  # 2 piscadas de ~0,36 s cada; depois o contorno fica fixo
_COR_SUAVE = "gray60"
_COR_NOTA = "#e0a030"


@dataclass(frozen=True)
class Passo:
    """Um passo do tutorial. `alvo` é a chave que a janela principal sabe achar na tela."""

    titulo: str
    texto: str
    alvo: str = ""
    pagina: int | None = None  # página da coluna da esquerda: 0, 1 ou 2 (só na vista processamento)
    vista: str = VISTA_PROCESSAMENTO


@dataclass
class Destino:
    """O que a janela principal devolve ao preparar um passo: o que destacar e um aviso extra.

    `alvo` pode ser um widget ou vários (o contorno envolve todos); `nota` aparece em amarelo
    abaixo do texto (por exemplo, "esta tela só aparece quando há Shorts calculados").
    """

    alvo: object = None
    nota: str = ""


PASSOS: tuple[Passo, ...] = (
    Passo(
        "Bem-vindo ao Studio Spurgeon",
        "Este programa transforma o vídeo de um sermão em um vídeo limpo, sem as pausas longas, "
        "com legendas, e em Shorts verticais. Tudo acontece neste computador, sem enviar nada "
        "para a internet. Vamos passar pelas etapas na ordem em que você as usa. Para sair, "
        "clique em Pular; para rever depois, use o botão “Como usar”.",
        alvo="como_usar",
        pagina=0,
    ),
    Passo(
        "1. Escolha o vídeo",
        "Clique em “Escolher vídeo…” e selecione o arquivo da gravação (.mov, .mp4, .mkv…). O "
        "programa lê o vídeo e mostra a duração e uma estimativa do tempo de processamento. Se o "
        "sermão já foi processado antes, use “Abrir projeto…” para continuar de onde parou.",
        alvo="video_arquivo",
        pagina=0,
    ),
    Passo(
        "Nome, data e pasta do projeto",
        "O nome do sermão e a data da gravação formam o nome da pasta do projeto. Eles já vêm "
        "preenchidos a partir do arquivo, mas você pode corrigir. Em “Pasta de projetos” ficam "
        "todos os resultados; use “Alterar…” para guardá-los em outro disco.",
        alvo="video_dados",
        pagina=0,
    ),
    Passo(
        "2. O que gerar: o modo",
        "“Tudo” processa um sermão novo do começo ao fim. “Só Shorts” refaz apenas os Shorts de "
        "um projeto que já foi transcrito. “Só vinhetas” junta a introdução e a vinheta final a um "
        "vídeo que já está pronto. No primeiro sermão, deixe em “Tudo”.",
        alvo="modo",
        pagina=0,
    ),
    Passo(
        "Áudio",
        "“Limpar áudio” tira chiado e ruído de fundo com IA: melhora o som e a transcrição, mas "
        "demora bastante; vale a pena se a gravação tem ruído. “Normalizar volume” deixa o volume "
        "num padrão que toca bem no YouTube e no celular, e costuma valer sempre.",
        alvo="audio",
        pagina=0,
    ),
    Passo(
        "Vídeo completo sem silêncios",
        "Gera o sermão inteiro sem as pausas longas, junto com o arquivo de legendas (.srt) para o "
        "YouTube. O campo de tamanho mínimo diz a partir de quantos segundos de silêncio o corte "
        "acontece: quanto menor o número, mais corrido fica o vídeo.",
        alvo="longo",
        pagina=0,
    ),
    Passo(
        "Introdução e vinheta final",
        "Emenda um vídeo de abertura antes do sermão e um de encerramento depois. Os arquivos "
        "padrão são introducao.mp4 e vinheta_final.mp4, na pasta “recursos”; você também pode "
        "escolher outros. O fade suaviza a transição: “Mesclar” dissolve um vídeo no outro e “Pelo "
        "preto” escurece e volta.",
        alvo="vinhetas",
        pagina=0,
    ),
    Passo(
        "Shorts e legendas dinâmicas",
        "“Gerar Shorts” faz a IA ler o sermão e escolher os melhores trechos, com título, "
        "descrição e hashtags; você revisa tudo antes de renderizar. “Legendas dinâmicas” grava a "
        "legenda dentro do vídeo do Short, com a palavra falada em destaque.",
        alvo="shorts",
        pagina=0,
    ),
    Passo(
        "3. Cortar o começo e o fim",
        "Se a gravação tem um começo ou um fim que não são do sermão (teste de som, conversa, "
        "despedida), digite o instante em “Começar em” e “Terminar em”, no formato min:s (por "
        "exemplo 0:07). Os botões com ▶ mostram 15 segundos de cada ponta para conferir. O que "
        "ficar fora é ignorado: no vídeo, nas legendas e nos Shorts.",
        alvo="aparar",
        pagina=1,
    ),
    Passo(
        "Zoom do vídeo",
        "Aproxima a câmera quando o pregador aparece pequeno. Arraste o zoom e clique no quadro "
        "para centrar a imagem no pregador: a área escura será cortada e o resto, ampliado. Guarde "
        "o ajuste como preset para usar nos próximos sermões da mesma câmera.",
        alvo="zoom",
        pagina=1,
    ),
    Passo(
        "Zoom e legenda dos Shorts",
        "Aqui você vê como o Short vertical (9:16) vai ficar. O zoom do Short aproxima só os "
        "Shorts. Para a altura da legenda, clique no quadro ou arraste o controle; deixe os "
        "elementos do YouTube ligados para conferir que a legenda não fica atrás dos botões e do "
        "título.",
        alvo="shorts_zoom",
        pagina=1,
    ),
    Passo(
        "4. Tratamento de imagem",
        "Corrige brilho, contraste, cor, brancos e sombras. Comece pela “Sugestão automática” e "
        "arraste a linha branca sobre o quadro para comparar o antes e o depois. Se a imagem já "
        "está boa, deixe desligado.",
        alvo="tratamento",
        pagina=1,
    ),
    Passo(
        "5. Marca d'água",
        "Coloca o logotipo da igreja num canto do vídeo. Escolha a imagem (um PNG com fundo "
        "transparente é o ideal), o canto, o tamanho e se a marca vai no vídeo longo, nos Shorts "
        "ou nos dois. A prévia ao lado mostra o resultado.",
        alvo="marca",
        pagina=1,
    ),
    Passo(
        "6. Pontos de revisão",
        "Aqui você escolhe onde o processamento para para você conferir o trabalho: depois da "
        "transcrição, para corrigir o texto, e antes de renderizar os Shorts, para escolher os "
        "cortes. Para começar, deixe os dois ligados.",
        alvo="revisao",
        pagina=2,
    ),
    Passo(
        "7. Processamento",
        "“Uso do processador” define quanto do computador o programa usa: 75% deixa o resto "
        "utilizável. O modelo do Whisper é o que transcreve a fala; o recomendado para este "
        "computador já vem marcado. A qualidade do vídeo final troca imagem por tempo: “Máxima” é "
        "a melhor e a mais lenta. Em “Opções avançadas” ficam ajustes finos que quase nunca "
        "precisam mudar.",
        alvo="processamento",
        pagina=2,
    ),
    Passo(
        "Iniciar o processamento",
        "Com tudo pronto, clique em “Iniciar processamento”. O programa confere o ambiente e "
        "começa; abaixo aparecem a barra de andamento, a etapa atual e o tempo restante. Um sermão "
        "longo pode levar horas: você pode deixar o programa trabalhando.",
        alvo="iniciar",
        pagina=2,
    ),
    Passo(
        "Pausar e cancelar",
        "“Pausar” suspende o processamento no próximo ponto seguro, e o mesmo botão passa a "
        "“Retomar”. “Cancelar” para tudo: o projeto fica salvo e, ao iniciar de novo com o mesmo "
        "vídeo, o trabalho continua de onde parou, sem refazer o que já está pronto.",
        alvo="pausar_cancelar",
        pagina=2,
    ),
    Passo(
        "Revisão da transcrição",
        "Se o ponto de revisão está ligado, quando o texto do sermão fica pronto aparece neste "
        "lado um aviso amarelo. Clique em “Abrir revisão”, leia o texto e corrija o que estiver "
        "errado (nomes, versículos, termos da igreja). Depois clique em “Aprovar e continuar”: as "
        "correções valem para as legendas e para os Shorts.",
        alvo="execucao",
        pagina=2,
    ),
    Passo(
        "A aba Studio Shorts",
        "Quando os cortes dos Shorts estão calculados, a aba “Studio Shorts” é liberada (depois de "
        "processar, ou ao abrir um projeto que já os tenha). É nela que você revisa os Shorts "
        "sugeridos, corrige a legenda, ajusta o começo e o fim e manda renderizar.",
        alvo="aba_studio",
        vista=VISTA_STUDIO,
    ),
    Passo(
        "Lista de cortes: notas e categorias",
        "À esquerda ficam os Shorts sugeridos. Marque os que quer renderizar. Cada um tem uma "
        "nota de 0 a 100 (o que a IA achou somado ao que se mede no texto) e uma categoria, o "
        "tipo de trecho, indicada por uma cor. Ordene por nota ou pelo horário no sermão e "
        "clique num corte para ver os detalhes.",
        alvo="studio_lista",
        vista=VISTA_STUDIO,
    ),
    Passo(
        "Tabela de frases e ferramentas",
        "A tabela mostra a fala do corte frase por frase, com a hora ao lado; o que está em "
        "cinza fica de fora do Short. Três botões escolhem o que o mouse faz: a seta seleciona e "
        "corrige o texto (duplo clique no cinza leva o começo ou o fim até ali); o marca-texto "
        "pinta, palavra por palavra, o que entra no Short, mesmo longe dele; a borracha apaga, "
        "palavra por palavra, o que sai: no meio do corte o Short pula essa parte e continua "
        "depois, ótimo para cortar um erro de pronúncia. As palavras tiradas ficam riscadas. "
        "Os botões de palavra e de frase, em cima e embaixo, mexem no começo e no fim.",
        alvo="studio_tabela",
        vista=VISTA_STUDIO,
    ),
    Passo(
        "Desfazer, refazer e português",
        "“Desfazer” e “Refazer” (ou Ctrl+Z e Ctrl+Y) voltam e repetem as mudanças que você fez "
        "no corte aberto: começo, fim, palavras tiradas, pausas e legenda. "
        "“Verificar português” marca as palavras que o dicionário não conhece; nomes e termos "
        "próprios da sua igreja podem ser guardados como certos. O botão “Dicionário…” abre "
        "a lista desses termos, para você acrescentar outros ou tirar um que guardou errado.",
        alvo="studio_edicao",
        vista=VISTA_STUDIO,
    ),
    Passo(
        "Título, descrição e hashtags",
        "A IA já escreve o título, a descrição e as hashtags de cada Short, mas você pode "
        "mudá-los. O botão “Refazer com IA”, ao lado de cada campo, pede uma nova sugestão só "
        "daquele campo, a partir do texto atual do corte.",
        alvo="studio_texto",
        vista=VISTA_STUDIO,
    ),
    Passo(
        "Prévia do Short",
        "“Gerar prévia” cria uma miniatura de baixa qualidade só para ver como o Short vai ficar: "
        "recorte, zoom, cor e legenda. Use “Subir” e “Descer” para acertar a altura da legenda "
        "olhando a prévia. “Cortar as pausas deste Short” encurta as pausas maiores que o tempo "
        "escolhido (a partir de 0,1 s) só neste Short, e a prévia já mostra o resultado. O som é o "
        "do vídeo original, e a marca d'água não aparece na prévia.",
        alvo="studio_previa",
        vista=VISTA_STUDIO,
    ),
    Passo(
        "Salvar, descartar e renderizar",
        "“Salvar alterações” guarda o que você corrigiu sem renderizar, e “Descartar alterações” "
        "joga fora o que mudou desde a última vez que salvou (ele pergunta antes). “Renderizar” "
        "cria os arquivos dos Shorts marcados; os que já estão prontos e não mudaram não são "
        "refeitos, e os desmarcados vão para a pasta “desmarcados” (nada é apagado). O tempo "
        "estimado aparece antes de começar.",
        alvo="studio_rodape",
        vista=VISTA_STUDIO,
    ),
    Passo(
        "Shorts livre",
        "Se a IA não sugeriu o trecho que você quer, abra “Shorts livre”. Busque por palavras "
        "parecidas ou por tema e escolha o começo e o fim: clique numa palavra, ou arraste sobre o "
        "texto, e afine com os botões. Com o marca-texto você pinta só as frases que quer (a 1 e a "
        "3, sem a 2) e a borracha tira o que sobrar. As barras coloridas na margem mostram o que a "
        "IA detectou (a legenda no alto leva ao próximo trecho de cada categoria) e o losango, o "
        "que já virou Short. Em “Criar Short deste trecho” a IA dá a nota e escreve o título, e o "
        "corte entra na lista como gerado manualmente.",
        alvo="studio_livre",
        vista=VISTA_LIVRE,
    ),
    Passo(
        "Onde ficam os arquivos prontos",
        "Cada sermão tem uma pasta dentro da pasta de projetos, com a data e o nome. Nela ficam o "
        "vídeo sem silêncios (video_longo_otimizado.mp4), as legendas (legendas_video_longo.srt), "
        "a pasta “shorts_gerados” com os Shorts e o processamento.log, que conta o que aconteceu. "
        "O botão “Abrir pasta do projeto” leva direto até ela. Bom trabalho!",
        alvo="pasta",
        pagina=2,
    ),
)


# --- o contorno ---------------------------------------------------------------------------------
class Destaque:
    """Um contorno colorido em volta de um ou mais widgets, que pisca duas vezes e fica fixo.

    São quatro tiras (`tkinter.Frame`) postas por cima da janela, em volta do retângulo do alvo.
    Um relógio curto as reposiciona, então o contorno acompanha a rolagem, o redimensionamento e as
    seções que abrem. O que está fora da área rolável não é contornado, e o lado cortado pela borda
    da área não ganha tira. Nenhum widget do programa é alterado: limpar o destaque é só apagar as
    tiras.
    """

    def __init__(
        self,
        janela,
        *,
        cor: str = COR_DO_DESTAQUE,
        espessura: int = 3,
        margem: int = 3,
        intervalo_ms: int = _INTERVALO_DO_DESTAQUE_MS,
    ):
        self._janela = janela
        self._cor = cor
        self._espessura = espessura
        self._margem = margem
        self._intervalo_ms = intervalo_ms
        self._alvos: list = []
        self._tiras: list[tkinter.Frame] = []
        self._relogio: str | None = None
        self._ciclos = 0
        self._ultimo: tuple | None = None

    @property
    def ativo(self) -> bool:
        return bool(self._tiras)

    @property
    def tiras(self) -> list[tkinter.Frame]:
        return list(self._tiras)

    @property
    def fixo(self) -> bool:
        """Já terminou de piscar (o contorno fica aceso até o destaque ser limpo)."""
        return self._ciclos >= _CICLOS_PISCANDO

    def mostrar(self, alvo, *, piscar: bool = True) -> None:
        """Contorna `alvo` (um widget ou uma lista deles). `None` só limpa o destaque."""
        self.limpar()
        alvos = list(alvo) if isinstance(alvo, (list, tuple)) else [alvo]
        alvos = [a for a in alvos if a is not None]
        if not alvos:
            return
        self._alvos = alvos
        self._ciclos = 0 if piscar else _CICLOS_PISCANDO
        try:
            self._tiras = [
                tkinter.Frame(self._janela, background=self._cor, bd=0, highlightthickness=0)
                for _ in range(4)
            ]
        except tkinter.TclError:
            self._tiras = []
            return
        self.atualizar()
        self._agendar()

    def limpar(self) -> None:
        """Apaga o contorno (os widgets explicados não foram tocados, não há nada a restaurar)."""
        if self._relogio is not None:
            with contextlib.suppress(tkinter.TclError):
                self._janela.after_cancel(self._relogio)
            self._relogio = None
        for tira in self._tiras:
            with contextlib.suppress(tkinter.TclError):
                tira.destroy()
        self._tiras = []
        self._alvos = []
        self._ultimo = None

    def _agendar(self) -> None:
        with contextlib.suppress(tkinter.TclError):
            self._relogio = self._janela.after(self._intervalo_ms, self._passo)

    def _passo(self) -> None:
        self._relogio = None
        if not self._tiras:
            return
        self._ciclos += 1
        self.atualizar()
        self._agendar()

    # --- geometria ----------------------------------------------------------------------------
    def retangulo(self) -> tuple[int, int, int, int] | None:
        """O retângulo do alvo na tela (esquerda, topo, direita, base); None se não está à vista."""
        achado = self._retangulo_e_cortes()
        return None if achado is None else achado[0]

    def _retangulo_e_cortes(self):
        caixas = []
        for alvo in self._alvos:
            medida = _medir(alvo)
            if medida is not None:
                caixas.append(medida)
        if not caixas:
            return None
        esq = min(c[0][0] for c in caixas)
        topo = min(c[0][1] for c in caixas)
        dir_ = max(c[0][2] for c in caixas)
        base = max(c[0][3] for c in caixas)
        cortados = tuple(any(c[1][lado] for c in caixas) for lado in range(4))
        return (esq, topo, dir_, base), cortados

    def atualizar(self) -> None:
        """Reposiciona as tiras sobre o alvo (e as esconde se ele sumiu ou na fase apagada)."""
        if not self._tiras:
            return
        try:
            achado = self._retangulo_e_cortes()
            aceso = self.fixo or (self._ciclos // 2) % 2 == 0
            if achado is None or not aceso:
                chave: tuple = ()
            else:
                (esq, topo, dir_, base), cortados = achado
                chave = (
                    esq - self._janela.winfo_rootx(),
                    topo - self._janela.winfo_rooty(),
                    dir_ - esq,
                    base - topo,
                    cortados,
                )
            if chave == self._ultimo:
                return
            self._ultimo = chave
            if not chave:
                for tira in self._tiras:
                    tira.place_forget()
                return
            self._posicionar(*chave)
        except tkinter.TclError:
            pass  # a janela está fechando

    def _posicionar(self, x: int, y: int, largura: int, altura: int, cortados: tuple) -> None:
        m, e = self._margem, self._espessura
        x0, y0 = x - m, y - m
        w, h = largura + 2 * m, altura + 2 * m
        cima, baixo, esquerda, direita = self._tiras
        lados = (
            (cima, cortados[1], dict(x=x0, y=y0, width=w, height=e)),
            (baixo, cortados[3], dict(x=x0, y=y0 + h - e, width=w, height=e)),
            (esquerda, cortados[0], dict(x=x0, y=y0, width=e, height=h)),
            (direita, cortados[2], dict(x=x0 + w - e, y=y0, width=e, height=h)),
        )
        for tira, cortado, onde in lados:
            if cortado:
                tira.place_forget()
            else:
                tira.place(**onde)
                tira.lift()


def _visivel_de(widget):
    """O widget que de fato aparece na tela (num quadro rolável do CustomTkinter, o externo)."""
    externo = getattr(widget, "_parent_frame", None)
    return widget if externo is None else externo


def _medir(alvo):
    """((esq, topo, dir, base), cortados) do widget na tela, ou None se não está à vista.

    `cortados` diz, para (esquerda, topo, direita, base), se a borda foi cortada pela área rolável
    em que o widget está (a parte rolada para fora não aparece).
    """
    widget = _visivel_de(alvo)
    try:
        if not widget.winfo_exists() or not widget.winfo_viewable():
            return None
        esq, topo = widget.winfo_rootx(), widget.winfo_rooty()
        dir_, base = esq + widget.winfo_width(), topo + widget.winfo_height()
        cortados = [False, False, False, False]
        pai = getattr(widget, "master", None)
        while pai is not None:
            tela = getattr(pai, "_parent_canvas", None)
            if tela is not None:
                ce, ct = tela.winfo_rootx(), tela.winfo_rooty()
                cd, cb = ce + tela.winfo_width(), ct + tela.winfo_height()
                for lado, (valor, limite, maior) in enumerate(
                    ((esq, ce, False), (topo, ct, False), (dir_, cd, True), (base, cb, True))
                ):
                    if (valor > limite) if maior else (valor < limite):
                        cortados[lado] = True
                esq, topo, dir_, base = max(esq, ce), max(topo, ct), min(dir_, cd), min(base, cb)
            pai = getattr(pai, "master", None)
        if dir_ <= esq or base <= topo:
            return None  # está inteiro fora da área visível
        return (esq, topo, dir_, base), tuple(cortados)
    except tkinter.TclError:
        return None


# --- a janela do passo a passo -----------------------------------------------------------------
class Tutorial:
    """A janelinha do passo a passo. Não bloqueia a janela principal e fica à frente dela."""

    def __init__(
        self,
        janela,
        passos: Sequence[Passo] = PASSOS,
        *,
        ir_para: Callable[[Passo], Destino | object | None],
        ao_fechar: Callable[[bool], None] | None = None,
    ):
        """`ir_para(passo)` mostra a página e a aba do passo e devolve o que destacar (um
        `Destino`, um widget, uma lista de widgets ou None). `ao_fechar(concluido)` roda quando o
        tutorial termina (True, no último passo) ou é pulado (False)."""
        self._janela = janela
        self.passos = tuple(passos)
        self._ir_para = ir_para
        self._ao_fechar = ao_fechar
        self.destaque = Destaque(janela)
        self.indice = -1
        self._tela: ctk.CTkToplevel | None = None
        self._fechando = False

    # --- estado -------------------------------------------------------------------------------
    @property
    def aberto(self) -> bool:
        return self._tela is not None

    @property
    def passo(self) -> Passo | None:
        return self.passos[self.indice] if 0 <= self.indice < len(self.passos) else None

    @property
    def tela(self) -> ctk.CTkToplevel | None:
        return self._tela

    # --- navegação ----------------------------------------------------------------------------
    def iniciar(self, indice: int = 0) -> None:
        """Abre a janela no passo `indice` (se já está aberta, só a traz para a frente)."""
        if not self.passos:
            return
        if self._tela is not None:
            self._tela.lift()
            return
        self._fechando = False
        self._montar()
        self.ir_ao_passo(indice)

    def ir_ao_passo(self, indice: int) -> None:
        if self._tela is None:
            return
        self.indice = min(max(indice, 0), len(self.passos) - 1)
        passo = self.passos[self.indice]
        try:
            devolvido = self._ir_para(passo)
        except Exception:  # um passo que não acha o alvo não pode travar o passo a passo
            logging.getLogger(__name__).exception("Falha ao preparar o passo %s", passo.titulo)
            devolvido = None
        destino = devolvido if isinstance(devolvido, Destino) else Destino(alvo=devolvido)
        self._mostrar_texto(passo, destino.nota)
        self.destaque.mostrar(destino.alvo)
        self._posicionar()

    def proximo(self) -> None:
        if self._tela is None:
            return
        if self.indice >= len(self.passos) - 1:
            self.concluir()
        else:
            self.ir_ao_passo(self.indice + 1)

    def anterior(self) -> None:
        if self._tela is not None and self.indice > 0:
            self.ir_ao_passo(self.indice - 1)

    def pular(self) -> None:
        self._fechar(concluido=False)

    def concluir(self) -> None:
        self._fechar(concluido=True)

    def fechar(self) -> None:
        """Fecha sem avisar `ao_fechar` (a janela principal está sendo destruída, por exemplo)."""
        self._fechar(concluido=False, avisar=False)

    def _fechar(self, *, concluido: bool, avisar: bool = True) -> None:
        if self._tela is None or self._fechando:
            return
        self._fechando = True
        self.destaque.limpar()
        tela, self._tela = self._tela, None
        with contextlib.suppress(tkinter.TclError):
            tela.destroy()
        self.indice = -1
        if avisar and self._ao_fechar is not None:
            self._ao_fechar(concluido)

    # --- a janela -----------------------------------------------------------------------------
    def _montar(self) -> None:
        tela = ctk.CTkToplevel(self._janela)
        tela.title("Passo a passo")
        tela.resizable(False, False)
        with contextlib.suppress(tkinter.TclError):
            tela.transient(self._janela)  # fica sempre à frente da janela principal
        tela.protocol("WM_DELETE_WINDOW", self.pular)
        tela.columnconfigure(0, weight=1)
        self._tela = tela

        topo = ctk.CTkFrame(tela, fg_color="transparent")
        topo.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        topo.columnconfigure(0, weight=1)
        self._rotulo_numero = ctk.CTkLabel(
            topo, text="", anchor="w", text_color=_COR_SUAVE, font=ctk.CTkFont(size=12)
        )
        self._rotulo_numero.grid(row=0, column=0, sticky="w")
        self.botao_pular = ctk.CTkButton(
            topo, text="Pular", width=64, height=24, fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"), command=self.pular,
        )  # fmt: skip
        self.botao_pular.grid(row=0, column=1, sticky="e")
        dicas.dica(
            self.botao_pular,
            "Fecha o passo a passo. Para vê-lo de novo, use o botão “Como usar”, no canto "
            "inferior direito da janela principal.",
        )
        self._barra = ctk.CTkProgressBar(tela, height=5)
        self._barra.grid(row=1, column=0, sticky="ew", padx=16, pady=(8, 0))

        # a largura fixa evita que a janelinha encolha e estique de um passo para outro
        bloco = {
            "text": "",
            "anchor": "w",
            "justify": "left",
            "width": _LARGURA_DO_TEXTO,
            "wraplength": _LARGURA_DO_TEXTO,
        }
        self._rotulo_titulo = ctk.CTkLabel(tela, **bloco, font=ctk.CTkFont(size=16, weight="bold"))
        self._rotulo_titulo.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 4))
        self._rotulo_texto = ctk.CTkLabel(tela, **bloco)
        self._rotulo_texto.grid(row=3, column=0, sticky="ew", padx=16)
        self._rotulo_nota = ctk.CTkLabel(
            tela, **bloco, text_color=_COR_NOTA, font=ctk.CTkFont(size=12)
        )
        self._rotulo_nota.grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 0))

        botoes = ctk.CTkFrame(tela, fg_color="transparent")
        botoes.grid(row=5, column=0, sticky="ew", padx=16, pady=(12, 14))
        botoes.columnconfigure(0, weight=1)
        self.botao_anterior = ctk.CTkButton(
            botoes, text="◀ Anterior", width=110, fg_color=("gray70", "gray30"),
            command=self.anterior,
        )  # fmt: skip
        self.botao_anterior.grid(row=0, column=0, sticky="w")
        self.botao_proximo = ctk.CTkButton(
            botoes, text="Próximo ▶", width=110, command=self.proximo
        )
        self.botao_proximo.grid(row=0, column=1, sticky="e")
        dicas.dica(self.botao_anterior, "Volta ao passo anterior (tecla seta para a esquerda).")
        dicas.dica(
            self.botao_proximo,
            lambda: (
                "Termina o passo a passo."
                if self.indice >= len(self.passos) - 1
                else "Vai ao próximo passo (tecla seta para a direita)."
            ),
        )
        for tecla, comando in (
            ("<Right>", self.proximo),
            ("<Left>", self.anterior),
            ("<Escape>", self.pular),
        ):
            tela.bind(tecla, lambda _e, c=comando: c())

    def _mostrar_texto(self, passo: Passo, nota: str) -> None:
        total = len(self.passos)
        self._rotulo_numero.configure(text=f"Passo {self.indice + 1} de {total}")
        self._barra.set((self.indice + 1) / total)
        self._rotulo_titulo.configure(text=passo.titulo)
        self._rotulo_texto.configure(text=passo.texto)
        self._rotulo_nota.configure(text=nota)
        if nota:
            self._rotulo_nota.grid()
        else:
            self._rotulo_nota.grid_remove()
        ultimo = self.indice >= total - 1
        self.botao_proximo.configure(text="Concluir" if ultimo else "Próximo ▶")
        self.botao_anterior.configure(state="normal" if self.indice > 0 else "disabled")

    def _posicionar(self) -> None:
        """Põe a janelinha no canto da janela principal onde ela cobre menos o que é explicado."""
        tela = self._tela
        if tela is None:
            return
        try:
            tela.update_idletasks()
            self._janela.update_idletasks()
            janela = (
                self._janela.winfo_rootx(),
                self._janela.winfo_rooty(),
                self._janela.winfo_width(),
                self._janela.winfo_height(),
            )
            tamanho = (tela.winfo_reqwidth(), tela.winfo_reqheight())
            x, y = posicao_sem_cobrir(
                janela,
                tamanho,
                self.destaque.retangulo(),
                tela=(tela.winfo_screenwidth(), tela.winfo_screenheight()),
            )
            tela.geometry(f"+{x}+{y}")
            tela.lift()
        except tkinter.TclError:
            pass


def posicao_sem_cobrir(
    janela: tuple[int, int, int, int],
    tamanho: tuple[int, int],
    alvo: tuple[int, int, int, int] | None,
    *,
    tela: tuple[int, int] | None = None,
    folga: int = 16,
) -> tuple[int, int]:
    """Onde pôr uma janelinha de `tamanho` (largura, altura) sobre a `janela` (x, y, largura,
    altura) sem cobrir o `alvo` (esquerda, topo, direita, base): um dos quatro cantos da janela, o
    primeiro em que não há sobreposição (inferior direito, inferior esquerdo, superior direito,
    superior esquerdo) ou, se todos cobrem, o que cobre menos. `tela` mantém o resultado à vista."""
    jx, jy, jl, ja = janela
    largura, altura = tamanho
    candidatos = [
        (jx + jl - largura - folga, jy + ja - altura - folga),
        (jx + folga, jy + ja - altura - folga),
        (jx + jl - largura - folga, jy + folga),
        (jx + folga, jy + folga),
    ]

    def sobreposicao(posicao: tuple[int, int]) -> int:
        if alvo is None:
            return 0
        esq, topo, dir_, base = alvo[0] - 12, alvo[1] - 12, alvo[2] + 12, alvo[3] + 12
        x, y = posicao
        larg = min(dir_, x + largura) - max(esq, x)
        alt = min(base, y + altura) - max(topo, y)
        return max(0, larg) * max(0, alt)

    x, y = min(candidatos, key=sobreposicao)  # em empate, vale o primeiro da ordem acima
    if tela is not None:
        x = max(0, min(x, tela[0] - largura - 4))
        y = max(0, min(y, tela[1] - altura - 48))  # deixa a barra de tarefas livre
    return x, y


# --- a oferta da primeira abertura --------------------------------------------------------------
class OfertaDoTutorial(ctk.CTkToplevel):
    """A pergunta "Quer ver o passo a passo?", que aparece quando o programa abre.

    `ao_responder(ver, nao_mostrar_mais)` é chamado uma vez só: `ver` é True se a pessoa quer ver
    o passo a passo; `nao_mostrar_mais` vale a caixinha "Não mostrar novamente". Fechar a janela
    sem escolher vale "Agora não".
    """

    def __init__(self, janela, *, ao_responder: Callable[[bool, bool], None]):
        super().__init__(janela)
        self._ao_responder = ao_responder
        self._respondeu = False
        self.title("Passo a passo")
        self.resizable(False, False)
        with contextlib.suppress(tkinter.TclError):
            self.transient(janela)
        self.var_nao_mostrar = ctk.BooleanVar(value=False)

        ctk.CTkLabel(
            self, text="Quer ver o passo a passo para aprender a usar o programa?",
            font=ctk.CTkFont(size=16, weight="bold"), anchor="w", justify="left", wraplength=400,
        ).pack(fill="x", padx=20, pady=(18, 6))  # fmt: skip
        ctk.CTkLabel(
            self,
            text="São alguns minutos, e você pode sair quando quiser. Depois, o botão “Como "
            "usar”, no canto inferior direito da janela, abre o passo a passo de novo.",
            anchor="w", justify="left", wraplength=400, text_color=_COR_SUAVE,
        ).pack(fill="x", padx=20, pady=(0, 10))  # fmt: skip
        self.caixa_nao_mostrar = ctk.CTkCheckBox(
            self, text="Não mostrar novamente", variable=self.var_nao_mostrar
        )
        self.caixa_nao_mostrar.pack(anchor="w", padx=20, pady=(0, 12))
        linha = ctk.CTkFrame(self, fg_color="transparent")
        linha.pack(fill="x", padx=20, pady=(0, 16))
        self.botao_ver = ctk.CTkButton(
            linha, text="Ver o passo a passo", width=160, command=lambda: self.escolher(True)
        )
        self.botao_ver.pack(side="right", padx=(8, 0))
        self.botao_agora_nao = ctk.CTkButton(
            linha, text="Agora não", width=110, fg_color=("gray70", "gray30"),
            command=lambda: self.escolher(False),
        )  # fmt: skip
        self.botao_agora_nao.pack(side="right")
        self.protocol("WM_DELETE_WINDOW", lambda: self.escolher(False))
        self.update_idletasks()
        with contextlib.suppress(tkinter.TclError):
            x = janela.winfo_rootx() + (janela.winfo_width() - self.winfo_reqwidth()) // 2
            y = janela.winfo_rooty() + (janela.winfo_height() - self.winfo_reqheight()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self._foco: str | None = self.after(150, self._focar)

    def _focar(self) -> None:
        self._foco = None
        with contextlib.suppress(tkinter.TclError):
            self.lift()
            self.focus()

    @property
    def respondida(self) -> bool:
        return self._respondeu

    def escolher(self, ver: bool) -> None:
        if self._respondeu:
            return
        self._respondeu = True
        nao_mostrar = bool(self.var_nao_mostrar.get())
        with contextlib.suppress(tkinter.TclError):
            if self._foco is not None:
                self.after_cancel(self._foco)
            self.destroy()
        self._ao_responder(ver, nao_mostrar)
