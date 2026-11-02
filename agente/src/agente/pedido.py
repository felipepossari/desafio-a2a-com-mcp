"""Interpretacao dos pedidos em formato fixo. Sem regra de dominio: so extrai campos."""

from __future__ import annotations

import re

CAMPOS_RESERVA = ("sala", "inicio", "fim", "responsavel")
RECUSAR = "recusar"
_CHAVE = re.compile(r"(?:(?<=\s)|^)([a-z]+)=")


class PedidoInvalido(ValueError):
    pass


def _pares(texto: str) -> dict[str, str]:
    """`k=v k2=v2 ...`: o valor vai ate a proxima chave (permite nomes com espaco)."""
    achados = list(_CHAVE.finditer(texto))
    pares: dict[str, str] = {}
    for i, m in enumerate(achados):
        fim = achados[i + 1].start() if i + 1 < len(achados) else len(texto)
        pares[m.group(1)] = texto[m.end():fim].strip()
    return pares


def parse_reserva(texto: str) -> dict[str, str]:
    """`reservar sala=<id> inicio=<iso> fim=<iso> responsavel=<nome>` -> argumentos da tool."""
    texto = texto.strip()
    if not texto.startswith("reservar"):
        raise PedidoInvalido("Pedido nao reconhecido. Formato: reservar sala=<id> inicio=<iso> fim=<iso> responsavel=<nome>")
    pares = _pares(texto[len("reservar"):].strip())
    faltando = [c for c in CAMPOS_RESERVA if not pares.get(c)]
    if faltando:
        raise PedidoInvalido(f"Pedido incompleto, faltam: {', '.join(faltando)}")
    return {c: pares[c] for c in CAMPOS_RESERVA}


def parse_escolha(texto: str) -> str | None:
    """`escolha=<id|recusar>` -> valor, ou None se a mensagem nao tem esse formato."""
    pares = _pares(texto.strip())
    valor = pares.get("escolha")
    return valor or None
