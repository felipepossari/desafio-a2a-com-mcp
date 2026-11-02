import pytest

from agente.pedido import PedidoInvalido, parse_escolha, parse_reserva


def test_reserva_completa():
    p = parse_reserva("reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty McFly")
    assert p == {"sala": "sala-garagem", "inicio": "2026-11-03T14:00:00-03:00", "fim": "2026-11-03T15:00:00-03:00", "responsavel": "Marty McFly"}


def test_reserva_incompleta():
    with pytest.raises(PedidoInvalido, match="fim"):
        parse_reserva("reservar sala=a inicio=b responsavel=c")


def test_reserva_nao_reconhecida():
    with pytest.raises(PedidoInvalido):
        parse_reserva("oi")


def test_escolha():
    assert parse_escolha("escolha=sala-fusca") == "sala-fusca"
    assert parse_escolha("escolha=recusar") == "recusar"
    assert parse_escolha("qualquer coisa") is None
