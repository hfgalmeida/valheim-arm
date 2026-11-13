# Configuração

Os nomes abaixo são variáveis do Compose e do `.env`. Na API, use os mesmos nomes em minúsculas. O painel salva seus ajustes em `data/config/overrides.json`, com precedência sobre o Compose. Não edite esse arquivo enquanto o supervisor estiver rodando.

| Variável | Padrão | Efeito |
|---|---|---|
| `SERVER_IP` | obrigatório | IPv4 público do painel/certificado; alterar exige recriar ambos os serviços |
| `SERVER_NAME` | Valheim ARM | Nome exibido pelo servidor |
| `WORLD_NAME` | Dedicated | Seleciona/cria mundo; preserva os anteriores |
| `SERVER_PASSWORD` | obrigatório | Senha do jogo, 5–128 caracteres |
| `PUBLIC` | true | Listagem pública |
| `CROSSPLAY` | false | Backend PlayFab, incluindo consoles |
| `PRESET` | vazio | normal, casual, easy, hard, hardcore, immersive ou hammer |
| `SAVE_INTERVAL` | 1800 | Intervalo de save em segundos; mínimo 60 |
| `NATIVE_BACKUPS` | 4 | Quantidade de backups internos do jogo |
| `TIMEZONE` | America/Sao_Paulo | Fuso IANA usado pelo agendador |
| `RESTART_AT` | 05:00 | HH:MM ou off |
| `BACKUP_AT` | 05:00 | HH:MM ou off; combinado com reinício quando coincidem |
| `AUTO_UPDATE` | true | Consulta e agenda atualização do jogo |
| `UPDATE_INTERVAL` | 1800 | Consulta em segundos; mínimo 300 |
| `RETAIN_DAILY` / `RETAIN_WEEKLY` | 7 / 4 | Dias e semanas distintos representados nos arquivos completos |
| `BEPINEX` | false | Injeção do pack Linux, com plugins locais |
| `BEPINEX_URL` / `BEPINEX_SHA256` | vazios | URL HTTPS e hash obrigatório quando habilitado |

`SERVER_PORT=2456` é fixado no Compose e não editável pelo painel. Para mudá-lo, altere o ambiente do serviço, o mapeamento UDP das duas portas consecutivas e as regras de rede. O proxy usa 443/TCP. A API 8080 é somente interna.

## Modificadores

Cada variável é independente, e um valor vazio deixa de enviar o argumento. Quando há preset, os modificadores vêm depois dele na linha de comando.

| Variável | Valores além de vazio |
|---|---|
| `MODIFIER_COMBAT` | veryeasy, easy, hard, veryhard |
| `MODIFIER_DEATHPENALTY` | casual, veryeasy, easy, hard, hardcore |
| `MODIFIER_RESOURCES` | muchless, less, more, muchmore, most |
| `MODIFIER_RAIDS` | none, muchless, less, more, muchmore |
| `MODIFIER_PORTALS` | casual, hard, veryhard |

As variáveis booleanas `KEY_NOBUILDCOST`, `KEY_PLAYEREVENTS`, `KEY_PASSIVEMOBS` e `KEY_NOMAP` enviam `-setkey` quando verdadeiras. Não são comandos de remoção das chaves já existentes. O comportamento deriva da [interface oficial do Valheim](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/); após atualização do jogo, confirme se novas opções foram adicionadas antes de expandir o formulário.

Exemplo:

```dotenv
PRESET=hard
MODIFIER_COMBAT=easy
MODIFIER_RESOURCES=more
KEY_NOMAP=true
CROSSPLAY=true
```

## FEX

O cache JIT usa `data/cache` e é separado dos saves. O build é Release, ajustado a Neoverse N1, sem interface gráfica, thunks gráficos ou telemetria offline. LTO fica desligado para reduzir o custo da construção em duas OCPUs. Nenhuma extensão ARM inexistente é forçada.

| Variável | Padrão | Efeito |
|---|---|---|
| `FEX_VERSION` | latest | Última release estável no build; aceita tag `FEX-2611` ou equivalente |
| `BUILD_JOBS` | 2 | Paralelismo de construção, 1–8 |
| `FEX_DISKCACHE` | 1 | Cache JIT em disco |
| `FEX_DISKCACHEMAXFILESIZE` | 1073741824 | Limite do arquivo principal do cache; índice pode usar espaço adicional |
| `FEX_MAXINST` | 5000 | Máximo de instruções por bloco |
| `FEX_MULTIBLOCK` | 1 | Compilação multibloco |
| `FEX_TSOENABLED` | 1 | Preserva ordenação de memória x86 |

São opções avançadas documentadas pelo [FEX](https://wiki.fex-emu.com/index.php/Development%3AConfiguring_FEX). Altere uma por vez e compare o mesmo mundo/carga. Desativar TSO ou alterar os blocos pode prejudicar correção e estabilidade; não é adotado como otimização padrão. Nenhuma melhoria de desempenho foi medida nesta estação Windows.

Alterações FEX no `.env` exigem recriar o contêiner, preferencialmente após parada segura pelo painel. Para voltar aos padrões, restaure os valores de `.env.example`. Para limpar o cache, pare o serviço e mova `data/cache` para uma pasta de diagnóstico; crie novamente a pasta com proprietário 10001. Não altere o diretório de saves.
