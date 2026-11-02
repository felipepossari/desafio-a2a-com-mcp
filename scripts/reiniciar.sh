#!/usr/bin/env bash
# Sobe (ou reinicia) servidor MCP e agente em segundo plano, logs em /tmp.
# Uso: REQUEST_STATE_SECRET=<>=32 bytes> scripts/reiniciar.sh
set -euo pipefail
raiz="$(cd "$(dirname "$0")/.." && pwd)"
: "${REQUEST_STATE_SECRET:?defina REQUEST_STATE_SECRET (veja .env.example)}"
logs="${LOG_DIR:-/tmp}"
for porta in 7300 7301; do
  pid="$(ss -ltnp 2>/dev/null | awk -v p=":$porta" '$4 ~ p"$" {match($0,/pid=[0-9]+/); print substr($0,RSTART+4,RLENGTH-4)}' | head -1)"
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
done
sleep 1
(cd "$raiz/servidor-mcp" && nohup uv run python -m central_de_salas >"$logs/mcp.log" 2>&1 &)
(cd "$raiz/agente" && nohup uv run python -m agente >"$logs/agente.log" 2>&1 &)
sleep 3
echo "servidor MCP: $logs/mcp.log | agente: $logs/agente.log"
