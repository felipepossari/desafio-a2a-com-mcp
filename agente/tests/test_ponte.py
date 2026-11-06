import asyncio
import json

from agente import ponte
from agente.cliente_mcp import ErroMCP, extrair_versao_politica
from agente.ponte import _aplicar_resultado
from agente.tasks import CANCELED, COMPLETED, FAILED, INPUT_REQUIRED, Task


def _resposta(**dados):
    return {"result": {"resultType": "complete", "structuredContent": dados}}


RESERVA = {"reserva": "res-0003", "reservado": True, "sala": "sala-fusca", "inicio": "i", "fim": "f",
           "responsavel": "Marty", "politica": "2026-11-01", "motivo": None}


def _artifact(task):
    return json.loads(task.artifacts[0]["parts"][0]["text"])


def test_artifact_usa_a_versao_do_resource_e_nao_a_da_tool():
    task = Task(versao_politica="2099-01-01")
    _aplicar_resultado(task, _resposta(**RESERVA))
    assert task.estado == COMPLETED
    conteudo = _artifact(task)
    assert conteudo["politica"] == "2099-01-01"
    assert list(conteudo) == ["reserva", "sala", "inicio", "fim", "responsavel", "politica"]
    assert conteudo["sala"] == "sala-fusca"


def test_recusa_nao_gera_artifact():
    task = Task(versao_politica="2026-11-01")
    _aplicar_resultado(task, _resposta(**{**RESERVA, "reservado": False}))
    assert task.estado == CANCELED
    assert task.artifacts == []


CONFLITO = {"result": {"resultType": "input_required", "requestState": "opaco",
            "inputRequests": {"k": {"method": "elicitation/create", "params": {"mode": "form", "requestedSchema": {
                "type": "object", "properties": {"sala": {"type": "string", "enum": ["sala-fusca"]}}}}}}}}


class ClienteFalso:
    """Resource que muda de versao a cada leitura (como um servidor MCP reiniciado com outra politica)."""

    def __init__(self, versoes, respostas):
        self.versoes = list(versoes)
        self.respostas = list(respostas)
        self.chamadas = []

    async def ler_versao_politica(self, traceparent):
        self.chamadas.append("resources/read")
        versao = self.versoes.pop(0)
        if versao is None:
            raise ErroMCP("Nao foi possivel ler a versao da politica de uso em politica://uso")
        return versao

    async def chamar_tool(self, nome, argumentos, traceparent, input_responses=None, request_state=None):
        self.chamadas.append("tools/call")
        return self.respostas.pop(0)


def test_cada_task_le_o_resource_no_proprio_inicio():
    cliente = ClienteFalso(["2099-01-01", "3000-12-31"], [_resposta(**RESERVA), _resposta(**RESERVA)])
    t1, t2 = Task(), Task()
    asyncio.run(ponte.iniciar(cliente, t1, {}))
    asyncio.run(ponte.iniciar(cliente, t2, {}))
    assert _artifact(t1)["politica"] == "2099-01-01"
    assert _artifact(t2)["politica"] == "3000-12-31"
    assert cliente.chamadas == ["resources/read", "tools/call"] * 2


def test_retomada_usa_a_versao_lida_no_inicio_da_task():
    cliente = ClienteFalso(["2099-01-01"], [CONFLITO, _resposta(**RESERVA)])
    task = Task()
    asyncio.run(ponte.iniciar(cliente, task, {}))
    assert task.estado == INPUT_REQUIRED
    asyncio.run(ponte.continuar(cliente, task, "sala-fusca"))
    assert task.estado == COMPLETED
    assert _artifact(task)["politica"] == "2099-01-01"


def test_politica_ilegivel_falha_sem_chamar_a_tool():
    cliente = ClienteFalso([None], [])
    task = Task()
    asyncio.run(ponte.iniciar(cliente, task, {}))
    assert task.estado == FAILED
    assert cliente.chamadas == ["resources/read"]
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
