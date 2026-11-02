"""Configuracao do servidor: tudo vem do ambiente, nada de segredo no codigo."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PORTA_PADRAO = 7301
TTL_REQUEST_STATE_S = 600.0  # 10 minutos, dentro da faixa de 5 a 30 que o enunciado pede
TAMANHO_MINIMO_SEGREDO = 32


def dir_dados() -> Path:
    """Diretorio `dados/` do repositorio (somente leitura). Parametrizavel por DADOS_DIR."""
    env = os.environ.get("DADOS_DIR")
    if env:
        return Path(env).resolve()
    # servidor-mcp/src/central_de_salas/config.py -> raiz do repositorio
    return Path(__file__).resolve().parents[3] / "dados"


def porta() -> int:
    return int(os.environ.get("MCP_PORT", PORTA_PADRAO))


def host() -> str:
    return os.environ.get("MCP_HOST", "127.0.0.1")


def segredo_request_state() -> str:
    """Le REQUEST_STATE_SECRET. Sem fallback: ausente ou curto derruba a subida."""
    segredo = os.environ.get("REQUEST_STATE_SECRET", "")
    if len(segredo.encode()) < TAMANHO_MINIMO_SEGREDO:
        sys.exit(
            "ERRO: REQUEST_STATE_SECRET ausente ou com menos de 32 bytes.\n"
            'Gere um com: python3 -c "import secrets; print(secrets.token_hex(32))"\n'
            "e exporte: export REQUEST_STATE_SECRET=<valor>"
        )
    return segredo
