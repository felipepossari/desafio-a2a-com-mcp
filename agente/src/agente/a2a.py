"""Servidor A2A v1.0, binding JSON-RPC 2.0 sobre HTTP (SendMessage e GetTask)."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import ponte
from .cliente_mcp import ClienteMCP, log
from .pedido import PedidoInvalido, parse_escolha, parse_reserva
from .tasks import FAILED, INPUT_REQUIRED, Repositorio, novo_id

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
TASK_NOT_FOUND, UNSUPPORTED_OPERATION = -32001, -32004


class ErroRpc(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code, self.message = code, message


def card(url_publica: str) -> dict[str, Any]:
    return {
        "name": "Central de Salas",
        "description": "Reserva salas de reuniao da Hill Valley Tech.",
        "provider": {"organization": "Hill Valley Tech", "url": "https://hillvalley.example"},
        "version": "1.0.0",
        "supportedInterfaces": [
            {"url": f"{url_publica}/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "reservar-sala",
                "name": "Reservar sala",
                "description": "Reserva uma sala em um intervalo. Se houver conflito, pergunta qual alternativa usar.",
                "tags": ["salas", "agenda"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
                "examples": [
                    "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
                ],
            }
        ],
    }


def criar_app(mcp_url: str, url_publica: str) -> Starlette:
    cliente = ClienteMCP(mcp_url)
    repo = Repositorio()

    def _mensagem(params: Any) -> tuple[dict[str, Any], str]:
        if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
            raise ErroRpc(INVALID_PARAMS, "params.message e obrigatorio")
        msg = params["message"]
        partes = msg.get("parts")
        if not isinstance(partes, list) or not partes:
            raise ErroRpc(INVALID_PARAMS, "message.parts e obrigatorio")
        texto = " ".join(p.get("text", "") for p in partes if isinstance(p, dict) and isinstance(p.get("text"), str))
        if not texto.strip():
            raise ErroRpc(INVALID_PARAMS, "message precisa de uma parte de texto")
        msg.setdefault("messageId", novo_id("msg"))
        msg.setdefault("role", "ROLE_USER")
        return msg, texto

    async def send_message(params: Any, traceparent: str | None) -> dict[str, Any]:
        msg, texto = _mensagem(params)
        task_id = msg.get("taskId")
        if task_id:
            task = repo.obter(task_id)
            if task is None:
                raise ErroRpc(TASK_NOT_FOUND, f"Task nao encontrada: {task_id}")
            async with task.trava:
                if task.terminal:
                    raise ErroRpc(UNSUPPORTED_OPERATION, f"Task {task.id} ja esta em estado terminal ({task.estado})")
                if task.estado != INPUT_REQUIRED:
                    raise ErroRpc(UNSUPPORTED_OPERATION, f"Task {task.id} nao aguarda entrada")
                if traceparent:
                    task.traceparent = traceparent
                task.historico.append(msg)
                await ponte.continuar(cliente, task, parse_escolha(texto))
                return {"task": task.wire()}
        task = repo.criar()
        task.traceparent = traceparent
        task.historico.append(msg)
        async with task.trava:
            try:
                argumentos = parse_reserva(texto)
            except PedidoInvalido as e:
                task.definir(FAILED, str(e))
                return {"task": task.wire()}
            await ponte.iniciar(cliente, task, argumentos)
            return {"task": task.wire()}

    async def get_task(params: Any) -> dict[str, Any]:
        task_id = params.get("id") if isinstance(params, dict) else None
        if not isinstance(task_id, str):
            raise ErroRpc(INVALID_PARAMS, "params.id e obrigatorio")
        task = repo.obter(task_id)
        if task is None:
            raise ErroRpc(TASK_NOT_FOUND, f"Task nao encontrada: {task_id}")
        return {"task": task.wire()}

    async def rpc(request: Request) -> JSONResponse:
        try:
            corpo = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "JSON invalido"}})
        if not isinstance(corpo, dict) or corpo.get("jsonrpc") != "2.0" or not isinstance(corpo.get("method"), str):
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "Request invalido"}})
        rid, metodo = corpo.get("id"), corpo["method"]
        traceparent = request.headers.get("traceparent")
        log(f"[a2a] metodo={metodo} id={rid} traceparent={traceparent or '-'}")
        try:
            if metodo == "SendMessage":
                resultado = await send_message(corpo.get("params"), traceparent)
            elif metodo == "GetTask":
                resultado = await get_task(corpo.get("params"))
            else:
                raise ErroRpc(METHOD_NOT_FOUND, f"Metodo nao suportado: {metodo}")
        except ErroRpc as e:
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}})
        except Exception as e:  # nunca vaza detalhes (nem estado) para o cliente
            log(f"[a2a] erro interno: {type(e).__name__}")
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": INTERNAL, "message": "Erro interno"}})
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": resultado})

    async def agent_card(_: Request) -> JSONResponse:
        return JSONResponse(card(url_publica))

    @asynccontextmanager
    async def lifespan(_: Starlette):
        yield
        await cliente.fechar()

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/.well-known/agent-card.json", agent_card, methods=["GET"]),
            Route("/a2a", rpc, methods=["POST"]),
        ],
    )


def app_do_ambiente() -> Starlette:
    porta = int(os.environ.get("AGENTE_PORT", 7300))
    mcp_url = os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")
    url_publica = os.environ.get("AGENTE_URL_PUBLICA", f"http://127.0.0.1:{porta}").rstrip("/")
    return criar_app(mcp_url, url_publica)
