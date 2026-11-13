# API interna do painel

Base `/api/v1`, JSON. A API é acessada pelo mesmo HTTPS do painel. Não publique 8080. Há um administrador; não há registro nem alteração de senha pela web.

1. `POST /login` com `{"password":"..."}` e cabeçalho `Origin` exatamente igual a `PANEL_ORIGIN`.
2. Guardar o cookie `session` (Secure, HttpOnly, SameSite=Strict) e o `csrf` retornado.
3. Em mutações autenticadas, enviar `Origin` e `X-CSRF-Token`. O cookie autentica leituras e SSE.

| Método e caminho | Entrada / saída |
|---|---|
| GET `/session` | Recupera token CSRF da sessão atual |
| POST `/logout` | Invalida sessão |
| GET `/state` | Estado do jogo, contagem e motivo, builds, operações, últimos 500 logs, histórico de até 720 métricas e proveniência da imagem |
| GET `/events` | SSE a cada cinco segundos, com estado, operações, logs e métrica mais recente; termina ao expirar a sessão |
| GET `/config` | `values`, `sources`, `pending`, opções de modificadores; senha do jogo sempre vazia |
| PATCH `/config` | `{"changes":{"crossplay":true},"reset":[],"confirm_world":false}`; trocar o mundo exige confirmação |
| POST `/actions` | `{"action":"backup"}`; aceita start, stop, restart, update, backup, restore |
| GET `/jobs/{id}` | Consulta resultado da operação retornada com HTTP 202 |
| GET `/backups` | Lista de nomes e tamanhos |
| GET `/backups/{name}` | Download autenticado do tar.gz |
| GET `/users/{kind}` | Listas `admins`, `banned`, `permitted`, como `{"entries":["Steam_..."]}` |
| PUT `/users/{kind}` | Substitui a lista após validar/deduplicar IDs |

Restauração exige `{"action":"restore","backup":"NOME.tar.gz","confirm":true}` e jogo parado. Somente arquivos do diretório de backups podem ser selecionados. Não há upload web nem execução de comandos arbitrários. Configurações e listas não podem ser alteradas durante operação em execução.

Operações têm `id`, `action`, `state`, `created` e, quando disponíveis, `result` ou `error`. Estados: `fila`, `executando`, `concluído`, `falha`, `adiado`, `cancelado`. Retornar 202 confirma entrada na fila, não conclusão nem prontidão do jogo. O estado `online` depende da mensagem de conexão emitida pelo jogo.

HTTP 401 indica sessão ausente/expirada ou login recusado; 403 indica origem/CSRF incorretos; 404, recurso ausente; 409, conflito/fila cheia; 422, configuração inválida ou confirmação ausente. Erros de validação não retornam o valor de senha enviado.

`GET /healthz` não exige sessão, é interno e verifica a disponibilidade do supervisor. O estado do jogo é um campo separado. Métricas CPU/memória usam cgroup v2 quando disponível; a alternativa mede apenas o supervisor e é identificada no payload/painel. CPU usa 100% por núcleo; rede corresponde ao namespace do contêiner e disco ao sistema de arquivos que contém `/data`.
