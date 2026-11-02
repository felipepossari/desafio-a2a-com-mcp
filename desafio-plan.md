# Plano de execução: "A Ponte" (agente A2A com MCP por dentro)

> Documento de planejamento. Nenhum código de aplicação foi escrito. Este arquivo é apoio de trabalho e pode ficar na raiz (o enunciado permite arquivos de apoio na raiz).

## 1. Resumo do que precisa ser entregue

Dois processos separados, falando por HTTP:

| Processo | Porta | Endpoint | Papel |
|---|---|---|---|
| Servidor MCP (`servidor-mcp/`) | `7301` | `/mcp` (Streamable HTTP) | 3 tools, 1 resource, erros de execução e de protocolo, MRTR com `requestState` assinado |
| Agente (`agente/`) | `7300` | `/a2a` (JSON-RPC) e `/.well-known/agent-card.json` | Servidor A2A por fora, host MCP por dentro, com a ponte `input_required` <-> `TASK_STATE_INPUT_REQUIRED` |

Mais: README substituído (4 seções), validador passando 36/36, repositório público (fork do starter) com tudo na `main`.

## 2. Restrições e tecnologias obrigatórias (checklist permanente)

**Tecnologias**
- SDK MCP **v2**, revisão de spec `2026-07-28`: `@modelcontextprotocol/server` (Node 20+, ESM) **ou** pacote `mcp` do Python (3.10+).
- A2A **v1.0**, binding **JSON-RPC 2.0 sobre HTTP**.
- Python 3.10+ para o validador (stdlib apenas).
- **Versões travadas** em `package.json` ou `pyproject.toml` (pin exato, com lockfile commitado).

**Não negociável**
1. Dois processos separados; o agente fala com o MCP por HTTP (nunca importa a função da tool).
2. O agente **não** tem regra de negócio de sala: conflito, política e alternativas são do servidor MCP.
3. **Sem sessão**: nenhum lado infere versão/capabilities/contexto de request anterior (manter objeto cliente vivo é permitido).
4. `requestState` é **opaco** para o agente: guardar e ecoar, nunca abrir/interpretar/reconstruir.
5. **Sem LLM** no caminho de execução; nenhum SDK de provedor de LLM nas dependências.
6. Limitação real de SDK: documentar no README com evidência, em vez de reescrever o protocolo.
7. **Proibido alterar** `dados/`, `validador/`, `exemplos/`.
8. Segredo `REQUEST_STATE_SECRET` só por variável de ambiente (>= 32 bytes), nunca hardcoded e nunca com o valor real no README.
9. Fora de escopo (não fazer): auth, streaming/SSE, push, `referenceTaskIds`, card estendido/JWS, prompts/subscriptions/progress, container/compose, OpenTelemetry (só propagar e logar `traceparent`), concorrência de escrita, persistência de reservas em disco, UI.

## 3. Decisões de arquitetura

### 3.1 Linguagem e SDKs: Python
- Os exemplos de wire (`__main__:escolha_de_sala`, "resolver '...'" na mensagem de -32021) foram capturados do SDK Python, então seguir Python minimiza divergência de formato.
- Versões disponíveis verificadas em 02/11/2026: `mcp` 2.3.0 (série 2.x estável), `a2a-sdk` 1.3.0 (série 1.x = A2A v1.0).
- **Pin exato** no `pyproject.toml` (ex.: `mcp==2.3.0`), um `pyproject.toml` por processo (`servidor-mcp/` e `agente/`), com `uv.lock` commitado. Python local é 3.14, o piso declarado é `>=3.10`.
- **Spike obrigatório (Fase 0)** antes de fixar: confirmar que `mcp==2.3.0` fala a revisão `2026-07-28` (campos `_meta` por request, `resultType`, `input_required`, `-32020/-32021`). Se a 2.3.0 não bastar, subir para a menor 2.x que bastar e travar essa. Se o Python falhar de forma bloqueante, o plano B é Node (`@modelcontextprotocol/server` 2.4.0), registrando a razão.
- **Lado A2A**: avaliar `a2a-sdk` 1.3.0. Se ele atender a v1.0 (card com `supportedInterfaces`, `SendMessage`, `GetTask`, `TASK_STATE_*`, `taskId` na mensagem, erro em tarefa terminal) sem esconder o controle da máquina de estados, usar. Caso contrário, implementar o binding JSON-RPC à mão com FastAPI/Starlette (é pequeno: 2 métodos) e documentar. A formatação do wire é cobrada pelo validador, então vale mais o controle do que a conveniência.

### 3.2 Servidor MCP (porta 7301)
- Lê `dados/salas.json`, `dados/reservas.json`, `dados/politica-de-uso.md` **do repositório (read-only)**; caminho resolvido relativo ao arquivo, parametrizável por env, sem copiar os dados.
- Reservas em memória (lista inicial = `reservas.json`; novas reservas com ids sequenciais `res-0003`, `res-0004`, ...).
- Lógica de domínio isolada em módulo puro (`dominio`): validar sala, janela de uso 08:00–20:00 (-03:00), duração <= 2h, intervalo válido, conflito (sobreposição), alternativas (livres, capacidade >= pedida, no máximo 3, ordenadas por capacidade asc e `id` asc). Testável sem rede.
- MRTR de primeira classe do SDK (procurar `input_required`/resolvers; **nunca** `ctx.elicit()` síncrono, que gera o erro "sem canal de volta").
- `requestState`: **HMAC-SHA256 ou AEAD** com `REQUEST_STATE_SECRET`, usando o utilitário do SDK se existir (a spec e o enunciado dizem que os SDKs oferecem um). Payload sela `tool`, `arguments` originais (sala, inicio, fim, responsavel), alternativas oferecidas, `exp` (TTL **10 min**, dentro de 5–30). Verificação: assinatura (comparação em tempo constante), expiração, e nome da tool. Falhou -> `-32602`.
- Retry: reconstrói o pedido **a partir do estado selado**; ignora/rejeita `arguments` divergentes (escolho: usar os valores selados, e opcionalmente rejeitar divergência). Valida que a sala escolhida está entre as alternativas seladas (defesa contra `inputResponses` forjado).
- Stateless de verdade: nada em memória entre o `input_required` e o retry; só a chave vem do ambiente, então o retry sobrevive a restart.
- Validação de `_meta` obrigatório (`protocolVersion`, `clientCapabilities`) -> `-32602` + HTTP 400, sem cache de request anterior. Capability de elicitation em form mode ausente num conflito -> `-32021` + HTTP 400 com `data.requiredCapabilities`.
- Log em **stderr** para cada request: método, id, `traceparent` (do `_meta`). Nunca em stdout, nunca via "logging" depreciado do protocolo.
- Segredo ausente ou com menos de 32 bytes: **falha na subida** com mensagem clara.

### 3.3 Agente (porta 7300)
- **Cliente MCP "cru"**: o agente precisa enxergar o `input_required`. Não usar callback de elicitation do cliente do SDK. Duas opções, decidir no spike: (a) API de baixo nível do cliente do SDK que devolva o resultado `input_required` sem resolver sozinho; (b) cliente HTTP próprio com `httpx` montando os requests (muito fácil: o formato está nos 11 exemplos de wire). A opção (b) é a mais previsível e continua sendo "cliente MCP de verdade por HTTP".
- Todo request MCP do agente: headers `Content-Type`, `Accept: application/json, text/event-stream`, `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (em `tools/call` e `resources/read`); `_meta` com `protocolVersion`, `clientInfo`, `clientCapabilities = {"elicitation":{"form":{}}}` e `traceparent`.
- Descoberta: `tools/list` uma vez antes do primeiro `tools/call` (cacheado no objeto cliente, o que é permitido) e leitura do resource `politica://uso` (extrai a versão da 1ª linha: `versao: 2026-11-01`). Os nomes das tools usadas vêm da descoberta (não de lista fixa de schemas), validando que `reservar_sala` existe.
- Máquina de estados da Task em memória (`dict[task_id] -> Task`), cada Task com seu `requestState`, `inputRequests` key, chamada original (nome da tool + argumentos como string opaca para o agente), `traceparent`, histórico, artifacts. Estado terminal é definitivo.
- Parse do pedido de formato fixo `reservar sala=<id> inicio=<iso> fim=<iso> responsavel=<nome>` e de `escolha=<id|recusar>`. **Sem regra de domínio**: o agente só extrai campos; quem valida é o MCP (inclusive sala inexistente).
- Propagação de `traceparent`: lê do header do `SendMessage`, guarda por Task, injeta o mesmo trace-id em todos os requests MCP da Task (span-id novo por request, trace-id igual). Na continuação, usa o `traceparent` guardado se o header não vier.

### 3.4 A ponte (o ponto central)
```
SendMessage(texto) ──► cria Task (SUBMITTED -> WORKING)
                      └─► tools/call reservar_sala  (id N)
                           ├─ complete, isError=false  ─► artifact "reserva" + COMPLETED
                           ├─ complete, isError=true   ─► FAILED (mensagem exata da tool no histórico)
                           └─ input_required ──────────► guarda {requestState, inputRequests key} NA Task
                                                          Task -> INPUT_REQUIRED, mensagem "alternativas: a, b"
SendMessage(taskId, "escolha=X") ──► localiza Task (por taskId; nunca por contexto global)
                      ├─ X fora do enum  ─► mantém INPUT_REQUIRED, repete "alternativas: ..."
                      ├─ X = recusar      ─► tools/call (id N+k, action=decline) ─► CANCELED
                      └─ X ∈ enum         ─► tools/call (id N+k NOVO, action=accept, mesma chave, requestState ecoado) ─► COMPLETED
```
Pontos de atenção: id JSON-RPC **novo** no retry; `requestState` nunca sai do processo do agente para o cliente A2A; estado indexado por Task (as duas Tasks pausadas simultâneas não se misturam).

## 4. Fases e tarefas

Cada tarefa tem um **critério de pronto** verificável. A ordem segue a "ordem de execução sugerida" do enunciado.

### Fase 0: Preparação e spike (sem código de aplicação)
- **T0.1** Fork do starter para a conta do aluno (repositório público). Clonar. *Pronto:* remoto `origin` aponta para o fork; `main` limpa.
- **T0.2** Ler `exemplos/wire/` inteiro (já mapeado: ver §6) e as 36 checagens do `validador/validar.py`. *Pronto:* tabela de verificações do §5 preenchida.
- **T0.3** Spike MCP: criar ambiente temporário (fora do repo) com `mcp==2.3.0`; confirmar `input_required`, resolvers/MRTR, utilitário de assinatura de `requestState`, validação de `_meta`, `-32020`, `-32021`, nomes de campo. Registrar nomes reais da API. *Pronto:* decisão escrita sobre versão/linguagem e sobre o utilitário de `requestState`.
- **T0.4** Spike A2A: avaliar `a2a-sdk==1.3.0` (card v1.0, binding JSON-RPC, `TASK_STATE_*`, controle do estado). *Pronto:* decisão "SDK" ou "JSON-RPC manual" registrada; se manual, anotar no README (item 6 das restrições).
- **T0.5** Definir estrutura de pastas e scripts (§7), `.gitignore` já cobre `.env`, `.venv`, `__pycache__`, `node_modules`, `dist`, `*.log`; adicionar `.env.example` **sem** segredo. *Pronto:* árvore combinada.

### Fase 1: Servidor MCP, esqueleto e `listar_salas`
- **T1.1** Projeto `servidor-mcp/` com `pyproject.toml` (versões travadas, `requires-python >=3.10`) e lockfile. Servidor Streamable HTTP em `0.0.0.0/127.0.0.1:7301`, endpoint `/mcp`, porta parametrizável por env com **default 7301**.
- **T1.2** Capabilities `tools` e `resources`; `serverInfo` (`central-de-salas`/`1.0.0`).
- **T1.3** Middleware de validação de `_meta` (protocolVersion + clientCapabilities obrigatórios): `-32602` + HTTP 400.
- **T1.4** Log em stderr por request: método, id, `traceparent`.
- **T1.5** `listar_salas`: `inputSchema` objeto vazio, `outputSchema` declarado, `structuredContent` + mesmo JSON em bloco de texto.
- **T1.6** Verificação manual (curl ou MCP Inspector) comparando com `01-tools-list.json`. *Pronto:* validador 3, 4, 5 passam.

### Fase 2: Domínio, `consultar_disponibilidade` e resource
- **T2.1** Módulo de domínio puro: carga de dados, janela de uso (08:00–20:00 -03:00 para início e para fim), duração <= 2h, intervalo `fim > inicio`, sala existente, sobreposição, alternativas. Atenção a fuso (datetimes com offset; comparar em UTC ou convertido para -03:00) e a bordas (fim às 20:00 permitido; sobreposição estrita, ou seja, `[14:00,15:00]` e `[15:00,16:00]` **não** conflitam).
- **T2.2** Ordem de validação coerente para bater com o validador: sala -> intervalo invertido/vazio -> janela -> duração (a definir sem ambiguidade; ver nota em §8, R2).
- **T2.3** Os 5 erros de execução com **texto exato** (`isError: true` dentro de `resultType: complete`).
- **T2.4** `consultar_disponibilidade`: `{livre: bool, conflitos: [...]}` + mesmos erros.
- **T2.5** Resource `politica://uso` (conteúdo literal do arquivo, `mimeType: text/markdown`); URI inexistente -> `-32602` (nunca `contents` vazio). Tool inexistente -> `-32602` (ou `isError`).
- **T2.6** Testes unitários do domínio (pytest, dependência de **dev**, não de runtime). *Pronto:* validador 1–12 passam.

### Fase 3: `reservar_sala` (caminho feliz e erros)
- **T3.1** `reservar_sala` livre: cria reserva em memória (`res-NNNN` sequencial), devolve `structuredContent` com `reserva, reservado, sala, inicio, fim, responsavel, politica, motivo` (+ bloco de texto), `politica` = versão lida do arquivo.
- **T3.2** Reservas criadas ficam visíveis em `consultar_disponibilidade` e em conflitos seguintes.
- **T3.3** Conflito sem alternativas -> `isError: true` com `Sem alternativas disponiveis no intervalo`. *Pronto:* validador 20 (parcial, depende de MRTR).

### Fase 4: MRTR no servidor
- **T4.1** Geração do `REQUEST_STATE_SECRET` (comando documentado), leitura por env, falha na subida se ausente/curto. Sem fallback.
- **T4.2** Conflito com alternativas: `resultType: input_required`, `inputRequests` com **uma** entrada `elicitation/create` em `mode: form`, `requestedSchema` plano (`sala` string com `enum` na ordem certa; `const` se só uma alternativa é aceito), `requestState` assinado + TTL.
- **T4.3** Sem capability de elicitation form no `_meta`: `-32021` + `data.requiredCapabilities` + HTTP 400 (cobrar **só** no caminho que exige elicitation; reserva livre não exige).
- **T4.4** Retry: lê `inputResponses` e `requestState` de `params`; verifica integridade e expiração; reconstrói o pedido dos valores selados; valida escolha contra alternativas seladas; reserva; devolve `complete`. Adulterado/expirado -> `-32602` (não 500).
- **T4.5** `decline`/`cancel`: `complete`, sem `isError`, `reservado: false`, `motivo` ("recusado"), campos nulos como em `11-tools-call-retry-recusa.json`.
- **T4.6** Argumentos divergentes no retry não surtem efeito (usar selados, validado pelo caso do validador 18).
- **T4.7** Teste de restart: gerar `input_required`, matar o processo, subir de novo com a mesma chave, retry funciona. *Pronto:* validador 1–20 passam.

### Fase 5: Agente como cliente MCP puro (sem A2A ainda)
- **T5.1** Módulo cliente MCP (`httpx`): monta headers espelhados e `_meta`; id JSON-RPC incremental **por request**; expõe resultado bruto (`complete` | `input_required` | erro RPC).
- **T5.2** Fluxo de descoberta: `tools/list` antes do primeiro `tools/call`; `resources/read politica://uso`; parse da versão na primeira linha.
- **T5.3** Script de linha de comando de teste (descartável ou em `agente/scripts/`) que completa o ciclo de MRTR respondendo a mão. *Pronto:* log do servidor mostra `tools/list`, `tools/call` (id A), `tools/call` (id B != A) com `traceparent`.

### Fase 6: Agente como servidor A2A
- **T6.1** Servidor HTTP na porta 7300 (default) com `GET /.well-known/agent-card.json` conforme `07-a2a-agent-card.json` (campo `supportedInterfaces[].protocolBinding = "JSONRPC"`, `protocolVersion = "1.0"`, URL do `/a2a`, skill `reservar-sala`, `streaming:false`, sem `securitySchemes` preenchido). URL do card parametrizável por env (host/porta).
- **T6.2** `POST /a2a` JSON-RPC 2.0: `SendMessage`, `GetTask`; erros JSON-RPC para método desconhecido, params inválidos, Task inexistente, Task terminal.
- **T6.3** Modelo de Task: `id`, `contextId` próprios, `status.state` + `status.message`, `history`, `artifacts`; transita `SUBMITTED -> WORKING -> {COMPLETED|FAILED|INPUT_REQUIRED}`; `GetTask` reflete estado corrente.
- **T6.4** Sala livre -> `COMPLETED` com artifact `reserva` (conteúdo JSON: `reserva, sala, inicio, fim, responsavel, politica`; ver exemplo 10) e mensagem final de confirmação.
- **T6.5** `isError: true` da tool -> `FAILED` com o texto exato da tool na mensagem de status **e** no histórico.
- **T6.6** `SendMessage` com `taskId` de Task terminal ou inexistente -> erro JSON-RPC.

### Fase 7: A ponte
- **T7.1** Ao receber `input_required`: extrair a chave única de `inputRequests` e o `enum` de `requestedSchema.properties.sala` (**sem** abrir o `requestState`); guardar `{requestState, chave, tool, argumentos originais, traceparent}` na Task; estado `INPUT_REQUIRED`; mensagem de status **exatamente** `alternativas: <ids separados por ", ">` na ordem do enum, sem prefixo (o `const` com uma só alternativa também deve virar `alternativas: x`).
- **T7.2** Continuação `escolha=<valor>` com `taskId`: parse; fora do enum -> permanece `INPUT_REQUIRED` e repete a lista; dentro -> retry MCP com **id novo**, mesma chave em `inputResponses` (`{"action":"accept","content":{"sala":<valor>}}`), `requestState` ecoado byte a byte, mesmos argumentos originais e `traceparent` da Task.
- **T7.3** `escolha=recusar` -> retry com `{"action":"decline"}` e Task `CANCELED`.
- **T7.4** O `requestState` nunca aparece em: card, artifacts, mensagens, histórico, `GetTask`, erros e logs do agente (log: não registrar o valor).
- **T7.5** Duas Tasks pausadas ao mesmo tempo, cada uma com seu estado, sem variável global de "última pausa". Teste com pedidos intercalados.
- **T7.6** Se o retry do MCP voltar `-32602` (estado expirado/inválido): Task -> `FAILED` com mensagem clara, sem expor o estado.
- **T7.7** Determinismo: mesmo pedido -> mesmo texto de pausa (sem timestamps/ids/aleatoriedade na mensagem `alternativas:`).

### Fase 8: Integração e validador
- **T8.1** Subir os dois processos do zero; rodar o validador; corrigir; repetir (reiniciar **ambos** a cada execução, pois o estado muda).
- **T8.2** Conferir manualmente o fluxo do avaliador passos 3–13 (curl). Guardar os comandos.
- **T8.3** Conferir no stderr: `tools/list` antes do primeiro `tools/call` do agente; trace-id impresso pelo validador presente; par de `tools/call` com ids diferentes no fluxo com pausa.
- **T8.4** Teste de restart do servidor entre `input_required` e retry (passo 12 do avaliador), também através do agente.
- **T8.5** `git diff --stat main..origin-starter` confirmando que `dados/`, `validador/`, `exemplos/` ficaram intactos.

### Fase 9: README, limpeza e entrega
- **T9.1** Substituir o `README.md` com as 4 seções: **Como rodar**, **Onde a ponte acontece**, **Decisões técnicas**, **Saída do validador** (saída completa, em bloco de código).
- **T9.2** Teste "do zero": novo clone em pasta limpa, seguir só o README (incluindo geração do segredo), rodar o validador e a checklist de critérios de aceite (§5) item a item.
- **T9.3** Varredura de segredo: `grep` por qualquer valor de `REQUEST_STATE_SECRET`/hex longo no repo e no histórico; `.env` não versionado, `.env.example` sem valor real.
- **T9.4** Lockfiles commitados; sem dependência de SDK de LLM (`openai`, `anthropic`, `langchain`, etc.).
- **T9.5** Commit(s) na `main` do fork e `git push`. Conferir no GitHub que o repo é público e a `main` tem tudo.

## 5. Matriz de rastreabilidade: critérios de aceite -> tarefas -> validação

| # | Critério de aceite | Tarefas | Como verificar |
|---|---|---|---|
| S1 | Sobe na 7301 em Streamable HTTP e `tools/list` traz 3 tools | T1.1, T1.5, T2.4, T3.1 | Validador 1 |
| S2 | `inputSchema` objeto JSON Schema válido em toda tool | T1.5, T2.4, T3.1 | Validador 2 |
| S3 | `listar_salas`: `structuredContent` conforme `outputSchema` + texto idêntico | T1.5 | Validador 3 |
| S4 | Sem `protocolVersion`/`clientCapabilities` -> `-32602` + HTTP 400 | T1.3 | Validador 4, 5 |
| S5 | Tool inexistente recusada | T2.5 | Validador 6 |
| S6 | `politica://uso` correto; URI inexistente `-32602` | T2.5 | Validador 7, 8 |
| S7 | 4 erros de execução com mensagem exata | T2.3 | Validador 9–12 |
| S8 | stderr com método, id e `traceparent` | T1.4 | Inspeção manual (passo 5) |
| M1 | Conflito -> `input_required`, 1 entrada, form, `requestState` | T4.2 | Validador 13 |
| M2 | `requestedSchema` plano, alternativas na ordem | T2.1, T4.2 | Validador 14 |
| M3 | Sem elicitation -> `-32021` + `requiredCapabilities` + 400 | T4.3 | Validador 15 |
| M4 | Retry conclui, `resultType: complete` | T4.4 | Validador 16 |
| M5 | `requestState` adulterado -> `-32602` | T4.4 | Validador 17 |
| M6 | Argumentos adulterados no retry sem efeito | T4.4, T4.6 | Validador 18 |
| M7 | Retry válido após restart do servidor | T4.1, T4.7 | Manual (passo 12) |
| M8 | Chave vem de `REQUEST_STATE_SECRET` (>= 32 bytes), sem hardcode | T4.1, T9.3 | Revisão de código + grep |
| M9 | `decline` conclui sem reservar e sem `isError` | T4.5 | Validador 19 |
| M10 | (extra do validador) conflito sem alternativa -> `isError` | T3.3 | Validador 20 |
| H1 | Log mostra `tools/list` antes do 1º `tools/call` | T5.2 | Inspeção do stderr (passo 5) |
| H2 | Log mostra `traceparent` com o trace-id do validador | T5.1, T6.x | Inspeção do stderr |
| H3 | Requests do agente declaram elicitation form | T5.1 | Inspeção do stderr / código |
| A1 | Card 200 e válido v1.0 | T6.1 | Validador 21 |
| A2 | Card com interface JSONRPC, `1.0`, skill `reservar-sala` | T6.1 | Validador 22, 23 |
| A3 | Sala livre -> `COMPLETED` + artifact `reserva` | T6.4 | Validador 24 |
| A4 | Artifact com a reserva e `politica` lida do resource | T5.2, T6.4 | Validador 25 |
| A5 | `GetTask`: `id`, `contextId`, estado, artifact | T6.3 | Validador 26 |
| A6 | `SendMessage` em Task terminal recusado | T6.6 | Validador 31 |
| A7 | Sala inexistente -> `FAILED` com mensagem exata | T6.5 | Validador 35 |
| P1 | Sala ocupada -> `INPUT_REQUIRED` + `alternativas:` ordenadas | T7.1 | Validador 27, 28 |
| P2 | `escolha=<id>` -> `COMPLETED` na sala escolhida | T7.2 | Validador 30 |
| P3 | `escolha=recusar` -> `CANCELED` | T7.3 | Validador 32 |
| P4 | Escolha fora do enum mantém `INPUT_REQUIRED` | T7.2 | Validador 29 |
| P5 | Duas Tasks pausadas, sem trocar `requestState` | T7.5 | Validador 33 |
| P6 | `requestState` ausente de qualquer resposta A2A | T7.4 | Validador 34 |
| V1 | Validador 36/36, exit 0, processos recém-iniciados | T8.1 | Execução final |
| V2 | `dados/`, `validador/`, `exemplos/` intactos | T8.5 | `git diff` |
| V3 | Agente determinístico, sem SDK de LLM | T7.7, T9.4 | Validador 36 + revisão de dependências |
| V4 | README com 4 seções, comandos funcionam em clone limpo | T9.1, T9.2 | Teste do zero |

## 6. Contrato extraído dos exemplos de wire (referência rápida)

- Headers MCP: `Content-Type: application/json`, `Accept: application/json, text/event-stream`, `MCP-Protocol-Version: 2026-07-28`, `Mcp-Method`, `Mcp-Name` (só em `tools/call`/`resources/read`).
- `_meta` do request: `io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientInfo` (`agente-central-de-salas` / `1.0.0`), `io.modelcontextprotocol/clientCapabilities`, `traceparent`.
- Resposta `complete` inclui `resultType: "complete"`, `content[0].text` (JSON indentado), `structuredContent`, `isError`, `_meta."io.modelcontextprotocol/serverInfo"`.
- `tools/list`/`resources/read` trazem `cacheScope: "private"`; `resources/read` traz também `ttlMs: 0`.
- `structuredContent` de reserva sempre tem as 8 chaves: `reserva, reservado, sala, inicio, fim, responsavel, politica, motivo` (nulos na recusa; `motivo: "recusado"`).
- `-32021`: HTTP 400, `data.requiredCapabilities = {"elicitation":{"form":{}}}`.
- A2A: `SendMessage` retorna `result.task`; mensagens têm `messageId`, `role` (`ROLE_USER`/`ROLE_AGENT`), `parts[{text}]`, `taskId`/`contextId` nas do agente; `history` completo; `artifacts` (`artifactId`, `name`, `parts`); mensagem de COMPLETED é `Reserva <id> confirmada na <sala>.`.

## 7. Estrutura proposta do repositório

```
.
├── README.md                    # substituído (4 seções)
├── desafio-plan.md              # este plano (pode permanecer ou ser removido na entrega)
├── .env.example                 # sem segredo
├── Makefile (ou scripts/*.sh)   # subir servidor, subir agente, rodar validador
├── dados/        (intocado)
├── validador/    (intocado)
├── exemplos/     (intocado)
├── servidor-mcp/
│   ├── pyproject.toml           # versões travadas
│   ├── uv.lock
│   ├── src/central_de_salas/{__main__,server,dominio,estado,config}.py
│   └── tests/
└── agente/
    ├── pyproject.toml
    ├── uv.lock
    ├── src/agente/{__main__,a2a,cliente_mcp,tasks,ponte,pedido}.py
    └── tests/
```
Comandos de subida (a confirmar no spike): `cd servidor-mcp && uv run ...` e `cd agente && uv run ...`; alternativa sem `uv` com `python -m venv` + `pip install -r`/lock, para o avaliador conseguir seguir só o README. O `.gitkeep` das pastas pode ser removido ao adicionar arquivos.

## 8. Riscos e pontos de atenção

| # | Risco | Mitigação |
|---|---|---|
| R1 | SDK 2.x não ter exatamente a API esperada para MRTR/`requestState` | Spike T0.3; se limitação real, documentar com evidência (restrição 6) |
| R2 | Ordem de validação divergir do validador (ex.: 07:00–08:00 é "fora da janela"; 09:00–12:00 é "duração"; 10:00→09:00 é "invertido") | Cada caso do validador tem exatamente um erro possível; definir ordem: sala, intervalo, janela, duração; testes unitários cobrem os 4 casos |
| R3 | Cliente MCP do SDK resolver a elicitation sozinho e fechar o ciclo | Usar cliente HTTP cru/nível baixo (§3.3) |
| R4 | Reutilizar o id JSON-RPC no retry | Gerador de ids por request no cliente; teste que compara os ids no log |
| R5 | Vazamento de `requestState` em resposta A2A (validador 34 varre os primeiros 40 caracteres) | Nunca incluir o estado em objetos serializados para o cliente; não logar |
| R6 | Validador 36 depende de `sala-porao 9h-10h` já reservada na checagem 24 e de resultado idêntico entre duas chamadas | Estado em memória do servidor; reiniciar os dois processos a cada rodada; pausa sem dados variáveis |
| R7 | `-32021` disparado no caso errado (ex.: reserva livre de cliente sem capability) | Só exigir a capability quando a resposta seria `input_required` (conflito com alternativas) |
| R8 | Chave curta ou ausente, ou valor real vazando no README/histórico | Falha na subida; `.env` ignorado; grep final; README só com o comando de geração |
| R9 | Comparação de datas com fuso/borda (20:00, 08:00, fim==início de outra reserva) | Datetimes com offset, testes de borda |
| R10 | Versão do Python do avaliador (3.10) vs 3.14 local | `requires-python >=3.10`; evitar sintaxe >3.10; testar com 3.10 se possível (`uv run --python 3.10`) |
| R11 | Dois processos concorrentes sobre dicionário de Tasks | Bloqueio simples (`asyncio.Lock`) por Task ou atualização atômica; concorrência de escrita é fora de escopo, mas o estado por Task é obrigatório |
| R12 | Comandos do README não funcionarem em clone limpo | T9.2 obrigatório, copiando e colando os comandos do próprio README |

## 9. Revisão contra os critérios de aceite

Revisão feita após a escrita do plano, confrontando cada item do README (requisitos 1–6, restrições, critérios de aceite e fluxo do avaliador).

**Cobertura:** todos os 37 itens de critério de aceite constam na matriz do §5 com tarefa e forma de verificação. Os requisitos das seções 1–6 do enunciado mapeiam para as Fases 1–9.

**Não conformidades encontradas e correções aplicadas ao plano:**

1. **Rejeição de `arguments` divergentes**: o plano original só dizia "usar selados". Mantido, e acrescentada em T4.4 a validação da sala escolhida contra as alternativas **seladas** (impede `inputResponses` forjado escolher uma sala não oferecida).
2. **`-32021` no momento certo (R7)**: faltava explicitar que a checagem só vale quando há elicitation a emitir; reserva livre de cliente sem capability deve funcionar. Incluído em T4.3.
3. **Servidor sem segredo**: o critério exige chave por env com >= 32 bytes. Incluída falha na subida (T4.1) e a validação do tamanho, não só da presença.
4. **Uma única alternativa (`const`)**: o agente precisa extrair as alternativas do `enum` **ou** `const`. Incluído em T7.1.
5. **Estado do agente após falha do retry**: não havia tratamento de `-32602` do MCP na retomada. Incluído T7.6 (Task `FAILED` sem expor estado).
6. **`traceparent` na continuação**: a continuação pode vir sem header; o agente guarda o `traceparent` da Task e reutiliza (T7.2, §3.3).
7. **Log do agente não pode expor `requestState`**: acrescentado em T7.4.
8. **`isError` na mensagem de FAILED**: o critério exige a mensagem "visível no histórico"; T6.5 passou a exigir no status **e** no histórico.
9. **Opacidade do `requestState`**: o agente não deve decodificar nem sequer o prefixo `v1.`; o plano proíbe qualquer parse (R5/T7.1).
10. **Descoberta em runtime (H1)**: o `tools/list` precisa ser anterior ao 1º `tools/call` mesmo após restart do agente; T5.2 faz isso na primeira chamada de cada processo (cache no objeto cliente, permitido), com retentativa se o servidor reiniciar.
11. **Restart do servidor entre pausa e retry com o agente no meio**: adicionado T8.4 para exercitar também pelo agente.
12. **Entrega**: acrescentados T8.5 (provar que `dados/validador/exemplos` estão intactos) e T9.3 (varredura de segredo) para fechar os critérios V2 e M8.

**Pontos que dependem de decisão no spike (não são não conformidades, mas estão rastreados):** versão exata do `mcp` (T0.3), uso de `a2a-sdk` vs JSON-RPC manual (T0.4), e utilitário de assinatura do SDK vs HMAC próprio (T0.3). Em todos os casos o critério de aceite é o mesmo; só muda o caminho.

**Veredito:** com as correções acima o plano atende a todos os critérios de aceite do README e respeita as tecnologias obrigatórias e as restrições não negociáveis. Nenhum item ficou sem tarefa.

## 10. Ordem de execução resumida

`F0 spike -> F1 servidor esqueleto -> F2 domínio/consulta/resource -> F3 reserva feliz -> F4 MRTR -> F5 cliente MCP puro -> F6 A2A -> F7 ponte -> F8 validador/integração -> F9 README e entrega`
