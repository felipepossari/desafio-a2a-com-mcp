# A Ponte: agente A2A com MCP por dentro

Dois processos separados que conversam por HTTP:

| Processo | Porta | Endpoints | Papel |
|---|---|---|---|
| `servidor-mcp/` | 7301 | `/mcp` (Streamable HTTP) | Servidor MCP `2026-07-28`: 3 tools, 1 resource, MRTR com `requestState` selado |
| `agente/` | 7300 | `/a2a` (JSON-RPC), `/.well-known/agent-card.json` | Servidor A2A v1.0 por fora, cliente MCP por dentro |

Python 3.10+ e [`uv`](https://docs.astral.sh/uv/). Dependencias com versao exata e `uv.lock` commitado
(`mcp==2.3.0`, `httpx==0.28.1`, `starlette==1.7.0`, `uvicorn==0.54.0`). Nenhum SDK de LLM.

## Como rodar

Em um clone limpo. Gere o segredo do `requestState` (minimo 32 bytes) e exporte-o no terminal do servidor MCP (o agente nao usa o segredo).

```bash
# Terminal 1: servidor MCP (stderr visivel: metodo, id e traceparent de cada request)
export REQUEST_STATE_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
cd servidor-mcp && uv sync && uv run python -m central_de_salas

# Terminal 2: agente A2A
cd agente && uv sync && uv run python -m agente

# Terminal 3: validador (rode sempre com os dois processos recem-iniciados)
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Sem `REQUEST_STATE_SECRET` (ou com menos de 32 bytes) o servidor MCP **nao sobe** e explica como gerar a chave.
Variaveis opcionais: `MCP_PORT` (7301), `AGENTE_PORT` (7300), `MCP_URL` (`http://127.0.0.1:7301/mcp`, lido pelo agente),
`AGENTE_URL_PUBLICA` (URL anunciada no card), `DADOS_DIR` (padrao: `dados/` do repositorio). Veja `.env.example`.

Atalho que sobe/reinicia os dois em segundo plano: `scripts/reiniciar.sh` (logs em `/tmp`). Testes unitarios: `uv run pytest` dentro de cada pasta.

## Onde a ponte acontece

Em `agente/src/agente/ponte.py`. A funcao `_aplicar_resultado` recebe o resultado do `tools/call` e, quando
`resultType == "input_required"`, extrai a chave unica de `inputRequests` e as alternativas (`enum`/`const` do campo `sala`),
**guarda `requestState` e a chave na `Task`** (`tasks.py`, campos privados que nunca vao para o cliente A2A) e coloca a Task em
`TASK_STATE_INPUT_REQUIRED` com a mensagem `alternativas: a, b`. Na continuacao `escolha=<id|recusar>`, `continuar()` le a Task
por `taskId`, monta `inputResponses` com a **mesma chave**, devolve o `requestState` **byte a byte** (sem abrir) junto dos argumentos
originais e chama `ClienteMCP.chamar_tool`, que gera um **id JSON-RPC novo** (`cliente_mcp.py`) e reaproveita o trace-id da Task.
O `tools/list` e o resource `politica://uso` sao lidos uma vez, antes do primeiro `tools/call`.

## Decisoes tecnicas

- **Protecao do `requestState`**: do proprio SDK (`mcp.server.request_state.RequestStateSecurity`). O estado e cifrado e autenticado com
  AES-256-GCM (chave derivada por HKDF-SHA256 de `REQUEST_STATE_SECRET`) e vinculado ao metodo, a tool e ao hash dos argumentos.
  Adulterado, expirado ou apresentado com argumentos diferentes vira `-32602`. A resposta de `reservar_sala` tambem so aceita como escolha
  uma das alternativas que foram perguntadas (o schema do formulario e um `Literal`).
- **Validade**: 10 minutos (`TTL_REQUEST_STATE_S` em `config.py`). Nao ha estado em memoria entre a pergunta e o retry, entao o retry
  sobrevive a reinicio do servidor desde que a mesma chave seja usada (testado: pausar, matar o servidor, subir de novo, concluir).
- **MRTR**: `reservar_sala` tem um parametro `Resolve(escolha_de_sala)`; o resolver so pergunta quando ha conflito, por isso a
  capability de elicitation (form) so e exigida nesse caminho (`-32021` + HTTP 400 vem do SDK). Sem alternativa, e erro de execucao.
- **Estado das Tasks**: dicionario em memoria no processo do agente (`Repositorio`), com um `asyncio.Lock` por Task. Cada Task tem o seu
  `requestState`; nao ha variavel global de "ultima pausa". Reiniciar o agente perde as Tasks (persistencia esta fora de escopo).
- **Cliente MCP**: `httpx` cru, sem callback de elicitation de SDK, porque o agente precisa enxergar o `input_required`. Sem sessao:
  todo request leva `_meta` completo (`protocolVersion`, `clientInfo`, `clientCapabilities` com elicitation form, `traceparent`).
- **A2A sem `a2a-sdk`**: implementei o binding JSON-RPC (`SendMessage`, `GetTask`) e o card diretamente com Starlette. Sao dois metodos e o
  formato exato do wire e cobrado pelo validador; nao avaliei o `a2a-sdk` a fundo, so escolhi ter controle total da maquina de estados.
- **Mensagens de erro de tool**: o SDK prefixa erros de execucao com `Error executing tool <nome>: `. O texto da regra vem inteiro depois do
  prefixo e e propagado ao pe da letra para a Task `FAILED` (status e historico). O validador compara por contencao.
- **Trace**: o agente le `traceparent` do `SendMessage`, guarda por Task e usa o mesmo trace-id (span novo) em cada request MCP.
- **Dados**: `dados/` e lido do repositorio, somente leitura; reservas novas ficam so em memoria (`res-0003`, `res-0004`, ...).

## Saida do validador

```
trace-id desta execucao: b4922fb645d0c28f04f561f4aff13b01
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
