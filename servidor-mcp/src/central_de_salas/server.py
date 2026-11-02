"""Servidor MCP da Central de Salas (revisao 2026-07-28, Streamable HTTP).

MRTR de primeira classe do SDK: `reservar_sala` tem um parametro resolvido por
`Resolve(escolha_de_sala)`. Quando ha conflito, o resolver devolve um `Elicit` e o
SDK termina a resposta com `resultType: input_required`, `inputRequests` e um
`requestState` selado (AES-256-GCM, TTL, vinculado ao metodo, a tool e aos
argumentos). Nada fica em memoria entre a pergunta e o retry.
"""

import sys
import time
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.server.mcpserver.resolve import Elicit, Resolve
from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.request_state import RequestStateSecurity
from pydantic import BaseModel, Field, create_model

from . import config
from .dominio import ERRO_SEM_ALTERNATIVA, Dominio, ErroDeDominio

POLITICA_URI = "politica://uso"


# ---- modelos de saida (geram o outputSchema do tools/list) --------------------
class SalaOut(BaseModel):
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


class ListaDeSalas(BaseModel):
    salas: list[SalaOut]


class ConflitoOut(BaseModel):
    id: str
    inicio: str
    fim: str
    responsavel: str


class Disponibilidade(BaseModel):
    sala: str
    livre: bool
    conflitos: list[ConflitoOut]


class ReservaOut(BaseModel):
    reserva: str | None = None
    reservado: bool = True
    sala: str | None = None
    inicio: str | None = None
    fim: str | None = None
    responsavel: str | None = None
    politica: str | None = None
    motivo: str | None = None


def log(mensagem: str) -> None:
    """Sempre em stderr: stdout fica livre e o 'logging' do protocolo e depreciado."""
    print(mensagem, file=sys.stderr, flush=True)


def _modelo_escolha(alternativas: list[str]) -> type[BaseModel]:
    """Formulario plano: um unico campo `sala` com as alternativas, na ordem."""
    tipo = Literal[tuple(alternativas)]  # type: ignore[valid-type]
    return create_model(
        "EscolhaDeSala",
        sala=(tipo, Field(description="Sala alternativa escolhida", title="Sala")),
    )


def criar_servidor(dominio: Dominio, segredo: str) -> MCPServer:
    mcp = MCPServer(
        name="central-de-salas",
        version="1.0.0",
        request_state_security=RequestStateSecurity(keys=[segredo], ttl=config.TTL_REQUEST_STATE_S),
    )

    async def registrar_request(ctx: Any, call_next: Any) -> Any:
        params = ctx.params or {}
        meta = params.get("_meta") or {}
        log(f"[mcp] metodo={ctx.method} id={ctx.request_id} traceparent={meta.get('traceparent', '-')}")
        return await call_next(ctx)

    mcp.middleware.insert(0, registrar_request)

    @mcp.resource(POLITICA_URI, mime_type="text/markdown", name="politica-de-uso")
    def politica() -> str:
        return dominio.politica_texto

    @mcp.tool(description="Lista todas as salas com capacidade e recursos.")
    def listar_salas() -> ListaDeSalas:
        return ListaDeSalas(
            salas=[SalaOut(id=s.id, nome=s.nome, capacidade=s.capacidade, recursos=list(s.recursos)) for s in dominio.salas]
        )

    @mcp.tool(description="Diz se uma sala esta livre no intervalo, e quais reservas conflitam.")
    def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
        try:
            s, t_ini, t_fim = dominio.validar(sala, inicio, fim)
        except ErroDeDominio as e:
            raise ToolError(str(e)) from None
        conflitos = dominio.conflitos(s.id, t_ini, t_fim)
        return Disponibilidade(
            sala=s.id,
            livre=not conflitos,
            conflitos=[ConflitoOut(id=r.id, inicio=r.inicio, fim=r.fim, responsavel=r.responsavel) for r in conflitos],
        )

    def escolha_de_sala(sala: str, inicio: str, fim: str) -> Elicit[Any] | None:
        """Resolver: so pergunta quando ha conflito (e so entao exige elicitation)."""
        try:
            s, t_ini, t_fim = dominio.validar(sala, inicio, fim)
        except ErroDeDominio as e:
            raise ToolError(str(e)) from None
        if not dominio.conflitos(s.id, t_ini, t_fim):
            return None
        alternativas = dominio.alternativas(s, t_ini, t_fim)
        if not alternativas:
            raise ToolError(ERRO_SEM_ALTERNATIVA)
        return Elicit(
            "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
            _modelo_escolha(alternativas),
        )

    @mcp.tool(description="Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.")
    def reservar_sala(
        sala: str,
        inicio: str,
        fim: str,
        responsavel: str,
        escolha: Annotated[ElicitationResult[Any], Resolve(escolha_de_sala)],
    ) -> ReservaOut:
        destino = sala
        if isinstance(escolha, AcceptedElicitation):
            if escolha.data is not None:
                destino = escolha.data.sala
        else:  # decline / cancel: conclui sem reservar e sem erro
            return ReservaOut(reservado=False, motivo="recusado")
        try:
            _, t_ini, t_fim = dominio.validar(destino, inicio, fim)
            if dominio.conflitos(destino, t_ini, t_fim):
                raise ToolError(ERRO_SEM_ALTERNATIVA)
            reserva = dominio.reservar(destino, inicio, fim, responsavel)
        except ErroDeDominio as e:
            raise ToolError(str(e)) from None
        return ReservaOut(
            reserva=reserva.id,
            reservado=True,
            sala=reserva.sala,
            inicio=reserva.inicio,
            fim=reserva.fim,
            responsavel=reserva.responsavel,
            politica=dominio.versao_politica,
        )

    return mcp


def main() -> None:
    segredo = config.segredo_request_state()  # falha na subida se ausente/curto
    dominio = Dominio.carregar(config.dir_dados())
    mcp = criar_servidor(dominio, segredo)
    log(f"[mcp] central-de-salas em http://{config.host()}:{config.porta()}/mcp (politica {dominio.versao_politica})")
    mcp.run("streamable-http", host=config.host(), port=config.porta(), stateless_http=True, json_response=True)
