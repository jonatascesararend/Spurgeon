"""As opções de um projeto, ajustadas pelo celular antes de começar o processamento.

Só o que faz sentido decidir sermão a sermão (o que gerar, o trecho do vídeo, áudio, imagem,
Shorts, revisões, qualidade); o resto (centro do zoom, cores, fontes, arquivos das vinhetas e da
marca) continua na janela do PC e já vem nas opções salvas (`config.padroes`). Os campos e os
limites vêm de `OpcoesProcessamento`: a validação é a mesma do PC (pydantic), e o celular desenha
a tela a partir da descrição (`configuracao`), sem repetir regras no JavaScript.

Mudar só vale enquanto o projeto não começou (nenhuma etapa saiu de "pendente"): depois disso as
opções decidem o que já foi feito, e mudar refaria etapas sem a pessoa ver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from spurgeon.core.aparencia import AJUSTES_DO_TRATAMENTO
from spurgeon.core.config import OpcoesProcessamento
from spurgeon.core.estado import StatusEtapa, carregar_estado, salvar_estado
from spurgeon.core.projeto import PastasProjeto


class OpcaoInvalida(ValueError):
    pass


def _sim_nao(campo, rotulo, ajuda=""):
    return {"campo": campo, "rotulo": rotulo, "tipo": "sim_nao", "ajuda": ajuda}


def _escolha(campo, rotulo, opcoes, ajuda=""):
    return {"campo": campo, "rotulo": rotulo, "tipo": "escolha", "opcoes": opcoes, "ajuda": ajuda}


def _numero(campo, rotulo, minimo, maximo, passo, unidade="", ajuda=""):
    return {"campo": campo, "rotulo": rotulo, "tipo": "numero", "min": minimo, "max": maximo,
            "passo": passo, "unidade": unidade, "ajuda": ajuda}  # fmt: skip


def _trilha(campo, rotulo, ajuda=""):
    """A trilha de áudio de fora: o celular envia o arquivo (`remoto/trilha.py`) ou volta ao som
    do vídeo; o valor mostrado é só o nome do arquivo (o caminho do PC não vai para o celular)."""
    return {"campo": campo, "rotulo": rotulo, "tipo": "trilha", "ajuda": ajuda}


def _tempo(campo, rotulo, ajuda=""):
    return {"campo": campo, "rotulo": rotulo, "tipo": "tempo", "ajuda": ajuda}


def _ajuste(nome):
    """Um ajuste do tratamento de imagem (−100 a +100), desenhado como régua no celular."""
    rotulo, ajuda = AJUSTES_DO_TRATAMENTO[nome]
    return {"campo": f"tratamento_{nome}", "rotulo": rotulo, "tipo": "ajuste", "min": -100,
            "max": 100, "passo": 1, "ajuda": ajuda}  # fmt: skip


#: o grupo que o celular desenha com a prévia do quadro (antes e depois) e a sugestão automática
GRUPO_DO_TRATAMENTO = "Tratamento de imagem"


GRUPOS: list[tuple[str, list[dict[str, Any]]]] = [
    ("O que gerar", [
        _escolha("modo", "Processamento", [["completo", "Completo"], ["so_shorts", "Só Shorts"],
                                           ["so_audio", "Só o áudio"]],
                 "“Só Shorts” não gera o vídeo longo. “Só o áudio” só tira o som do vídeo (limpo e "
                 "normalizado, se marcado em Áudio) e o grava num arquivo para baixar."),
        _sim_nao("gerar_video_longo", "Vídeo longo (o sermão sem as pausas longas)"),
        _sim_nao("gerar_shorts", "Shorts"),
        _sim_nao("legendas_dinamicas", "Legenda nos Shorts"),
    ]),
    ("Trecho do vídeo", [
        _tempo("video_inicio_s", "Começa em", "0:00 = desde o começo do vídeo."),
        _tempo("video_fim_s", "Termina em", "0:00 = até o fim do vídeo."),
    ]),
    ("Áudio", [
        _sim_nao("limpar_audio", "Limpar o áudio (tira ruído)"),
        _sim_nao("normalizar_volume", "Normalizar o volume"),
        _trilha("audio_externo", "Trilha de áudio",
                "Um áudio melhorado por fora substitui o som do vídeo em tudo. Ele precisa começar "
                "junto com o vídeo."),
        _escolha("audio_formato", "Arquivo do “Só o áudio”", [["wav", "WAV (sem perdas)"],
                                                              ["mp3", "MP3 (menor)"]]),
        _numero("limiar_pausa_s", "Cortar pausas maiores que", 0.5, 30, 0.5, "s"),
    ]),
    ("Imagem", [
        _numero("zoom_pct", "Zoom", 100, 300, 5, "%", "100% = sem zoom (o centro é o da janela)."),
        _sim_nao("marca_dagua", "Marca d'água"),
        _sim_nao("introducao", "Introdução"),
        _sim_nao("vinheta_final", "Vinheta no fim"),
    ]),
    (GRUPO_DO_TRATAMENTO, [
        _sim_nao("tratamento_ativo", "Usar o tratamento de imagem"),
        *(_ajuste(nome) for nome in AJUSTES_DO_TRATAMENTO),
        _sim_nao("tratamento_nos_shorts", "Também nos Shorts"),
    ]),
    ("Shorts", [
        _numero("shorts_limite_seguranca", "No máximo", 1, 100, 1, "Shorts"),
        _numero("shorts_duracao_min_s", "Duração mínima", 10, 180, 5, "s"),
        _numero("shorts_duracao_max_s", "Duração máxima", 10, 180, 5, "s"),
    ]),
    ("Revisão", [
        _sim_nao("pausar_revisar_transcricao", "Parar para eu revisar a transcrição"),
    ]),
    ("Processamento", [
        _escolha("qualidade", "Qualidade", [["maxima", "Máxima"], ["alta", "Alta"],
                                             ["equilibrado", "Equilibrada"], ["rapido", "Rápida"]]),
        _escolha("uso_cpu_percentual", "Uso do processador", [[75, "75% (o PC continua usável)"],
                                                             [100, "100% (mais rápido)"]]),
    ]),
]  # fmt: skip
CAMPOS = {c["campo"]: c for _, campos in GRUPOS for c in campos}


def nao_comecou(pastas: PastasProjeto) -> bool:
    estado = carregar_estado(pastas.raiz)
    return estado is not None and all(e.status == StatusEtapa.PENDENTE for e in estado.etapas)


def _motivo_desligado(campo: str, opcoes: OpcoesProcessamento) -> str:
    """Por que a opção não pode ser ligada pelo celular (falta escolher um arquivo no PC)."""
    if campo == "introducao" and not opcoes.introducao_arquivo:
        return "Escolha o arquivo da introdução na janela do PC."
    if campo == "vinheta_final" and not opcoes.vinheta_arquivo:
        return "Escolha o arquivo da vinheta na janela do PC."
    if campo == "marca_dagua" and not opcoes.marca_imagem:
        return "Escolha a imagem da marca d'água na janela do PC."
    return ""


def _valor_na_tela(campo: str, opcoes: OpcoesProcessamento) -> Any:
    valor = getattr(opcoes, campo)
    if campo == "audio_externo":  # só o nome: o caminho do PC fica no PC
        return Path(valor).name if valor.strip() else ""
    return valor


def configuracao(pastas: PastasProjeto) -> dict[str, Any]:
    """A descrição da tela de opções, com os valores do projeto."""
    estado = carregar_estado(pastas.raiz)
    if estado is None:
        raise OpcaoInvalida("O projeto não tem estado.json.")
    opcoes = estado.opcoes
    grupos = []
    for titulo, campos in GRUPOS:
        grupos.append({"titulo": titulo, "tratamento": titulo == GRUPO_DO_TRATAMENTO, "campos": [
            {**c, "valor": _valor_na_tela(c["campo"], opcoes),
             "desligado": _motivo_desligado(c["campo"], opcoes)}
            for c in campos
        ]})  # fmt: skip
    pode = nao_comecou(pastas)
    from spurgeon.remoto.novo import video_baixado

    return {
        "grupos": grupos,
        "video_no_pc": video_baixado(pastas),  # a prévia do tratamento precisa do vídeo
        "pode_mudar": pode,
        "motivo": "" if pode else "O processamento já começou: as opções valem como estão.",
    }


def mudar(pastas: PastasProjeto, mudancas: dict[str, Any]) -> dict[str, Any]:
    """Grava as mudanças nas opções do projeto (só os campos da tela, com a validação do PC)."""
    if not mudancas:
        raise OpcaoInvalida("Nada para mudar.")
    if desconhecidos := sorted(set(mudancas) - set(CAMPOS)):
        raise OpcaoInvalida(f"Opção que o celular não muda: {', '.join(desconhecidos)}.")
    if not nao_comecou(pastas):
        raise OpcaoInvalida("O processamento já começou: as opções não mudam mais por aqui.")
    estado = carregar_estado(pastas.raiz)
    assert estado is not None
    for campo, valor in mudancas.items():
        if valor is True and (motivo := _motivo_desligado(campo, estado.opcoes)):
            raise OpcaoInvalida(motivo)
    if mudancas.get("audio_externo", "") != "":  # escolher um arquivo é pelo envio, não por caminho
        raise OpcaoInvalida("Envie o arquivo de áudio pelo botão da trilha.")
    trilha_antiga = estado.opcoes.audio_externo
    try:
        novas = OpcoesProcessamento.model_validate({**estado.opcoes.model_dump(), **mudancas})
    except ValidationError as erro:
        primeiro = erro.errors()[0]
        if not primeiro["loc"]:  # regra do modelo inteiro (duração mínima x máxima, trecho)
            mensagem = str(primeiro["msg"]).removeprefix("Value error, ")
            raise OpcaoInvalida(mensagem) from erro
        campo = CAMPOS.get(str(primeiro["loc"][0]), {})
        rotulo = campo.get("rotulo", primeiro["loc"][0])
        faixa = ""
        if campo.get("tipo") == "numero":
            de, ate = (f"{campo[k]:g}".replace(".", ",") for k in ("min", "max"))
            faixa = f" (de {de} a {ate}{' ' + campo['unidade'] if campo.get('unidade') else ''})"
        raise OpcaoInvalida(f"Valor inválido em “{rotulo}”{faixa}.") from erro
    estado.opcoes = novas
    salvar_estado(pastas.raiz, estado)
    if "audio_externo" in mudancas and trilha_antiga:
        from spurgeon.remoto.trilha import apagar_trilhas, enviada_pelo_celular

        if enviada_pelo_celular(pastas, trilha_antiga):  # a escolhida no PC nunca é apagada
            apagar_trilhas(pastas)
    return configuracao(pastas)
