"""O horário em que um Short vai ao ar: ler, mostrar e virar "postado" quando a hora passa.

`Corte.agendado_para` guarda `AAAA-MM-DDTHH:MM` na hora do computador (vazio = sem agendamento). É a
previsão que a pessoa marcou no Studio; o que cada rede de fato guardou fica em `postagens.json`
(`spurgeon.publicacao.agenda`). Quando a hora passa, o Short vira "já postado" sozinho.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from spurgeon.shorts.dados import Corte

FORMATO_GUARDADO = "%Y-%m-%dT%H:%M"
_DATA = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2}|\d{4}))?\s*$")
_HORA = re.compile(r"^\s*(\d{1,2})(?:[:hH](\d{2})?)?\s*$")


def ler_horario(texto: str) -> datetime | None:
    """O horário guardado no corte, na hora do computador; None se vazio ou ilegível."""
    if not texto:
        return None
    try:
        return datetime.strptime(texto, FORMATO_GUARDADO).astimezone()
    except ValueError:
        return None


def guardar_horario(quando: datetime) -> str:
    return quando.astimezone().strftime(FORMATO_GUARDADO)


def ler_data(texto: str, *, hoje: date | None = None) -> date | None:
    """`05/11/2026` (ou `5/11`, `5.11.26`) vira uma data; None se não faz sentido.

    Sem o ano vale o próximo em que a data ainda não passou.
    """
    achou = _DATA.match(texto)
    if not achou:
        return None
    dia, mes, ano = achou.groups()
    hoje = hoje or datetime.now().astimezone().date()
    try:
        if ano is None:
            candidato = date(hoje.year, int(mes), int(dia))
            return candidato if candidato >= hoje else date(hoje.year + 1, int(mes), int(dia))
        return date(int(ano) + (2000 if len(ano) == 2 else 0), int(mes), int(dia))
    except ValueError:
        return None


def ler_hora(texto: str) -> tuple[int, int] | None:
    """`18:30` (ou `18h30`, `18h`, `18`) vira `(18, 30)`; None se não faz sentido."""
    achou = _HORA.match(texto)
    if not achou:
        return None
    horas, minutos = int(achou.group(1)), int(achou.group(2) or 0)
    return (horas, minutos) if horas < 24 and minutos < 60 else None


def interpretar(data: str, hora: str, *, hoje: date | None = None) -> datetime | None:
    """`05/11/2026` e `18:30` (ou `5/11`, `18h`, `18`) viram um horário; None se não fazem sentido.

    Sem o ano vale o próximo em que a data ainda não passou.
    """
    dia, momento = ler_data(data, hoje=hoje), ler_hora(hora)
    if dia is None or momento is None:
        return None
    return datetime(dia.year, dia.month, dia.day, *momento).astimezone()


def rotulo_do_horario(quando: datetime) -> str:
    """`05/11/2026 às 18:30`."""
    quando = quando.astimezone()
    return f"{quando:%d/%m/%Y} às {quando:%H:%M}"


def faltam(quando: datetime, agora: datetime) -> str:
    """`em 2 dias e 3 h`, `em 25 min`, `agora`, `há 10 min`."""
    segundos = int((quando.astimezone() - agora.astimezone()).total_seconds())
    if abs(segundos) < 60:
        return "agora"
    passado = segundos < 0
    resto = timedelta(seconds=abs(segundos))
    dias, horas, minutos = resto.days, resto.seconds // 3600, (resto.seconds % 3600) // 60
    if dias:
        texto = f"{dias} dia{'s' if dias > 1 else ''}" + (f" e {horas} h" if horas else "")
    elif horas:
        texto = f"{horas} h" + (f" e {minutos} min" if minutos else "")
    else:
        texto = f"{minutos} min"
    return f"há {texto}" if passado else f"em {texto}"


def venceu(corte: Corte, agora: datetime) -> bool:
    """O Short está agendado e a hora marcada já passou."""
    quando = ler_horario(corte.agendado_para)
    return quando is not None and quando.astimezone() <= agora.astimezone()


def aplicar_vencimento(corte: Corte, agora: datetime) -> bool:
    """Passou a hora do agendamento: o Short vira "já postado" (na data marcada) e deixa de estar
    agendado. Devolve True se mudou alguma coisa."""
    if not venceu(corte, agora):
        return False
    quando = ler_horario(corte.agendado_para)
    assert quando is not None
    corte.postado = True
    corte.postado_em = quando.date().isoformat()
    corte.agendado_para = ""
    return True
