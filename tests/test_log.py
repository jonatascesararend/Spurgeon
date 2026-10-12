import logging

from spurgeon.core.log import contexto_etapa, criar_log_projeto, encerrar_log


def test_linhas_do_log_trazem_a_etapa(tmp_path):
    arquivo = tmp_path / "processamento.log"
    log = criar_log_projeto(arquivo, nome="spurgeon.teste.etapa")
    try:
        log.info("fora de etapa")
        with contexto_etapa("transcrever"):
            log.info("Transcrevendo o bloco 3 de 9")
    finally:
        encerrar_log(log)
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    assert "| INFO    | -                    | fora de etapa" in linhas[0]
    assert "| transcrever          | Transcrevendo o bloco 3 de 9" in linhas[1]


def test_log_e_acrescentado_e_nao_apagado(tmp_path):
    arquivo = tmp_path / "processamento.log"
    for mensagem in ("primeira execução", "retomada"):
        log = criar_log_projeto(arquivo, nome="spurgeon.teste.acrescimo")
        log.info(mensagem)
        encerrar_log(log)
    texto = arquivo.read_text(encoding="utf-8")
    assert "primeira execução" in texto
    assert "retomada" in texto


def test_painel_recebe_as_mesmas_linhas(tmp_path):
    recebidas: list[tuple[str, int]] = []
    log = criar_log_projeto(
        tmp_path / "p.log",
        nome="spurgeon.teste.painel",
        callback=lambda linha, nivel: recebidas.append((linha, nivel)),
    )
    try:
        log.warning("atenção")
    finally:
        encerrar_log(log)
    assert recebidas[0][1] == logging.WARNING
    assert "atenção" in recebidas[0][0]


def test_acentos_ficam_legiveis_no_arquivo(tmp_path):
    arquivo = tmp_path / "p.log"
    log = criar_log_projeto(arquivo, nome="spurgeon.teste.acentos")
    log.info("Limpando áudio… ação concluída")
    encerrar_log(log)
    assert "Limpando áudio… ação concluída" in arquivo.read_text(encoding="utf-8")
