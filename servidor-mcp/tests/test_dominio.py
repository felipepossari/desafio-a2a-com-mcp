from pathlib import Path

import pytest

from central_de_salas.dominio import (
    ERRO_DURACAO, ERRO_INTERVALO, ERRO_JANELA, Dominio, ErroDeDominio, erro_sala,
)

DADOS = Path(__file__).resolve().parents[2] / "dados"


def h(hora: str) -> str:
    return f"2026-11-03T{hora}:00-03:00"


@pytest.fixture
def dom() -> Dominio:
    return Dominio.carregar(DADOS)


@pytest.mark.parametrize(
    "sala,ini,fim,msg",
    [
        ("sala-delorean", "09:00", "10:00", erro_sala("sala-delorean")),
        ("sala-aquario", "10:00", "09:00", ERRO_INTERVALO),
        ("sala-aquario", "09:00", "09:00", ERRO_INTERVALO),
        ("sala-aquario", "07:00", "08:00", ERRO_JANELA),
        ("sala-aquario", "19:00", "21:00", ERRO_JANELA),
        ("sala-aquario", "09:00", "12:00", ERRO_DURACAO),
    ],
)
def test_erros(dom, sala, ini, fim, msg):
    with pytest.raises(ErroDeDominio, match=msg):
        dom.validar(sala, h(ini), h(fim))


def test_bordas_da_janela_sao_validas(dom):
    dom.validar("sala-aquario", h("08:00"), h("10:00"))
    dom.validar("sala-aquario", h("18:00"), h("20:00"))


def test_fuso_diferente_e_convertido(dom):
    # 12:00Z = 09:00 em -03:00
    dom.validar("sala-aquario", "2026-11-03T12:00:00+00:00", "2026-11-03T13:00:00+00:00")


def test_sem_fuso_e_invalido(dom):
    with pytest.raises(ErroDeDominio):
        dom.validar("sala-aquario", "2026-11-03T09:00:00", "2026-11-03T10:00:00")


def test_sobreposicao_estrita(dom):
    _, ini, fim = dom.validar("sala-garagem", h("15:00"), h("16:00"))
    assert dom.conflitos("sala-garagem", ini, fim) == []  # encosta em res-0001 (14h-15h)
    _, ini, fim = dom.validar("sala-garagem", h("14:30"), h("15:30"))
    assert [r.id for r in dom.conflitos("sala-garagem", ini, fim)] == ["res-0001"]


def test_alternativas_ordem_capacidade_e_id(dom):
    sala, ini, fim = dom.validar("sala-garagem", h("14:00"), h("15:00"))
    assert dom.alternativas(sala, ini, fim) == ["sala-fusca", "sala-mirante"]
    sala, ini, fim = dom.validar("sala-aquario", h("09:00"), h("10:00"))
    assert dom.alternativas(sala, ini, fim) == ["sala-porao", "sala-fusca", "sala-garagem"]  # no maximo 3


def test_reserva_ids_sequenciais_e_visivel(dom):
    r1 = dom.reservar("sala-aquario", h("09:00"), h("10:00"), "Doc")
    r2 = dom.reservar("sala-aquario", h("10:00"), h("11:00"), "Doc")
    assert (r1.id, r2.id) == ("res-0003", "res-0004")
    _, ini, fim = dom.validar("sala-aquario", h("09:30"), h("10:30"))
    assert len(dom.conflitos("sala-aquario", ini, fim)) == 2


def test_versao_politica(dom):
    assert dom.versao_politica == "2026-11-01"
