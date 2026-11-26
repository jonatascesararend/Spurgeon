"""Cronograma do canal: o JSON do YouTube Studio lido (`canal/cronograma.py`), a última leitura
guardada e atualizada em segundo plano (`remoto/cronograma.py`) e as rotas do celular."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from starlette.testclient import TestClient

from spurgeon.canal.cronograma import (
    PROGRAMADO,
    PUBLICADO,
    SHORT,
    VIDEO,
    ItemDoCronograma,
    comeco_do_mes_passado,
    juntar,
    ler_item,
)
from spurgeon.publicacao.cofre import Cofre
from spurgeon.remoto.avisos import Avisos
from spurgeon.remoto.config import ConfigRemoto
from spurgeon.remoto.contexto import Contexto
from spurgeon.remoto.cronograma import VALIDADE_S, Cronograma
from spurgeon.remoto.servidor import criar_app
from tests.falso_de_rede import TransporteFalso
from tests.test_remoto import CONTA


def _utc(segundos: int) -> datetime:
    return datetime.fromtimestamp(segundos, UTC).replace(tzinfo=None)


NOV_24_1830 = int(datetime(2026, 11, 24, 18, 30, tzinfo=UTC).timestamp())
NOV_17_1930 = int(datetime(2026, 11, 17, 19, 30, tzinfo=UTC).timestamp())
SET_30 = int(datetime(2026, 9, 30, 12, 0, tzinfo=UTC).timestamp())


def _programado(video_id="Sh0rtProg01", segundos=NOV_24_1830, **extra) -> dict:
    return {
        "videoId": video_id, "title": "Um  Short   programado", "lengthSeconds": "52",
        "contentType": "CREATOR_CONTENT_TYPE_SHORTS", "privacy": "VIDEO_PRIVACY_PRIVATE",
        "timePublishedSeconds": "0",
        "scheduledPublishingDetails": {"scheduledPublishings": [{
            "scheduledTimeSeconds": str(segundos),
            "status": "SCHEDULED_PUBLISHING_STATUS_SCHEDULED",
        }]},
        **extra,
    }  # fmt: skip


def _publicado(video_id="LongoPub001", segundos=NOV_17_1930, privacidade="PUBLIC") -> dict:
    return {
        "videoId": video_id, "title": "Um sermão", "lengthSeconds": 4135,
        "contentType": "CREATOR_CONTENT_TYPE_UNSPECIFIED",
        "privacy": f"VIDEO_PRIVACY_{privacidade}",
        "timePublishedSeconds": str(segundos),
    }  # fmt: skip


def test_le_programado_e_publicado_do_json_do_studio():
    curto = ler_item(_programado(), VIDEO, hora_local=_utc)
    assert curto == ItemDoCronograma(
        "Sh0rtProg01", "Um Short programado", SHORT, PROGRAMADO, datetime(2026, 11, 24, 18, 30), 52
    )
    assert curto.para_json()["quando"] == "2026-11-24T18:30"
    assert ItemDoCronograma.de_json(curto.para_json()) == curto
    longo = ler_item(_publicado(), VIDEO, hora_local=_utc)
    assert (longo.tipo, longo.situacao, longo.duracao_s) == (VIDEO, PUBLICADO, 4135)


def test_fica_de_fora_o_que_nao_e_publico_nem_programado():
    assert ler_item(_publicado(privacidade="UNLISTED"), hora_local=_utc) is None
    assert ler_item(_publicado(privacidade="PRIVATE"), hora_local=_utc) is None
    cancelado = _programado()
    cancelado["scheduledPublishingDetails"]["scheduledPublishings"][0]["status"] = "OUTRO"
    assert ler_item(cancelado, hora_local=_utc) is None
    assert ler_item({"title": "sem id"}, hora_local=_utc) is None


def test_junta_as_abas_em_ordem_desde_o_mes_passado():
    paginas = {
        "short": [_programado(), _programado("Sh0rtVelho1", SET_30)],
        "upload": [_publicado(), _publicado()],  # o mesmo vídeo duas vezes (página repetida)
    }
    itens = juntar(paginas, date(2026, 10, 1), hora_local=_utc)
    assert [i.id for i in itens] == ["LongoPub001", "Sh0rtProg01"]
    assert comeco_do_mes_passado(date(2026, 11, 20)) == date(2026, 10, 1)
    assert comeco_do_mes_passado(date(2027, 1, 5)) == date(2026, 12, 1)


class _Relogio:
    def __init__(self, agora: datetime):
        self.agora = agora

    def __call__(self) -> datetime:
        return self.agora


ITENS = [
    ItemDoCronograma("v1", "Sermão", VIDEO, PUBLICADO, datetime(2026, 9, 28, 19, 0), 4000),
    ItemDoCronograma("v2", "Sermão novo", VIDEO, PUBLICADO, datetime(2026, 11, 17, 19, 30), 4100),
    ItemDoCronograma("s1", "Um Short", SHORT, PROGRAMADO, datetime(2026, 11, 24, 18, 30), 52),
]


def test_leitura_em_segundo_plano_guarda_e_volta_do_arquivo(tmp_path):
    arquivo = tmp_path / "cronograma.json"
    relogio = _Relogio(datetime(2026, 11, 20, 23, 0))
    pedidos: list[date] = []

    def ler(desde: date):
        pedidos.append(desde)
        return ITENS

    cronograma = Cronograma(arquivo, ler=ler, relogio=relogio)
    assert cronograma.velho() and cronograma.estado()["itens"] == []
    assert cronograma.atualizar()
    cronograma.esperar(5)
    assert pedidos == [date(2026, 10, 1)]
    estado = cronograma.estado()
    assert [i["id"] for i in estado["itens"]] == ["v2", "s1"]  # setembro fica de fora
    assert estado["desde"] == "2026-10-01" and estado["lido_em"] == "2026-11-20T23:00"
    assert not estado["velho"] and not estado["rodando"] and arquivo.is_file()

    de_novo = Cronograma(arquivo, ler=ler, relogio=relogio)  # o servidor reiniciou
    assert [i.id for i in de_novo.itens] == ["v1", "v2", "s1"] and not de_novo.velho()
    relogio.agora = datetime(2026, 11, 20, 23, 0) + timedelta(seconds=VALIDADE_S + 60)
    assert de_novo.velho()
    de_novo.lido_em = relogio.agora
    de_novo.marcar_velho()  # uma postagem terminou
    assert de_novo.velho()


def test_erro_na_leitura_aparece_e_mantem_o_que_ja_havia(tmp_path):
    def falha(_desde):
        raise RuntimeError("sessão caiu")

    cronograma = Cronograma(None, ler=falha, relogio=_Relogio(datetime(2026, 11, 20)))
    cronograma.itens = list(ITENS)
    cronograma.atualizar()
    cronograma.esperar(5)
    assert "sessão caiu" in cronograma.estado()["erro"] and len(cronograma.itens) == 3


def test_rotas_do_cronograma(tmp_path):
    lidos: list[date] = []

    def ler(desde):
        lidos.append(desde)
        return ITENS

    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(
        ConfigRemoto(usuarios=[CONTA]), tmp_path, Avisos(cofre, TransporteFalso()), "x",
        gravar_config=lambda _c: None,
        cronograma=Cronograma(None, ler=ler, relogio=_Relogio(datetime(2026, 11, 20, 12))),
    )  # fmt: skip
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    assert cliente.get("/api/canal/cronograma").json()["itens"] == []  # sem `auto`, não lê
    assert lidos == []
    cliente.get("/api/canal/cronograma?auto=1")
    ctx.cronograma.esperar(5)
    assert len(lidos) == 1
    estado = cliente.get("/api/canal/cronograma?auto=1").json()  # leitura nova: não lê de novo
    assert len(lidos) == 1 and estado["itens"][-1]["situacao"] == PROGRAMADO
    cliente.post("/api/canal/cronograma/atualizar")
    ctx.cronograma.esperar(5)
    assert len(lidos) == 2


def _com_relacionado(video: dict, alvo: str) -> dict:
    return {**video, "videoLinkageShortsAttribution": {"links": [{"targetExternalVideoId": alvo}]}}


def test_short_leva_o_sermao_relacionado():
    from spurgeon.canal.cronograma import id_do_relacionado, relacionados_sem_titulo

    nao_listado = _publicado("SermaoNL001", privacidade="UNLISTED")
    sermao_nao_listado = {**nao_listado, "title": "07. Sermão"}
    paginas = {
        "short": [
            _com_relacionado(_programado("Sh0rtA00001"), "SermaoNL001"),  # sermão não listado
            _com_relacionado(_programado("Sh0rtB00001"), "SermaoVelho"),  # sermão mais antigo
            _programado("Sh0rtC00001"),  # sem vídeo relacionado
        ],
        "upload": [sermao_nao_listado],
    }
    assert id_do_relacionado(paginas["short"][0]) == "SermaoNL001"
    assert relacionados_sem_titulo(paginas) == ["SermaoVelho"]
    titulos = {"SermaoVelho": "01. Um sermão antigo"}
    lidos = juntar(paginas, date(2026, 10, 1), hora_local=_utc, titulos=titulos)
    itens = {i.id: i for i in lidos}
    assert "SermaoNL001" not in itens  # o não listado não entra no calendário…
    assert itens["Sh0rtA00001"].relacionado == "07. Sermão"  # …mas dá o título
    assert itens["Sh0rtB00001"].relacionado == "01. Um sermão antigo"
    assert (itens["Sh0rtC00001"].relacionado_id, itens["Sh0rtC00001"].relacionado) == ("", "")
    assert ItemDoCronograma.de_json(itens["Sh0rtB00001"].para_json()) == itens["Sh0rtB00001"]


def test_arquivo_de_formato_antigo_e_lido_de_novo(tmp_path):
    import json

    arquivo = tmp_path / "cronograma.json"
    arquivo.write_text(json.dumps({"lido_em": "2026-11-20T23:00:00", "itens": [
        {"id": "s1", "titulo": "t", "tipo": "short", "situacao": "programado",
         "quando": "2026-11-24T18:30", "duracao_s": 5},
    ]}), "utf-8")  # fmt: skip
    relogio = _Relogio(datetime(2026, 11, 20, 23))
    cronograma = Cronograma(arquivo, ler=lambda _d: [], relogio=relogio)
    assert cronograma.velho() and cronograma.itens == []


def test_agendados_pelo_spurgeon_entram_na_hora_sem_ler_o_canal(tmp_path):
    import json

    from spurgeon.canal.cronograma import SPURGEON
    from spurgeon.core.projeto import PastasProjeto
    from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO, RegistroDePostagens
    from spurgeon.publicacao.modelo import Resultado
    from spurgeon.remoto.cronograma import itens_dos_projetos, juntar_com_os_projetos
    from tests.test_remoto import criar_projeto

    base = tmp_path / "Projetos"
    pastas = PastasProjeto(criar_projeto(base))  # corte 3: postado em 12/11 18:30
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    for c in cortes:
        if c["id"] == 7:  # agendado pelo celular: o registro tem o id do YouTube e o sermão
            c["agendado_para"] = "2026-11-25T14:00"
        if c["id"] == 9:  # marcado à mão no Studio do PC ("Agendado para")
            c["agendado_para"] = "2026-11-26T18:30"
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    RegistroDePostagens(pastas.work / NOME_DO_ARQUIVO).gravar(7, Resultado(
        "youtube", "agendado", "YtId0000007", detalhe="Vídeo relacionado: 09. Sermão de teste",
    ))  # fmt: skip

    locais = {i.titulo: i for i in itens_dos_projetos(base, date(2026, 10, 1))}
    assert set(locais) == {"Título 3", "Título 7", "Título 9"}
    assert all(i.origem == SPURGEON and i.tipo == SHORT for i in locais.values())
    sete = locais["Título 7"]
    assert (sete.id, sete.situacao, sete.quando) == (
        "YtId0000007",
        PROGRAMADO,
        datetime(2026, 11, 25, 14),
    )
    assert sete.relacionado == "09. Sermão de teste"
    assert locais["Título 3"].situacao == PUBLICADO
    assert locais["Título 3"].quando == datetime(2026, 11, 12, 18, 30)
    assert locais["Título 9"].id.startswith("spurgeon-") and locais["Título 9"].relacionado == ""
    assert itens_dos_projetos(base, date(2026, 11, 26)) == [locais["Título 9"]]

    canal = [
        ItemDoCronograma("YtId0000007", "Título 7 (mudado no YouTube)", SHORT, PROGRAMADO,
                         datetime(2026, 11, 25, 14), 40),  # o mesmo id
        ItemDoCronograma("x3", "título  3", SHORT, PUBLICADO, datetime(2026, 11, 12, 18, 30), 40),
        ItemDoCronograma("x9", "Outro", SHORT, PROGRAMADO, datetime(2026, 11, 26, 18, 30), 40),
    ]  # fmt: skip
    juntos = juntar_com_os_projetos(canal, list(locais.values()))
    assert [i.id for i in juntos] == ["x3", "YtId0000007", "x9"]  # nada repetido
    assert juntar_com_os_projetos([], list(locais.values()))[-1].titulo == "Título 9"

    cronograma = Cronograma(None, ler=lambda _d: [], relogio=_Relogio(datetime(2026, 11, 21, 9)),
                            pasta_projetos=base)  # fmt: skip
    titulos = [i["titulo"] for i in cronograma.estado()["itens"]]  # sem nunca ter lido o canal
    assert titulos == ["Título 3", "Título 7", "Título 9"]


# --- mesclar e acertar com o horário do YouTube -----------------------------------------------


def _projeto_com_agendados(tmp_path):
    import json

    from spurgeon.core.projeto import PastasProjeto
    from spurgeon.publicacao.agenda import NOME_DO_ARQUIVO, RegistroDePostagens
    from spurgeon.publicacao.modelo import Resultado
    from tests.test_remoto import criar_projeto

    base = tmp_path / "Projetos"
    pastas = PastasProjeto(criar_projeto(base))
    cortes = json.loads(pastas.cortes_shorts.read_text("utf-8"))
    for c in cortes:
        if c["id"] == 7:  # programado pelo Spurgeon (o registro tem o id do YouTube)
            c["agendado_para"] = "2026-11-25T14:00"
        if c["id"] == 9:  # marcado à mão no Spurgeon; no YouTube saiu com outro nome
            c["agendado_para"] = "2026-11-26T18:30"
            c["titulo"] = "A graça que nos sustenta"
    pastas.cortes_shorts.write_text(json.dumps(cortes), "utf-8")
    quando = datetime(2026, 11, 25, 14).astimezone().isoformat()
    RegistroDePostagens(pastas.work / NOME_DO_ARQUIVO).gravar(
        7, Resultado("youtube", "agendado", "YtId0000007", quando=quando)
    )
    return base, pastas


def test_o_horario_mudado_no_youtube_vale_no_spurgeon(tmp_path):
    from spurgeon.remoto.cronograma import acertar_com_o_canal
    from spurgeon.remoto.dados import carregar_cortes
    from spurgeon.remoto.despachante import horario_no_youtube

    base, pastas = _projeto_com_agendados(tmp_path)
    canal = [  # o 7 foi mudado à mão no YouTube Studio para o dia seguinte
        ItemDoCronograma("YtId0000007", "Título 7", SHORT, PROGRAMADO,
                         datetime(2026, 11, 26, 9, 15), 40),
    ]  # fmt: skip
    mudancas = acertar_com_o_canal(base, canal, pode_mover=lambda: False)
    assert mudancas == ["Título 7: 26/11 às 09:15"]
    sete = next(c for c in carregar_cortes(pastas) if c.id == 7)
    assert sete.agendado_para == "2026-11-26T09:15"
    assert horario_no_youtube(pastas, 7) == datetime(2026, 11, 26, 9, 15)
    assert acertar_com_o_canal(base, canal, pode_mover=lambda: False) == []  # já acertado
    canal = [ItemDoCronograma("YtId0000007", "Título 7", SHORT, PUBLICADO,
                              datetime(2026, 11, 26, 9, 15), 40)]  # fmt: skip
    acertar_com_o_canal(base, canal, pode_mover=lambda: False)
    sete = next(c for c in carregar_cortes(pastas) if c.id == 7)
    assert sete.postado and sete.postado_em == "2026-11-26T09:15" and not sete.agendado_para


def test_mesclar_um_short_com_outro_nome_no_youtube(tmp_path):
    import pytest

    from spurgeon.remoto.cronograma import MesclaInvalida
    from spurgeon.remoto.dados import carregar_cortes
    from spurgeon.remoto.despachante import horario_no_youtube

    base, pastas = _projeto_com_agendados(tmp_path)
    canal = [
        ItemDoCronograma("YtId0000007", "Título 7", SHORT, PROGRAMADO,
                         datetime(2026, 11, 25, 14), 40),
        ItemDoCronograma("YtGraca0009", "A graça de Deus que nos sustenta", SHORT, PROGRAMADO,
                         datetime(2026, 11, 26, 19, 0), 40),
        ItemDoCronograma("YtOutro0001", "Sem nada a ver", SHORT, PROGRAMADO,
                         datetime(2026, 12, 5, 19, 0), 40),
    ]  # fmt: skip
    cronograma = Cronograma(None, ler=lambda _d: canal, relogio=_Relogio(datetime(2026, 11, 21, 9)),
                            pasta_projetos=base)  # fmt: skip
    cronograma.pode_mover = lambda: False
    cronograma.atualizar()
    cronograma.esperar(5)
    itens = cronograma.estado()["itens"]
    nove = next(i for i in itens if i["origem"] == "spurgeon" and i["corte"] == 9)
    assert nove["parecidos"][0] == "YtGraca0009"  # título parecido e o mesmo dia
    assert "YtId0000007" not in nove["parecidos"]  # já ligado ao Short 7
    with pytest.raises(MesclaInvalida, match="já está mesclado"):
        cronograma.mesclar(nove["projeto"], 9, "YtId0000007")
    cronograma.mesclar(nove["projeto"], 9, "YtGraca0009")
    corte = next(c for c in carregar_cortes(pastas) if c.id == 9)
    assert corte.agendado_para == "2026-11-26T19:00"  # vale o horário do YouTube
    assert horario_no_youtube(pastas, 9) == datetime(2026, 11, 26, 19, 0)
    titulos = [i["titulo"] for i in cronograma.estado()["itens"]]
    assert "A graça que nos sustenta" not in titulos  # sem a duplicata


def test_rota_de_mesclar(tmp_path):
    base, _pastas = _projeto_com_agendados(tmp_path)
    canal = [ItemDoCronograma("YtGraca0009", "A graça de Deus", SHORT, PROGRAMADO,
                              datetime(2026, 11, 26, 19, 0), 40)]  # fmt: skip
    cofre = Cofre(tmp_path / "c.json", proteger=lambda b: b, revelar=lambda b: b)
    ctx = Contexto(ConfigRemoto(usuarios=[CONTA]), base, Avisos(cofre, TransporteFalso()), "x",
                   gravar_config=lambda _c: None,
                   cronograma=Cronograma(None, ler=lambda _d: canal, pasta_projetos=base,
                                         relogio=_Relogio(datetime(2026, 11, 21, 9))))  # fmt: skip
    ctx.cronograma.pode_mover = lambda: False
    ctx.cronograma.atualizar()
    ctx.cronograma.esperar(5)
    cliente = TestClient(criar_app(ctx), headers={"Tailscale-User-Login": CONTA})
    projeto = base.iterdir().__next__().name
    errado = cliente.post("/api/canal/cronograma/mesclar",
                          json={"projeto": projeto, "corte": 9, "canal": "naoexiste"})  # fmt: skip
    assert errado.status_code == 409 and "atualize" in errado.json()["erro"]
    certo = cliente.post("/api/canal/cronograma/mesclar",
                         json={"projeto": projeto, "corte": 9, "canal": "YtGraca0009"})  # fmt: skip
    assert certo.status_code == 200 and certo.json()["mesclado"]["id"] == "YtGraca0009"
