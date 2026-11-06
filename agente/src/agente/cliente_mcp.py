"""Cliente MCP cru sobre HTTP (httpx).

Nao usa callback de elicitation de SDK: o agente precisa enxergar o `input_required`
para costura-lo com a Task A2A. Cada request leva o `_meta` completo (sem sessao) e
um id JSON-RPC novo.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import secrets
import sys
from typing import Any

import httpx

PROTOCOLO = "2026-07-28"
CLIENT_INFO = {"name": "agente-central-de-salas", "version": "1.0.0"}
CAPABILITIES = {"elicitation": {"form": {}}}
POLITICA_URI = "politica://uso"
TOOL_RESERVA = "reservar_sala"


class ErroMCP(Exception):
    """O servidor MCP nao respondeu de forma utilizavel."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def novo_traceparent(traceparent: str | None) -> str:
    """Mesmo trace-id, span-id novo por request. Sem traceparent de entrada, cria um trace."""
    trace_id = None
    if traceparent:
        partes = traceparent.split("-")
        if len(partes) == 4 and len(partes[1]) == 32:
            trace_id = partes[1]
    return f"00-{trace_id or secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def extrair_versao_politica(resposta: dict[str, Any]) -> str | None:
    """Versao declarada na primeira linha (`versao: <v>`) do resource, ou None se ilegivel."""
    if resposta.get("error"):
        return None
    contents = (resposta.get("result") or {}).get("contents") or [{}]
    linhas = (contents[0].get("text") or "").splitlines()
    chave, sep, valor = (linhas[0] if linhas else "").partition(":")
    valor = valor.strip()
    return valor if sep and chave.strip() == "versao" and valor else None


class ClienteMCP:
    def __init__(self, url: str) -> None:
        self._url = url
        self._http = httpx.AsyncClient(timeout=30.0)
        self._ids = itertools.count(1)
        self._descoberto = False
        self._trava = asyncio.Lock()
        self.tools: dict[str, dict[str, Any]] = {}
        self.versao_politica: str | None = None

    async def fechar(self) -> None:
        await self._http.aclose()

    async def _post(self, metodo: str, params: dict[str, Any], nome: str | None, traceparent: str | None) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOLO,
            "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
            "io.modelcontextprotocol/clientCapabilities": CAPABILITIES,
            "traceparent": novo_traceparent(traceparent),
        }
        cabecalhos = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOLO,
            "Mcp-Method": metodo,
        }
        if nome:
            cabecalhos["Mcp-Name"] = nome
        corpo = {"jsonrpc": "2.0", "id": next(self._ids), "method": metodo, "params": {**params, "_meta": meta}}
        log(f"[agente->mcp] metodo={metodo} id={corpo['id']} traceparent={meta['traceparent']}")
        try:
            resp = await self._http.post(self._url, json=corpo, headers=cabecalhos)
        except httpx.HTTPError as e:
            self._descoberto = False  # servidor pode ter reiniciado: redescobre na proxima
            raise ErroMCP(f"Servidor MCP indisponivel: {type(e).__name__}") from None
        texto = resp.text
        if "text/event-stream" in resp.headers.get("content-type", ""):
            dados = [l[5:].strip() for l in texto.splitlines() if l.startswith("data:")]
            texto = dados[-1] if dados else ""
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            raise ErroMCP(f"Resposta MCP invalida (HTTP {resp.status_code})") from None

    async def descobrir(self, traceparent: str | None) -> None:
        """tools/list antes do primeiro tools/call, mais a politica de uso (resource)."""
        async with self._trava:
            if self._descoberto:
                return
            resp = await self._post("tools/list", {}, None, traceparent)
            tools = {t["name"]: t for t in (resp.get("result") or {}).get("tools", [])}
            if TOOL_RESERVA not in tools:
                raise ErroMCP(f"O servidor MCP nao oferece a tool {TOOL_RESERVA}")
            resp = await self._post("resources/read", {"uri": POLITICA_URI}, POLITICA_URI, traceparent)
            versao = extrair_versao_politica(resp)
            if versao is None:
                raise ErroMCP(f"Nao foi possivel ler a versao da politica de uso em {POLITICA_URI}")
            self.versao_politica = versao
            self.tools = tools
            self._descoberto = True

    async def chamar_tool(
        self,
        nome: str,
        argumentos: dict[str, Any],
        traceparent: str | None,
        input_responses: dict[str, Any] | None = None,
        request_state: str | None = None,
    ) -> dict[str, Any]:
        """Devolve o envelope JSON-RPC bruto: {"result": ...} ou {"error": ...}."""
        await self.descobrir(traceparent)
        params: dict[str, Any] = {"name": nome, "arguments": argumentos}
        if input_responses is not None:
            params["inputResponses"] = input_responses
        if request_state is not None:
            params["requestState"] = request_state  # ecoado byte a byte, nunca interpretado
        return await self._post("tools/call", params, nome, traceparent)
