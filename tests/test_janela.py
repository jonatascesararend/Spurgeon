"""Testes da janela principal, com a janela escondida e as etapas de demonstração."""

import time
import tkinter
from pathlib import Path
from types import SimpleNamespace

import pytest

from spurgeon.core.config import ConfigApp
from spurgeon.ui import app as app_mod
from spurgeon.ui import dialogos
from spurgeon.ui.formulario import ValoresFormulario
from spurgeon.ui.painel_execucao import EstadoUI

pytestmark = pytest.mark.gui

LIMITE_S = 60


class Dialogos:
    """Substitui as caixas modais (que bloqueariam o teste) e registra o que foi mostrado."""

    def __init__(self):
        self.mostradas: list[tuple[str, str]] = []
        self.resposta_confirmar = True

    def mostrar(self, _janela, titulo, mensagem, *, botoes=("OK",), tipo="info"):
        self.mostradas.append((titulo, mensagem))
        return botoes[-1] if self.resposta_confirmar else botoes[0]


@pytest.fixture(scope="module")
def janela(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("janela")
    config = ConfigApp()
    config.pastas.entrada = pasta / "Downloads"
    config.pastas.projetos = pasta / "Projetos"
    config.pastas.recursos = pasta / "recursos"
    try:
        w = app_mod.JanelaPrincipal(config)
    except tkinter.TclError:
        pytest.skip("sem ambiente gráfico")
    w.withdraw()  # não piscar na tela de quem roda os testes
    yield w
    w.executor.encerrar(espera_s=5)
    w.destroy()


@pytest.fixture
def tela(janela, monkeypatch):
    """A janela limpa, sem efeitos colaterais no computador de quem testa."""
    caixas = Dialogos()
    monkeypatch.setattr(dialogos, "mostrar", caixas.mostrar)
    monkeypatch.setattr(app_mod, "abrir_pasta", lambda _p: None)
    monkeypatch.setattr(app_mod, "salvar_config", lambda *_a, **_k: None)
    monkeypatch.setenv("SPURGEON_DEMO", "1")
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0.002")
    janela.executor.encerrar(espera_s=5)
    janela.execucao.definir_estado(EstadoUI.OCIOSO, tem_projeto=False)
    janela._bloquear(False)
    janela._info_video = None  # a janela é compartilhada entre os testes
    janela.entrada.mostrar_estimativa("")
    janela.opcoes.aplicar_valores(ValoresFormulario())
    janela.entrada.var_nome.set("")
    janela.entrada.var_data.set("")
    janela._pastas = None
    janela._abas.set(app_mod.ABA_PROCESSAMENTO)
    janela._trocar_aba(app_mod.ABA_PROCESSAMENTO)
    janela._atualizar_aba_studio(recarregar=True)
    janela.caixas = caixas
    return janela


def bombear(janela, condicao, limite_s=LIMITE_S):
    """Roda a interface até `condicao()` ser verdadeira (os eventos chegam pelo `after`)."""
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        janela.update()
        if condicao():
            return
        time.sleep(0.03)
    pytest.fail(
        f"Tempo esgotado; estado={janela.execucao.estado}; caixas={janela.caixas.mostradas}"
    )


def escolher_video(janela, video):
    janela.entrada.definir_video(video)
    bombear(janela, lambda: janela._info_video is not None)


def test_abre_no_estado_inicial(tela):
    assert tela.execucao.estado == EstadoUI.OCIOSO
    assert tela.opcoes.valores() == ValoresFormulario()
    assert str(tela.execucao.botao_iniciar.cget("state")) == "normal"
    assert str(tela.execucao.botao_pausar.cget("state")) == "disabled"
    assert str(tela.execucao.botao_cancelar.cget("state")) == "disabled"


def test_legendas_dinamicas_so_ficam_ativas_com_shorts(tela):
    caixa = tela.opcoes.bools
    assert str(tela.opcoes._por_nome["legendas_dinamicas"].cget("state")) == "disabled"
    caixa["gerar_shorts"].set(True)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["legendas_dinamicas"].cget("state")) == "normal"
    caixa["gerar_shorts"].set(False)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["legendas_dinamicas"].cget("state")) == "disabled"


def test_campo_de_pausa_so_vale_com_o_video_longo(tela):
    tela.opcoes.bools["gerar_video_longo"].set(False)
    tela.opcoes._mudou()
    assert str(tela.opcoes._por_nome["limiar_pausa"].cget("state")) == "disabled"


def test_bloquear_trava_o_formulario(tela):
    tela._bloquear(True)
    assert str(tela.opcoes._por_nome["gerar_video_longo"].cget("state")) == "disabled"
    assert str(tela.entrada._controles[0].cget("state")) == "disabled"
    tela._bloquear(False)
    assert str(tela.opcoes._por_nome["gerar_video_longo"].cget("state")) == "normal"


@pytest.mark.ffmpeg
def test_escolher_video_preenche_nome_data_e_informacoes(tela, video_teste):
    escolher_video(tela, video_teste)
    assert tela.entrada.var_nome.get() == "teste"
    assert tela.entrada.var_data.get()
    assert "640×360" in tela.entrada.rotulo_info.cget("text")


def test_estimativa_de_tempo_aparece_e_acompanha_as_opcoes(tela, video_teste):
    assert tela.entrada.rotulo_estimativa.cget("text") == ""
    escolher_video(tela, video_teste)
    # o vídeo de teste dura 20 s: a estimativa é de poucos minutos
    assert "Tempo estimado nesta máquina" in tela.entrada.rotulo_estimativa.cget("text")
    antes = tela.entrada.rotulo_estimativa.cget("text")
    tela.opcoes.bools["gerar_shorts"].set(True)
    tela.opcoes._mudou()
    assert tela.entrada.rotulo_estimativa.cget("text") != antes


def test_campo_invalido_mostra_o_problema_e_nao_inicia(tela, video_falso):
    tela.entrada.var_video.set(str(video_falso))
    tela.opcoes.textos["limiar_pausa"].set("abc")
    tela._iniciar()
    assert tela.execucao.estado == EstadoUI.OCIOSO
    titulos = [t for t, _ in tela.caixas.mostradas]
    assert titulos == ["Confira os campos"]
    assert "Tamanho mínimo da pausa" in tela.caixas.mostradas[0][1]


@pytest.mark.ffmpeg
def test_fluxo_completo_com_revisao(tela, video_teste):
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão de teste")
    tela._iniciar()

    bombear(tela, lambda: tela.execucao.estado == EstadoUI.REVISAO)
    assert tela._revisao["id"] == "demo_revisar"
    assert str(tela.execucao.botao_iniciar.cget("state")) == "disabled"
    assert tela._pastas is not None and (tela._pastas.raiz / "estado.json").exists()

    tela._aprovar_revisao()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CONCLUIDO)
    pesados = [p for p in tela._pastas.work.rglob("*") if p.is_file() and p.suffix != ".json"]
    assert not pesados  # intermediários pesados apagados no sucesso (os .json da análise ficam)
    assert tela.caixas.mostradas == []  # nenhum diálogo de erro no caminho feliz
    assert str(tela.opcoes._por_nome["gerar_video_longo"].cget("state")) == "normal"


@pytest.mark.ffmpeg
def test_processar_de_novo_um_projeto_concluido_pergunta_antes(tela, video_teste):
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão repetido")
    tela.opcoes.bools["revisar_transcricao"].set(False)
    tela._iniciar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CONCLUIDO)

    tela.caixas.resposta_confirmar = False  # o usuário escolhe "Cancelar"
    tela._iniciar()
    bombear(tela, lambda: any(t == "Projeto já concluído" for t, _ in tela.caixas.mostradas))
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.OCIOSO)


@pytest.mark.ffmpeg
def test_cancelar_deixa_o_projeto_retomavel(tela, video_teste, monkeypatch):
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0.03")
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão cancelado")
    tela.opcoes.bools["revisar_transcricao"].set(False)
    tela._iniciar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.EXECUTANDO and tela._total > 0.1)

    tela._cancelar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CANCELADO)
    assert tela._pastas.work.exists()
    assert str(tela.execucao.botao_iniciar.cget("text")) == "Retomar processamento"


@pytest.mark.ffmpeg
def test_pausar_e_retomar(tela, video_teste, monkeypatch):
    monkeypatch.setenv("SPURGEON_DEMO_PASSO_S", "0.03")
    escolher_video(tela, video_teste)
    tela.entrada.var_nome.set("Sermão pausado")
    tela.opcoes.bools["revisar_transcricao"].set(False)
    tela._iniciar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.EXECUTANDO and tela._total > 0.1)

    tela._pausar_retomar()
    assert tela.execucao.estado == EstadoUI.PAUSANDO
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.PAUSADO)
    assert str(tela.execucao.botao_pausar.cget("text")) == "Retomar"

    tela._pausar_retomar()
    bombear(tela, lambda: tela.execucao.estado == EstadoUI.CONCLUIDO)


@pytest.mark.ffmpeg
def test_video_ilegivel_mostra_erro_na_tela(tela, tmp_path):
    lixo = tmp_path / "lixo.mp4"
    lixo.write_bytes(b"isto nao e um video" * 100)
    tela.entrada.definir_video(Path(lixo))
    bombear(
        tela, lambda: "Não foi possível usar este vídeo" in tela.entrada.rotulo_info.cget("text")
    )
    assert tela._info_video is None


# --- modos, marca d'água, projeto aberto e a aba Studio Shorts ---------------------------------


def estado_do(tela, nome: str) -> str:
    return str(tela.opcoes._por_nome[nome].cget("state"))


def botao_studio(tela) -> str:
    return str(tela._abas._buttons_dict[app_mod.ABA_STUDIO].cget("state"))


def test_modo_so_vinhetas_deixa_ativo_so_o_que_vale(tela):
    tela.opcoes.definir_modo("so_vinhetas")
    assert estado_do(tela, "limpar_audio") == "disabled"
    assert estado_do(tela, "gerar_video_longo") == "disabled"
    assert estado_do(tela, "gerar_shorts") == "disabled"
    assert estado_do(tela, "revisar_transcricao") == "disabled"
    assert str(tela.opcoes.marca._check_ativa.cget("state")) == "disabled"
    tela.opcoes.definir_modo("completo")
    assert estado_do(tela, "limpar_audio") == "normal"
    assert estado_do(tela, "gerar_shorts") == "normal"


def test_modo_so_shorts_marca_os_shorts_e_apaga_o_video_longo(tela):
    tela.opcoes.definir_modo("so_shorts")
    assert tela.opcoes.bools["gerar_shorts"].get() is True
    assert estado_do(tela, "gerar_shorts") == "disabled"
    assert estado_do(tela, "gerar_video_longo") == "disabled"
    assert estado_do(tela, "limiar_pausa") == "disabled"
    assert estado_do(tela, "aprovar_cortes") == "normal"
    assert tela.opcoes.valores().modo == "so_shorts"


def test_marca_dagua_liga_e_desliga_os_campos(tela):
    marca = tela.opcoes.marca
    assert str(marca._botao_imagem.cget("state")) == "disabled"
    marca.ativa.set(True)
    tela.opcoes._mudou()
    assert str(marca._botao_imagem.cget("state")) == "normal"
    assert str(marca._check_longo.cget("state")) == "normal"
    assert str(marca._check_shorts.cget("state")) == "disabled"  # os Shorts não estão marcados
    tela.opcoes.bools["gerar_shorts"].set(True)
    tela.opcoes._mudou()
    assert str(marca._check_shorts.cget("state")) == "normal"


def test_marca_com_png_transparente_nao_oferece_remover_o_fundo(tela, tmp_path):
    from PIL import Image

    transparente = tmp_path / "logo.png"
    Image.new("RGBA", (40, 20), (255, 0, 0, 0)).save(transparente)
    marca = tela.opcoes.marca
    marca.ativa.set(True)
    marca.definir_imagem(transparente)
    assert str(marca._check_remover.cget("state")) == "disabled"
    assert "fundo transparente" in marca._info_texto.cget("text")
    assert marca.valores()["marca_remover_fundo"] is False

    liso = tmp_path / "logo.jpg"
    Image.new("RGB", (40, 20), (255, 255, 255)).save(liso)
    marca.definir_imagem(liso)
    assert str(marca._check_remover.cget("state")) == "normal"
    marca.remover.set(True)
    tela.opcoes._mudou()
    assert marca.valores()["marca_remover_fundo"] is True
    assert tela.opcoes.valores().marca_imagem == str(liso)


def test_marca_ligada_sem_imagem_nao_deixa_iniciar(tela):
    tela.opcoes.marca.ativa.set(True)
    tela.opcoes._mudou()
    assert tela._coletar() is None
    assert any("marca d'água" in m for _, m in tela.caixas.mostradas)


@pytest.fixture
def projeto_pronto(tmp_path, video_falso):
    """Um projeto no disco, com transcrição e cortes calculados."""
    from datetime import date

    from spurgeon.core.config import OpcoesProcessamento
    from spurgeon.core.projeto import criar_ou_retomar_projeto
    from spurgeon.shorts.etapas import gravar_cortes
    from tests.test_shorts import cortes_do_sermao, gravar_transcricao, sermao_falso

    criado = criar_ou_retomar_projeto(
        tmp_path / "Projetos",
        video_falso,
        "Sermão Teste",
        date(2026, 10, 4),
        OpcoesProcessamento(gerar_shorts=True, legendas_dinamicas=True, qualidade="rapido"),
    )
    palavras = sermao_falso(frases=100)
    gravar_transcricao(criado.pastas.transcricao, palavras)
    cortes = cortes_do_sermao(palavras)
    for n, c in enumerate(cortes, start=1):
        c.titulo, c.slug = f"Título {n}", f"titulo_{n}"
    gravar_cortes(criado.pastas.cortes_shorts, cortes)
    return SimpleNamespace(pastas=criado.pastas, estado=criado.estado, cortes=cortes)


def test_abrir_projeto_preenche_a_tela_e_libera_o_studio(tela, projeto_pronto, video_falso):
    assert botao_studio(tela) == "disabled"
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    assert tela.entrada.var_nome.get() == "Sermão Teste"
    assert tela.entrada.var_data.get() == "04/10/2026"
    assert tela.entrada.var_video.get() == str(video_falso)
    assert tela.entrada.pasta_projetos() == projeto_pronto.pastas.raiz.parent
    assert "Shorts sugeridos" in tela.entrada.rotulo_projeto.cget("text")
    assert tela.opcoes.valores().gerar_shorts is True  # as opções do projeto voltam para a tela
    assert tela.opcoes.valores().modo == "completo"
    assert botao_studio(tela) == "normal"
    assert tela._abas.get() == app_mod.ABA_STUDIO
    assert tela.studio.editor is not None
    assert len(tela.studio.editor._cortes) == len(projeto_pronto.cortes)


@pytest.mark.ffmpeg
def test_abrir_projeto_nao_troca_a_data_pela_do_video(tela, projeto_pronto, video_teste):
    from spurgeon.core.estado import salvar_estado

    # o vídeo real tem outra data de gravação; a do projeto vale
    estado = projeto_pronto.estado
    estado.entrada.caminho = str(video_teste)
    salvar_estado(projeto_pronto.pastas.raiz, estado)
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    bombear(tela, lambda: tela._info_video is not None)
    assert tela.entrada.var_data.get() == "04/10/2026"


def test_pasta_que_nao_e_projeto_avisa(tela, tmp_path):
    vazia = tmp_path / "2026-10-04_Sem_estado"
    vazia.mkdir()
    tela._abrir_projeto(vazia)
    assert any("não parece um projeto" in m for _, m in tela.caixas.mostradas)
    assert tela._pastas is None


def test_studio_fica_desativado_ate_os_cortes_existirem(tela, projeto_pronto):
    projeto_pronto.pastas.cortes_shorts.unlink()
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    assert botao_studio(tela) == "disabled"
    assert tela._abas.get() == app_mod.ABA_PROCESSAMENTO
    projeto_pronto.pastas.cortes_shorts.write_text("[]", encoding="utf-8")
    tela._atualizar_aba_studio()
    assert botao_studio(tela) == "normal"


def test_studio_trava_enquanto_processa_e_libera_depois(tela, projeto_pronto):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela.execucao.definir_estado(EstadoUI.EXECUTANDO)
    tela._atualizar_aba_studio()
    assert "em andamento" in tela.studio._estado.cget("text")
    assert str(tela.studio._botao_renderizar.cget("state")) == "disabled"
    tela.execucao.definir_estado(EstadoUI.REVISAO)
    tela._atualizar_aba_studio()
    assert "Abrir revisão" in tela.studio._motivo_bloqueio
    tela.execucao.definir_estado(EstadoUI.OCIOSO)
    tela._atualizar_aba_studio()
    assert tela.studio._motivo_bloqueio is None
    assert str(tela.studio._botao_renderizar.cget("state")) == "normal"


def test_escolher_outro_video_larga_o_projeto_aberto(tela, projeto_pronto, tmp_path):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    outro = tmp_path / "outro.mp4"
    outro.write_bytes(b"x" * 100)
    tela.entrada.definir_video(outro)
    assert tela._pastas is None
    assert botao_studio(tela) == "disabled"


def test_so_vinhetas_sem_video_longo_no_projeto_e_recusado(tela, projeto_pronto):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._info_video = type("Info", (), {"duracao_s": 10.0, "hdr": False})()
    tela.opcoes.definir_modo("so_vinhetas")
    tela.opcoes.bools["introducao"].set(True)
    assert tela._coletar() is None
    assert any("não tem o vídeo longo pronto" in m for _, m in tela.caixas.mostradas)
    projeto_pronto.pastas.video_longo.write_bytes(b"x")
    tela.caixas.mostradas.clear()
    assert tela._coletar() is not None


def test_renderizar_do_studio_confirma_e_inicia_so_os_shorts(tela, projeto_pronto, monkeypatch):
    tela._abrir_projeto(projeto_pronto.pastas.raiz)
    tela._info_video = type("Info", (), {"duracao_s": 3900.0, "hdr": True})()
    chamadas = []
    monkeypatch.setattr(tela, "_iniciar", lambda ajustes=None: chamadas.append(ajustes))
    tela._renderizar_do_studio()
    assert chamadas == [
        {"modo": "so_shorts", "aprovar_cortes": False, "revisar_transcricao": False}
    ]
    _, texto = next((t, m) for t, m in tela.caixas.mostradas if t == "Renderizar os Shorts")
    assert "Tempo estimado" in texto and "não mudaram" in texto
    assert tela._abas.get() == app_mod.ABA_PROCESSAMENTO


def test_renderizar_sem_nada_pendente_so_avisa(tela, projeto_pronto, monkeypatch):
    from spurgeon.core.arquivos import escrever_json_atomico
    from spurgeon.render.shorts import detalhes_do_registro, planejar_shorts

    pastas = projeto_pronto.pastas
    pastas.shorts.mkdir(exist_ok=True)
    registro = {}
    for s in planejar_shorts(projeto_pronto.cortes):
        (pastas.shorts / s.video).write_bytes(b"x")
        registro[str(s.corte.id)] = {
            "arquivo": s.video,
            "assinatura": "a",
            **detalhes_do_registro(s.corte),
        }
    escrever_json_atomico(pastas.shorts_renderizados, registro)
    tela._abrir_projeto(pastas.raiz)
    chamadas = []
    monkeypatch.setattr(tela, "_iniciar", lambda ajustes=None: chamadas.append(ajustes))
    tela._renderizar_do_studio()
    assert chamadas == []
    assert any(t == "Nada para renderizar" for t, _ in tela.caixas.mostradas)
