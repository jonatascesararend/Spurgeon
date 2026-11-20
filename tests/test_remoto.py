"""Controle remoto, Fase R1 (Remoto.md): servidor, segurança, leitura dos projetos, avisos e vigia.

Nenhum teste toca a internet, o Tailscale de verdade nem a pasta "Inicializar" do Windows.
"""

from __future__ import annotations

import json
from pathlib import Path

import http_ece
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from starlette.testclient import TestClient

from spurgeon.core.estado import EntradaVideo, Estado, EstadoEtapa, StatusEtapa, salvar_estado
from spurgeon.core.projeto import PastasProjeto
from spurgeon.publicacao.cofre import Cofre
from spurgeon.publicacao.erros import ErroDeRede
from spurgeon.remoto import avisos as av
from spurgeon.remoto import dados, inicializacao, seguranca, tailscale, vigia
from spurgeon.remoto.config import ConfigRemoto, carregar_config_remoto, salvar_config_remoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.servidor import criar_app, socket_exclusivo
from tests.falso_de_rede import TransporteFalso, resposta

CONTA = "autor@example.com"
NOME_DO_PROJETO = "2026-11-10_09._Sermão_de_teste"


# --- um projeto de mentira ------------------------------------------------------------------


def _corte(id_, inicio, *, incluir=True, titulo="", **extra) -> dict:
    return {
        "id": id_, "inicio": inicio, "fim": inicio + 40.0, "texto": f"texto do corte {id_}",
        "categoria": "graca", "nota": 80.0, "detalhe": {}, "titulo": titulo or f"Título {id_}",
        "slug": f"titulo_{id_}", "incluir": incluir, **extra,
    }  # fmt: skip


def criar_projeto(base: Path, nome: str = NOME_DO_PROJETO, *, status=StatusEtapa.CONCLUIDA) -> Path:
    pasta = base / nome
    pastas = PastasProjeto(pasta)
    pastas.criar()
    estado = Estado(
        entrada=EntradaVideo(caminho="video.mp4", tamanho=1, hash_rapido="x"),
        etapas=[
            EstadoEtapa(id="transcrever", status=StatusEtapa.CONCLUIDA),
            EstadoEtapa(id="renderizar_shorts", status=status, progresso=0.5),
        ],
    )
    salvar_estado(pasta, estado)
    cortes = [
        _corte(7, 100.0),
        _corte(3, 50.0, postado=True, postado_em="2026-11-12T18:30"),
        _corte(9, 300.0, incluir=False),
    ]
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    # o Short 1 (corte 3, o primeiro do sermão) está renderizado e postado
    (pastas.shorts / "postados").mkdir(parents=True)
    (pastas.shorts / "postados" / "short_01_titulo_3.mp4").write_bytes(b"0123456789" * 100)
    registro = {"3": {"arquivo": "postados/short_01_titulo_3.mp4", "assinatura": "a"}}
    pastas.shorts_renderizados.write_text(json.dumps(registro), "utf-8")
    pastas.log.write_text("linha 1\nlinha 2\n", "utf-8")
    return pasta


@pytest.fixture
def cofre(tmp_path) -> Cofre:
    return Cofre(tmp_path / "cofre.json", proteger=lambda b: b[::-1], revelar=lambda b: b[::-1])


@pytest.fixture
def projetos(tmp_path) -> Path:
    base = tmp_path / "Projetos"
    base.mkdir()
    criar_projeto(base)
    return base


def _navegador():
    """O par de chaves e o segredo que um navegador geraria ao se inscrever."""
    privada = ec.generate_private_key(ec.SECP256R1())
    publica = privada.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    segredo = b"0123456789abcdef"
    inscricao = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
        "keys": {"p256dh": av.para_b64url(publica), "auth": av.para_b64url(segredo)},
        "expirationTime": None,
    }
    return privada, segredo, inscricao


# --- segurança e configuração ---------------------------------------------------------------


def test_so_a_conta_autorizada_do_tailscale_entra():
    assert seguranca.autorizado({"Tailscale-User-Login": "Autor@Example.com"}, [CONTA])
    assert not seguranca.autorizado({"Tailscale-User-Login": "outro@example.com"}, [CONTA])
    assert not seguranca.autorizado({}, [CONTA])  # direto em 127.0.0.1, sem o Tailscale
    assert not seguranca.autorizado({"Tailscale-User-Login": CONTA}, [])  # lista vazia: ninguém


def test_config_remota_vive_num_arquivo_proprio(tmp_path):
    assert carregar_config_remoto(tmp_path / "nao_existe.json") == ConfigRemoto()
    caminho = salvar_config_remoto(
        ConfigRemoto(usuarios=[CONTA], dormindo=True), tmp_path / "r.json"
    )
    assert carregar_config_remoto(caminho).usuarios == [CONTA]
    caminho.write_text("{estragado", "utf-8")
    assert carregar_config_remoto(caminho) == ConfigRemoto()


def test_porta_exclusiva_nao_e_dividida_com_outro_programa():
    primeiro = socket_exclusivo(0)
    porta = primeiro.getsockname()[1]
    try:
        with pytest.raises(OSError):
            socket_exclusivo(porta)
    finally:
        primeiro.close()


# --- leitura dos projetos -------------------------------------------------------------------


def test_lista_e_detalhe_dos_projetos(projetos):
    [resumo] = dados.projetos(projetos)
    assert resumo["id"] == NOME_DO_PROJETO
    assert resumo["nome"] == "09. Sermão de teste"
    assert resumo["situacao"] == "Concluído"
    detalhe = dados.detalhe_do_projeto(dados.achar_projeto(projetos, NOME_DO_PROJETO))
    nomes = [e["nome"] for e in detalhe["andamento"]["etapas"]]
    assert nomes == ["Transcrevendo o áudio (Whisper)", "Renderizando os Shorts"]
    assert detalhe["andamento"]["log"] == ["linha 1", "linha 2"]


@pytest.mark.parametrize("ident", ["..", "../Projetos", "nao_e_projeto", "", "a/b", "C:\\Windows"])
def test_nao_abre_caminho_inventado(projetos, ident):
    (projetos / "nao_e_projeto").mkdir()
    with pytest.raises(dados.ProjetoNaoEncontrado):
        dados.achar_projeto(projetos, ident)


def test_shorts_na_ordem_do_sermao_com_numero_e_arquivo(projetos):
    pastas = dados.achar_projeto(projetos, NOME_DO_PROJETO)
    lista = dados.shorts(pastas)
    assert [(s["id"], s["numero"], s["renderizado"]) for s in lista] == [
        (3, 1, True), (7, 2, False), (9, None, False)
    ]  # fmt: skip
    assert lista[0]["postado_rotulo"] == "12/11/2026 às 18:30"
    assert lista[0]["categoria"]["rotulo"]  # rótulo da categoria, não a chave
    assert dados.arquivo_do_short(pastas, 3).name == "short_01_titulo_3.mp4"
    assert dados.arquivo_do_short(pastas, 7) is None


def test_projeto_sem_cortes_ainda_nao_quebra(tmp_path):
    base = tmp_path / "P"
    pasta = criar_projeto(base)
    PastasProjeto(pasta).cortes_shorts.unlink()
    assert dados.shorts(PastasProjeto(pasta)) == []


# --- avisos (Web Push) ----------------------------------------------------------------------


def test_token_vapid_assinado_pela_chave_do_pc():
    chave = av.nova_chave()
    token = av.token_vapid(chave, "https://fcm.googleapis.com/fcm/send/abc", agora=1000)
    cabecalho, corpo, assinatura = token.split(".")
    dados_do_corpo = json.loads(av.de_b64url(corpo))
    assert dados_do_corpo["aud"] == "https://fcm.googleapis.com"
    assert dados_do_corpo["exp"] == 1000 + av.VALIDADE_DO_TOKEN_S
    assert "example.invalid" in dados_do_corpo["sub"]  # nada do autor vai para o Google
    bruta = av.de_b64url(assinatura)
    der = encode_dss_signature(int.from_bytes(bruta[:32], "big"), int.from_bytes(bruta[32:], "big"))
    chave.public_key().verify(der, f"{cabecalho}.{corpo}".encode(), ec.ECDSA(hashes.SHA256()))


def test_aviso_cifrado_so_o_navegador_inscrito_le():
    privada, segredo, inscricao = _navegador()
    cifrado = av.cifrar(b'{"titulo": "oi"}', inscricao)
    aberto = http_ece.decrypt(
        cifrado, private_key=privada, auth_secret=segredo, version="aes128gcm"
    )
    assert json.loads(aberto) == {"titulo": "oi"}


def test_inscricao_invalida_e_recusada():
    _, _, boa = _navegador()
    for ruim in (
        None,
        {},
        {**boa, "endpoint": "http://inseguro/x"},
        {**boa, "keys": {"auth": "x"}},
    ):
        with pytest.raises(ValueError):
            av.validar_inscricao(ruim)
    curta = {**boa, "keys": {**boa["keys"], "auth": av.para_b64url(b"curto")}}
    with pytest.raises(ValueError):
        av.validar_inscricao(curta)


def test_enviar_aviso_e_tirar_inscricao_vencida(cofre):
    _, _, uma = _navegador()
    _, _, outra = _navegador()
    outra["endpoint"] = "https://fcm.googleapis.com/fcm/send/vencida"
    rede = (
        TransporteFalso()
        .responder("POST", "send/abc", resposta(201))
        .responder("POST", "send/vencida", resposta(410))
    )
    avisos = av.Avisos(cofre, rede, relogio=lambda: 1000.0)
    avisos.inscrever(uma)
    avisos.inscrever(outra)
    avisos.inscrever(uma)  # de novo: não duplica
    assert len(avisos.inscricoes()) == 2

    entregas = avisos.enviar(av.Aviso("Projeto", "Pronto", url="/#/p/x"))
    assert sorted(e.codigo for e in entregas) == [201, 410]
    assert [i["endpoint"] for i in avisos.inscricoes()] == [uma["endpoint"]]
    chamada = rede.chamadas_de("POST", "send/abc")[0]
    assert chamada.cabecalhos["Content-Encoding"] == "aes128gcm"
    assert chamada.cabecalhos["Authorization"].startswith("vapid t=")
    assert f"k={avisos.chave_publica()}" in chamada.cabecalhos["Authorization"]
    assert b"Pronto" not in chamada.corpo  # vai cifrado


def test_chave_e_inscricoes_ficam_no_cofre_como_segredo(cofre):
    _, _, inscricao = _navegador()
    avisos = av.Avisos(cofre, TransporteFalso())
    publica = avisos.chave_publica()
    avisos.inscrever(inscricao)
    bruto = cofre.caminho.read_text("utf-8")
    assert "PRIVATE KEY" not in bruto and inscricao["endpoint"] not in bruto
    assert av.Avisos(cofre, TransporteFalso()).chave_publica() == publica  # a mesma depois


def test_sem_internet_o_aviso_falha_sem_derrubar(cofre):
    _, _, inscricao = _navegador()

    class SemRede:
        def pedir(self, *_a, **_k):
            raise ErroDeRede("Sem conexão com fcm.googleapis.com.")

    avisos = av.Avisos(cofre, SemRede())
    avisos.inscrever(inscricao)
    [entrega] = avisos.enviar(av.Aviso("a", "b"))
    assert entrega.codigo == 0 and not entrega.ok
    assert avisos.inscricoes()  # não some por falta de internet


# --- vigia ----------------------------------------------------------------------------------


def _foto(**campos) -> vigia.Foto:
    base = {"nome": "Sermão", "concluido": False, "esperando": False, "erro": False,
            "renderizados": 0, "marcados": 5}  # fmt: skip
    return vigia.Foto(**{**base, **campos})


def test_vigia_avisa_so_o_que_mudou():
    andando = {"p": _foto()}
    assert vigia.avisos_das_mudancas(andando, andando) == []
    [revisao] = vigia.avisos_das_mudancas(andando, {"p": _foto(esperando=True)})
    assert "revisão" in revisao.texto and revisao.url == "/#/p/p" and revisao.marca == "p"
    [pronto] = vigia.avisos_das_mudancas(andando, {"p": _foto(concluido=True, renderizados=4)})
    assert "4 de 5 Shorts" in pronto.texto
    [mais] = vigia.avisos_das_mudancas(
        {"p": _foto(concluido=True, renderizados=4)}, {"p": _foto(concluido=True, renderizados=5)}
    )
    assert mais.texto == "1 Short renderizado."
    [erro] = vigia.avisos_das_mudancas(andando, {"p": _foto(erro=True)})
    assert "erro" in erro.texto
    assert vigia.avisos_das_mudancas({}, {"novo": _foto(concluido=True)}) == []


def test_vigia_primeira_olhada_so_fotografa(tmp_path):
    base = tmp_path / "Projetos"
    pasta = criar_projeto(base, status=StatusEtapa.EM_ANDAMENTO)
    enviados = []
    v = vigia.Vigia(base, enviados.append, intervalo_s=60)
    assert v.olhar() == []
    estado = Estado.model_validate_json((pasta / "estado.json").read_text("utf-8"))
    for etapa in estado.etapas:
        etapa.status = StatusEtapa.CONCLUIDA
    salvar_estado(pasta, estado)
    [aviso] = v.olhar()
    assert enviados == [aviso] and "1 de 2 Shorts" in aviso.texto
    assert v.olhar() == []


def test_vigia_calado_quando_nao_pode_avisar(tmp_path):
    base = tmp_path / "Projetos"
    pasta = criar_projeto(base, status=StatusEtapa.EM_ANDAMENTO)
    enviados = []
    v = vigia.Vigia(base, enviados.append, intervalo_s=60, pode_avisar=lambda: False)
    v.olhar()
    estado = Estado.model_validate_json((pasta / "estado.json").read_text("utf-8"))
    estado.etapas[1].status = StatusEtapa.CONCLUIDA
    salvar_estado(pasta, estado)
    assert v.olhar() == [] and enviados == []


# --- servidor -------------------------------------------------------------------------------


@pytest.fixture
def servidor(projetos, cofre):
    gravadas = []
    rede = TransporteFalso().responder("POST", "fcm.googleapis.com", resposta(201))
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]),
        projetos,
        av.Avisos(cofre, rede),
        "9.9.9",
        gravar_config=gravadas.append,
    )
    encerrado = []
    ctx.encerrar = lambda: encerrado.append(True)
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    cliente.ctx, cliente.gravadas, cliente.encerrado, cliente.rede = ctx, gravadas, encerrado, rede
    return cliente


def test_servidor_barra_quem_nao_vem_pelo_tailscale(servidor):
    assert TestClient(servidor.app).get("/api/projetos").status_code == 403
    outro = TestClient(servidor.app, headers={"Tailscale-User-Login": "intruso@example.com"})
    assert outro.get("/").status_code == 403
    assert servidor.get("/api/projetos").status_code == 200


def test_servidor_entrega_o_app_e_o_icone(servidor):
    pagina = servidor.get("/")
    assert pagina.status_code == 200 and "Studio Spurgeon" in pagina.text
    for arquivo in ("/app.js", "/sw.js", "/estilo.css", "/manifest.json"):
        assert servidor.get(arquivo).status_code == 200, arquivo
    icone = servidor.get("/icone-192.png")
    assert icone.status_code == 200 and icone.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert servidor.get("/icone-77.png").status_code == 404


def test_servidor_api_dos_projetos(servidor):
    ident = NOME_DO_PROJETO
    assert servidor.get("/api/projetos").json()[0]["id"] == ident
    assert servidor.get(f"/api/projetos/{ident}").json()["nome"] == "09. Sermão de teste"
    assert servidor.get(f"/api/projetos/{ident}/andamento").json()["situacao"] == "Concluído"
    assert len(servidor.get(f"/api/projetos/{ident}/shorts").json()) == 3
    assert servidor.get("/api/projetos/..%2F..%2Fsegredo").status_code == 404
    assert servidor.get("/api/projetos/nao_existe/shorts").json() == {
        "erro": "Projeto não encontrado."
    }


def test_servidor_toca_o_short_com_pulo_no_video(servidor):
    url = f"/api/projetos/{NOME_DO_PROJETO}/shorts/3/video"
    inteiro = servidor.get(url)
    assert inteiro.status_code == 200 and inteiro.headers["content-type"] == "video/mp4"
    assert inteiro.headers["accept-ranges"] == "bytes"
    trecho = servidor.get(url, headers={"Range": "bytes=10-19"})
    assert trecho.status_code == 206 and trecho.content == b"0123456789"
    nao_renderizado = servidor.get(f"/api/projetos/{NOME_DO_PROJETO}/shorts/7/video")
    assert nao_renderizado.status_code == 404


def test_dormindo_so_acorda(servidor):
    assert servidor.post("/api/sistema/dormir").json() == {"dormindo": True}
    assert servidor.gravadas[-1].dormindo is True
    resposta_dormindo = servidor.get("/api/projetos")
    assert resposta_dormindo.status_code == 503 and resposta_dormindo.json()["erro"] == "dormindo"
    assert servidor.get("/api/sistema").json()["dormindo"] is True
    assert servidor.get("/").status_code == 200  # o app abre para mostrar "acordar"
    servidor.post("/api/sistema/acordar")
    assert servidor.get("/api/projetos").status_code == 200


def test_encerrar_responde_e_depois_sai(servidor):
    assert servidor.post("/api/sistema/encerrar").json() == {"encerrando": True}
    assert servidor.encerrado == [True]
    assert servidor.get("/api/sistema/encerrar").status_code != 200  # só POST encerra
    assert servidor.encerrado == [True]


def test_inscrever_e_testar_os_avisos_pelo_app(servidor):
    _, _, inscricao = _navegador()
    chave = servidor.get("/api/avisos/chave").text
    assert len(av.de_b64url(chave)) == 65
    assert servidor.post("/api/avisos/inscrever", json=inscricao).json()["aparelhos"] == 1
    assert servidor.post("/api/avisos/inscrever", json={"endpoint": "x"}).status_code == 400
    assert servidor.post("/api/avisos/teste").json() == {"entregues": 1, "aparelhos": 1}
    assert servidor.get("/api/sistema").json()["aparelhos_com_aviso"] == 1
    servidor.post("/api/avisos/desligar")
    assert servidor.gravadas[-1].avisos_ligados is False


# --- Tailscale, inicialização e linha de comando --------------------------------------------


def test_le_a_conta_e_o_endereco_do_status_do_tailscale():
    status = {
        "Self": {"UserID": 42, "DNSName": "meu-pc.tailabc.ts.net."},
        "User": {"42": {"LoginName": "Autor@Example.com"}},
        "CertDomains": ["meu-pc.tailabc.ts.net"],
    }
    assert tailscale.conta_do_pc(status) == CONTA
    assert tailscale.endereco_do_pc(status) == "https://meu-pc.tailabc.ts.net/"
    assert tailscale.https_ligado(status)
    assert not tailscale.https_ligado({}) and tailscale.conta_do_pc({}) == ""


def test_atalho_da_inicializacao_aguenta_apostrofo_no_caminho():
    script = inicializacao.script_do_atalho(
        Path(r"C:\Users\D'Ávila\Startup\a.lnk"),
        Path(r"C:\x\pythonw.exe"),
        "-m spurgeon remoto",
        Path("C:/r"),
    )
    assert "'C:\\Users\\D''Ávila\\Startup\\a.lnk'" in script
    assert "$a.Arguments = '-m spurgeon remoto'" in script


def test_comando_remoto_na_linha_de_comando(monkeypatch):
    from spurgeon import __main__ as principal
    from spurgeon.remoto import cli

    chamados = []
    monkeypatch.setitem(
        cli._ACOES, "iniciar", lambda a: chamados.append(("iniciar", a.sem_tailscale)) or 0
    )
    monkeypatch.setitem(
        cli._ACOES, "configurar", lambda a: chamados.append(("configurar", a.inicializacao)) or 0
    )
    assert principal.main(["remoto"]) == 0
    assert principal.main(["remoto", "iniciar", "--sem-tailscale"]) == 0
    assert principal.main(["remoto", "configurar", "--inicializacao"]) == 0
    assert chamados == [("iniciar", False), ("iniciar", True), ("configurar", True)]
