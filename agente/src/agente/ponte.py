"""A ponte: costura `input_required` (MCP) com `TASK_STATE_INPUT_REQUIRED` (A2A).

O agente nao tem regra de sala. Ele extrai campos do pedido, chama a tool, e traduz
o resultado MCP para o estado da Task. O `requestState` e guardado na Task e
ecoado no retry sem ser aberto.
"""

from __future__ import annotations

import json
from typing import Any

from .cliente_mcp import TOOL_RESERVA, ClienteMCP, ErroMCP, log
from .pedido import RECUSAR
from .tasks import CANCELED, COMPLETED, FAILED, INPUT_REQUIRED, WORKING, Task, novo_id

MSG_ESTADO_INVALIDO = "A escolha nao pode ser aplicada: o pedido expirou ou e invalido. Faca um novo pedido."


def texto_alternativas(alternativas: list[str]) -> str:
    return "alternativas: " + ", ".join(alternativas)


def _texto_resultado(resultado: dict[str, Any]) -> str:
    return " ".join(p.get("text", "") for p in resultado.get("content", []) if isinstance(p, dict))


def _extrair_pergunta(resultado: dict[str, Any]) -> tuple[str, list[str]] | None:
    """Chave unica de inputRequests e as alternativas (enum ou const), sem abrir o requestState."""
    pedidos = resultado.get("inputRequests") or {}
    if len(pedidos) != 1 or not resultado.get("requestState"):
        return None
    chave, pedido = next(iter(pedidos.items()))
    campo = (((pedido.get("params") or {}).get("requestedSchema") or {}).get("properties") or {}).get("sala") or {}
    alternativas = campo.get("enum") or ([campo["const"]] if "const" in campo else [])
    if not alternativas:
        return None
    return chave, list(alternativas)


def _aplicar_resultado(task: Task, resposta: dict[str, Any], sala_vazia_ok: bool = False) -> None:
    """Traduz um envelope JSON-RPC do MCP para o estado da Task."""
    erro = resposta.get("error")
    if erro:
        if erro.get("code") == -32602:
            task.definir(FAILED, MSG_ESTADO_INVALIDO if task.request_state is not None else f"Pedido recusado: {erro.get('message', '')}")
        else:
            task.definir(FAILED, f"Erro do servidor MCP ({erro.get('code')}): {erro.get('message', '')}")
        return
    resultado = resposta.get("result") or {}
    tipo = resultado.get("resultType")
    if tipo == "input_required":
        pergunta = _extrair_pergunta(resultado)
        if pergunta is None:
            task.definir(FAILED, "O servidor MCP pediu informacao em um formato que o agente nao entende.")
            return
        task.chave_input, task.alternativas = pergunta
        task.request_state = resultado["requestState"]
        task.definir(INPUT_REQUIRED, texto_alternativas(task.alternativas))
        return
    if tipo not in (None, "complete"):
        task.definir(FAILED, f"resultType inesperado: {tipo}")
        return
    if resultado.get("isError"):
        task.definir(FAILED, _texto_resultado(resultado) or "A tool devolveu um erro.")
        return
    dados = resultado.get("structuredContent") or {}
    if dados.get("reservado") is False:
        task.definir(CANCELED, "Reserva recusada: nada foi reservado.")
        return
    reserva = {k: dados.get(k) for k in ("reserva", "sala", "inicio", "fim", "responsavel", "politica")}
    task.artifacts.append(
        {"artifactId": novo_id("art"), "name": "reserva", "parts": [{"text": json.dumps(reserva)}]}
    )
    task.definir(COMPLETED, f"Reserva {reserva['reserva']} confirmada na {reserva['sala']}.")


async def iniciar(cliente: ClienteMCP, task: Task, argumentos: dict[str, str]) -> None:
    task.argumentos = argumentos
    task.definir(WORKING)
    try:
        resposta = await cliente.chamar_tool(TOOL_RESERVA, argumentos, task.traceparent)
    except ErroMCP as e:
        task.definir(FAILED, str(e))
        return
    _aplicar_resultado(task, resposta)


async def continuar(cliente: ClienteMCP, task: Task, escolha: str | None) -> None:
    """Retoma uma Task pausada com `escolha=<id|recusar>`."""
    if escolha is None or (escolha != RECUSAR and escolha not in task.alternativas):
        task.definir(INPUT_REQUIRED, texto_alternativas(task.alternativas))
        return
    resposta_input = (
        {"action": "decline"} if escolha == RECUSAR else {"action": "accept", "content": {"sala": escolha}}
    )
    chave, estado = task.chave_input, task.request_state
    assert chave is not None and estado is not None
    log(f"[agente] retomando task={task.id} escolha={'recusar' if escolha == RECUSAR else 'sala'}")
    try:
        # id JSON-RPC novo (o cliente gera um por request), mesmos argumentos, mesmo trace
        resposta = await cliente.chamar_tool(
            TOOL_RESERVA, task.argumentos, task.traceparent, {chave: resposta_input}, estado
        )
    except ErroMCP as e:
        task.definir(FAILED, str(e))
        return
    _aplicar_resultado(task, resposta)
