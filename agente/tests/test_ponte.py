import json

from agente.cliente_mcp import extrair_versao_politica
from agente.ponte import _aplicar_resultado
from agente.tasks import CANCELED, COMPLETED, Task


def _resposta(**dados):
    return {"result": {"resultType": "complete", "structuredContent": dados}}


RESERVA = {"reserva": "res-0003", "reservado": True, "sala": "sala-fusca", "inicio": "i", "fim": "f",
           "responsavel": "Marty", "politica": "2026-11-01", "motivo": None}


def test_artifact_usa_a_versao_do_resource_e_nao_a_da_tool():
    task = Task()
    _aplicar_resultado(task, _resposta(**RESERVA), "2099-01-01")
    assert task.estado == COMPLETED
    conteudo = json.loads(task.artifacts[0]["parts"][0]["text"])
    assert conteudo["politica"] == "2099-01-01"
    assert list(conteudo) == ["reserva", "sala", "inicio", "fim", "responsavel", "politica"]
    assert conteudo["sala"] == "sala-fusca"


def test_recusa_nao_gera_artifact():
    task = Task()
    _aplicar_resultado(task, _resposta(**{**RESERVA, "reservado": False}), "2026-11-01")
    assert task.estado == CANCELED
    assert task.artifacts == []


def _resource(texto):
    return {"result": {"contents": [{"uri": "politica://uso", "text": texto}]}}


def test_versao_da_primeira_linha():
    assert extrair_versao_politica(_resource("versao: 2026-11-01\n\n- regra")) == "2026-11-01"


def test_versao_ilegivel():
    assert extrair_versao_politica(_resource("sem versao aqui")) is None
    assert extrair_versao_politica(_resource("versao:")) is None
    assert extrair_versao_politica(_resource("")) is None
    assert extrair_versao_politica({"result": {"contents": []}}) is None
    assert extrair_versao_politica({"error": {"code": -32602, "message": "x"}}) is None
