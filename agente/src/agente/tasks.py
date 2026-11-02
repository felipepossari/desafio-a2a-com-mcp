"""Task A2A em memoria. O estado MCP (requestState) vive aqui e so aqui, por Task."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

SUBMITTED = "TASK_STATE_SUBMITTED"
WORKING = "TASK_STATE_WORKING"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
COMPLETED = "TASK_STATE_COMPLETED"
FAILED = "TASK_STATE_FAILED"
CANCELED = "TASK_STATE_CANCELED"
TERMINAIS = {COMPLETED, FAILED, CANCELED}


def novo_id(prefixo: str) -> str:
    return f"{prefixo}-{secrets.token_hex(6)}"


@dataclass
class Task:
    id: str = field(default_factory=lambda: novo_id("task"))
    context_id: str = field(default_factory=lambda: novo_id("ctx"))
    estado: str = SUBMITTED
    status_msg: dict[str, Any] | None = None
    historico: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    trava: asyncio.Lock = field(default_factory=asyncio.Lock)
    # ---- privado: nunca serializado para o cliente A2A -------------------------
    traceparent: str | None = None
    argumentos: dict[str, str] = field(default_factory=dict)  # pedido original, opaco p/ o agente
    chave_input: str | None = None  # chave de inputRequests atribuida pelo servidor MCP
    request_state: str | None = None  # opaco: guardar e ecoar
    alternativas: list[str] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.estado in TERMINAIS

    def mensagem_agente(self, texto: str) -> dict[str, Any]:
        msg = {
            "messageId": novo_id("msg"),
            "role": "ROLE_AGENT",
            "parts": [{"text": texto}],
            "taskId": self.id,
            "contextId": self.context_id,
        }
        self.historico.append(msg)
        return msg

    def definir(self, estado: str, texto: str | None = None) -> None:
        self.estado = estado
        if texto is not None:
            self.status_msg = self.mensagem_agente(texto)
        if estado in TERMINAIS or estado == WORKING:
            self.chave_input = self.request_state = None
            self.alternativas = []

    def wire(self) -> dict[str, Any]:
        status: dict[str, Any] = {"state": self.estado}
        if self.status_msg is not None:
            status["message"] = self.status_msg
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": status,
            "history": list(self.historico),
            "artifacts": list(self.artifacts),
        }


class Repositorio:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def criar(self) -> Task:
        t = Task()
        self._tasks[t.id] = t
        return t

    def obter(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)
