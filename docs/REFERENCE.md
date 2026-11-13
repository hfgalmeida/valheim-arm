# Referência avançada

Para instalar e jogar, siga apenas o [README](../README.md). Aqui ficam opções extras, recuperação e informações para mantenedores.

- [Configuração](#configuracao)
- [Painel e certificado](#painel)
- [Backups e mods](#backups)
- [Atualização da imagem e recuperação](#operacao)
- [Publicar imagem](#publicacao)
- [API](#api)
- [Validação na Oracle](#validacao)
- [Desenvolvimento](#desenvolvimento)

<a id="configuracao"></a>
## Configuração

Os nomes abaixo são variáveis do Compose e do `.env`. Configurações do jogo/supervisor usam os mesmos nomes em minúsculas na API. `VALHEIM_IMAGE`, `SERVER_IP` e opções `FEX_*` pertencem à implantação e não são editáveis pelo painel. O painel salva seus ajustes em `data/config/overrides.json`, com precedência sobre o Compose. Não edite esse arquivo enquanto o supervisor estiver rodando.

| Variável | Padrão | Efeito |
|---|---|---|
| `VALHEIM_IMAGE` | hfgalmeida/valheim-arm:latest | Imagem publicada no Docker Hub, por exemplo `hfgalmeida/valheim-arm:latest`; aceita digest para fixar versão |
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

### Modificadores

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

### FEX

O cache JIT usa `data/cache` e é separado dos saves. FEX já vem pronto na imagem Docker Hub, em Release ajustado a Neoverse N1, sem interface gráfica, thunks gráficos ou telemetria offline. Nenhuma extensão ARM inexistente é forçada. A instalação apenas baixa essa imagem.

| Variável | Padrão | Efeito |
|---|---|---|
| `FEX_DISKCACHE` | 1 | Cache JIT em disco |
| `FEX_DISKCACHEMAXFILESIZE` | 1073741824 | Limite do arquivo principal do cache; índice pode usar espaço adicional |
| `FEX_MAXINST` | 5000 | Máximo de instruções por bloco |
| `FEX_MULTIBLOCK` | 1 | Compilação multibloco |
| `FEX_TSOENABLED` | 1 | Preserva ordenação de memória x86 |

São opções avançadas documentadas pelo [FEX](https://wiki.fex-emu.com/index.php/Development%3AConfiguring_FEX). Altere uma por vez e compare o mesmo mundo/carga. Desativar TSO ou alterar os blocos pode prejudicar correção e estabilidade; não é adotado como otimização padrão. As medições de desempenho na instância de referência ainda precisam ser registradas conforme [validação na Oracle](#validacao).

Alterações FEX no `.env` exigem recriar o contêiner, preferencialmente após parada segura pelo painel. Para voltar aos padrões, restaure os valores de `.env.example`. Para limpar o cache, pare o serviço e mova `data/cache` para uma pasta de diagnóstico; crie novamente a pasta com proprietário 10001. Não altere o diretório de saves.

A versão do emulador é selecionada pela imagem em `VALHEIM_IMAGE`. `FEX_VERSION` e `BUILD_JOBS` ficam restritos à construção pelo mantenedor/CI e não fazem parte do `.env` da instalação. A publicação da imagem está documentada em [publicação](#publicacao).

<a id="painel"></a>
## Painel e certificado

O endereço é `https://SEU_IP`. O Caddy emite um certificado local com esse IP e renova os certificados automaticamente. Para confiar na autoridade dessa instalação, obtenha o certificado por SSH:

```sh
sudo docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./valheim-root.crt
openssl x509 -in valheim-root.crt -noout -fingerprint -sha256
```

Transfira `valheim-root.crt` via SCP para seu computador e importe como autoridade confiável no sistema/navegador. No Windows, use o gerenciador de certificados do usuário → Autoridades de Certificação Raiz Confiáveis → Importar. Compare a impressão digital com a exibida via SSH. Instale somente o certificado público `root.crt`; não copie a chave da autoridade. Consulte o [HTTPS local do Caddy](https://caddyserver.com/docs/automatic-https#local-https).

Os comandos para instalar o cliente SCP, baixar o certificado no Windows/Linux e importá-lo estão no [README](../README.md#certificado).

Entre com a senha definida no servidor. Para trocá-la:

```sh
sudo docker compose exec valheim python -m app.cli set-password
```

A troca invalida as sessões existentes. Sessões expiram em oito horas e também desaparecem ao reiniciar o supervisor. Depois de cinco tentativas de login em cinco minutos, novas tentativas ficam bloqueadas até a janela expirar. O proxy compartilha o limite entre os clientes.

Se mudar o IP, atualize `SERVER_IP` no `.env` e execute `sudo docker compose up -d`. O Caddy emitirá um certificado para o novo IP usando a mesma autoridade persistente.

<a id="backups"></a>
## Backups e mods

Backups nativos permanecem habilitados durante o jogo. Arquivos completos são criados diariamente, antes de atualizações e sob solicitação, sempre após parada limpa com confirmação `World saved`. Se houver jogadores continuamente, os arquivos completos serão adiados; os backups nativos continuam funcionando.

Arquivos completos contêm saves, listas, ajustes do painel e configurações de mods, com manifesto e hashes SHA256. A senha administrativa não é exportada. Ajustes podem incluir a senha do jogo: trate os arquivos como privados. A retenção mantém representantes de sete dias e quatro semanas, além do mais recente, podendo descartar backups intermediários do mesmo dia.

O Valheim 1.0 introduziu um [sistema de saves dividido em chunks](https://steamcommunity.com/app/892970/announcements/). O backup do projeto copia todos os arquivos de `data/saves` recursivamente, sem filtrar extensões, preservando as pastas completas dos mundos, os chunks e os backups nativos. A restauração recria essa árvore inteira, substituindo a anterior para evitar sobras de saves diferentes. O envio para a nuvem usa o mesmo arquivo completo. Os testes incluem uma árvore sintética do formato novo; a validação de salvar, restaurar e carregar um mundo real na Oracle permanece pendente.

Baixe um `.tar.gz` pelo painel ou ative o envio automático para a nuvem descrito abaixo. Backups no mesmo volume não protegem contra perda desse disco.

<a id="nuvem"></a>
### Envio automático para OneDrive, Dropbox ou Google Drive

A configuração é opcional e feita em **Backup na nuvem**, no painel. A imagem já inclui o rclone; no computador do administrador ele é usado apenas para autorizar a conta uma vez. Esse procedimento segue a [configuração remota do rclone](https://rclone.org/remote_setup/#configuring-by-copying-the-config-file).

1. No seu computador Windows ou Linux, [baixe o rclone](https://rclone.org/downloads/) e execute `rclone config --config ./valheim-cloud.conf` na pasta em que deseja guardar a conexão. No PowerShell, use `./rclone.exe` no lugar de `rclone` se o executável estiver na pasta atual e não estiver no PATH.
2. Escolha **New remote** (`n`), dê um nome e selecione **OneDrive**, **Dropbox** ou **Google Drive**. Siga a autorização no navegador, escolha a unidade quando solicitado e confirme a conexão. Use a configuração padrão, sem opções avançadas nem conta de serviço. Crie somente uma conexão nesse arquivo.
3. No painel, em **Backup na nuvem**, importe o arquivo `valheim-cloud.conf` ou cole seu conteúdo. Informe a pasta de destino, por exemplo `Valheim/backups`, selecione **Ativado** e clique em **Salvar envio na nuvem**. Para usar outra conta ou pasta, altere esses mesmos campos; deixe a conexão vazia para manter a conta atual.
4. Crie um backup pelo painel ou aguarde o próximo backup agendado. O status mostra envios pendentes, o último arquivo enviado e eventuais falhas. Não é necessário reiniciar o jogo para configurar a nuvem.

São enviados o backup completo `.tar.gz` e seu `.sha256`, usando [`rclone copyto`](https://rclone.org/commands/rclone_copyto/). O envio começa após a conclusão do arquivo local e roda em paralelo ao jogo. Cada arquivo só é registrado como enviado depois das duas cópias; interrupções e falhas são tentadas novamente automaticamente a cada cinco minutos, inclusive após reiniciar o contêiner. Arquivos anteriores à primeira ativação não são enviados. Ao reativar, os arquivos ainda disponíveis desde a primeira ativação são considerados novamente.

Enquanto o envio está ativo, arquivos pendentes não são apagados pela retenção local e podem ocupar espaço além do limite habitual. Desativar o envio pausa a fila e permite a retenção local normal. Ao trocar a conta ou a pasta, os pendentes vão para o destino atual; os arquivos já enviados não são reenviados. A cópia na nuvem não recebe exclusões automáticas: acompanhe a quota e remova arquivos antigos no provedor quando necessário.

A conexão contém credenciais da conta: guarde o arquivo local em lugar privado e não o publique no Git. No servidor, tokens ficam em `data/cloud`, com acesso restrito ao usuário do contêiner, fora dos backups; o rclone pode renovar os tokens durante o envio. A API nunca devolve a conexão salva. Se a autorização for revogada ou expirar sem renovação, autorize novamente e importe a conexão no painel. Para restaurar uma cópia da nuvem, baixe o arquivo e siga o procedimento de importação abaixo.

Para restaurar: pare o jogo pelo painel, selecione um arquivo, confirme a restauração e acompanhe a operação. Um backup prévio é criado. A restauração substitui o diretório de saves, incluindo listas; configurações do supervisor e mods arquivadas são preservadas para recuperação manual, mas não são reaplicadas automaticamente. Selecione no painel o mundo indicado pelo resultado e inicie o jogo. Para importar um backup baixado anteriormente, envie o `.tar.gz` por SCP para `data/backups`, preserve seu nome original, gere o sidecar com `sha256sum ARQUIVO.tar.gz | cut -d ' ' -f 1 > ARQUIVO.tar.gz.sha256` e ajuste proprietário para `10001:10001`.

Para importar um mundo do PC: pare o jogo e copie para `data/saves/worlds_local/` a pasta inteira do mundo no formato novo, preservando todos os arquivos internos. Para um mundo no formato antigo, copie o par `.db` e `.fwl` correspondente. Ajuste o proprietário de todos os arquivos importados para `10001:10001` e selecione em `WORLD_NAME` o nome do mundo (o nome da pasta no formato novo ou o nome sem extensão no antigo). Não renomeie nem selecione arquivos isolados de um save dividido em chunks. Preserve uma cópia externa dos arquivos originais.

Para BepInEx, obtenha um **BepInExPack_Valheim Linux** de fonte confiável, calcule seu SHA256 e configure `BEPINEX=true`, `BEPINEX_URL=https://...` e `BEPINEX_SHA256=...`. O pacote deve conter `libdoorstop.so` junto de `BepInEx/core`. As opções podem ser definidas no painel. Plugins ficam em `data/mods/BepInEx/plugins`, configurações em `data/mods/BepInEx/config` e patchers em `data/mods/BepInEx/patchers`. Copie-os com o jogo parado e proprietário `10001:10001`. O [BepInEx](https://docs.bepinex.dev/articles/user_guide/installation/) deve corresponder ao jogo Linux x86_64 e ao runtime esperado pelos plugins. Não há atualização automática de mods. Clientes de console não instalam plugins; use mods compatíveis com clientes vanilla ao habilitar crossplay.

<a id="operacao"></a>
## Atualização da imagem e recuperação

### Atualizar jogo, FEX e mods

Atualizações automáticas do **jogo** passam pela fila, aguardam zero jogadores, encerram graciosamente, criam backup e usam SteamCMD para instalar em uma pasta nova. O ponteiro `data/current` só muda após confirmação de sucesso. Uma falha de download preserva a instalação anterior; quando aplicável, o supervisor volta a iniciá-la. São mantidas duas instalações para recuperação.

Uma instalação que baixa corretamente mas falha ao executar permanece disponível para diagnóstico. A ativação não é um teste de conexão real. Verifique o log após cada atualização, especialmente com mods. Não há atualização automática do BepInEx/plugins.

Para atualizar **FEX e o painel**, use uma nova imagem do Docker Hub. Primeiro crie um backup, solicite parada segura pelo painel e confirme que terminou. Execute na pasta da instalação:

```sh
sudo docker compose stop valheim
sudo docker compose pull valheim
sudo docker compose run --rm --no-deps valheim preflight
sudo docker compose up -d --no-build
sudo docker compose exec valheim cat /opt/build-info.json
```

`VALHEIM_IMAGE` no `.env` seleciona a imagem: use `hfgalmeida/valheim-arm:latest`, uma tag publicada como `:1.0.0` ou um digest `hfgalmeida/valheim-arm@sha256:...` para fixar exatamente o artefato. Ao escolher outra referência, salve `.env` antes do `pull`. Se o download ou preflight falhar, não avance para o `up`; confira o erro e volte à referência anterior se necessário. Os volumes de saves, configurações e backups são preservados.

O `pull` só obtém uma versão nova do FEX quando o mantenedor já publicou outra imagem. `FEX_VERSION` e `BUILD_JOBS` são opções de construção usadas no CI, não configurações da instalação. Para atualizar o Caddy, planeje uma janela para o painel, execute `sudo docker compose pull proxy` e `sudo docker compose up -d --no-build proxy`; a autoridade HTTPS permanece no volume persistente.

Para reverter **a imagem do supervisor/FEX**, pare o serviço, ajuste `VALHEIM_IMAGE` para a tag/digest anterior, execute `pull`, preflight e `up -d --no-build`. Preserve previamente um backup e confira compatibilidade dos dados. Isso não reverte automaticamente a instalação Valheim em `data/installs` nem os saves; o rollback do jogo é descrito abaixo.

Para atualizar BepInEx, faça backup, pare o jogo, altere URL e SHA256 do pack, mantenha os plugins/configurações persistentes e reinicie. Verifique a compatibilidade dos plugins com a versão do jogo. Para desativar a injeção, salve `bepinex=false` no painel e reinicie; as pastas são preservadas. Alguns mods alteram saves e podem exigir seus próprios procedimentos de remoção.

### Rollback de instalação

Se uma atualização mudar o formato do save, uma versão antiga do jogo pode não conseguir carregá-lo. Preserve os arquivos atuais e escolha um backup criado **antes** da atualização.

1. Solicite parada pelo painel. Se o jogo já falhou, pare o serviço Docker e mantenha-o parado durante a recuperação.
2. Liste `data/installs` e identifique a instalação anterior pela data e pelo arquivo `steamapps/appmanifest_896660.acf`.
3. Com o serviço parado, ajuste `data/current` para a instalação anterior. Use o procedimento dentro do contêiner de manutenção, que conhece os caminhos internos `/data`. Substitua `IDENTIFICADOR` pela pasta confirmada no passo anterior:

```sh
sudo docker compose run --rm --no-deps --entrypoint python valheim -c \
 'from pathlib import Path; p=Path("/data/installs/IDENTIFICADOR"); assert p.is_dir() and not p.is_symlink(); n=Path("/data/.rollback-current"); n.symlink_to(p); n.replace("/data/current")'
```

Antes, inspecione um eventual `.rollback-current` deixado por uma tentativa anterior e remova somente esse link temporário, se necessário.

4. Se precisar recuperar os saves offline, veja o procedimento abaixo; depois inicie o serviço e confirme versão, mundo e conexão.

### Queda de energia ou parada incompleta

O supervisor deixa `data/config/game-active.json` enquanto o jogo estiver ativo e só remove o marcador após confirmar save e saída limpa. Se houver timeout de 150 segundos ou o jogo terminar sem `World saved`, backup/atualização são cancelados. Os saves anteriores não são sobrescritos pela manutenção.

Primeiro confira logs e espaço em disco. Se o mundo ainda iniciar, inicie o jogo e solicite uma parada limpa antes de retomar a manutenção. Caso precise restaurar sem conseguir iniciar o mundo:

1. Pare o serviço e guarde uma cópia externa de `data/saves`, `data/config` e `data/backups`.
2. Use um contêiner de manutenção, mantendo o supervisor parado, para validar e restaurar um arquivo. O método abaixo verifica o sidecar SHA256, os caminhos e o manifesto antes de substituir saves:

```sh
sudo docker compose stop valheim
sudo docker compose run --rm --no-deps --entrypoint python valheim -c \
 'from pathlib import Path; from app.backups import Backups; print(Backups(Path("/data")).restore("NOME.tar.gz"))'
```

3. Após preservar o estado anterior e concluir a restauração, arquive o marcador de encerramento incompleto (renomeie `data/config/game-active.json` para `game-active.recovered.json`). Não remova o marcador só para contornar uma falha sem conferir os saves.
4. Selecione o mundo correto nas configurações e inicie com `sudo docker compose up -d`. O painel pode estar indisponível enquanto somente o serviço do jogo estiver parado; ajustes offline devem ser feitos com o supervisor parado e JSON válido.

### Diagnóstico

| Sintoma | Verificação |
|---|---|
| Sem acesso ao painel | IP no `.env`, `docker compose ps`, log do proxy, NSG/Security List e cadeia DOCKER-USER |
| Certificado não confiável | Importar a CA desta instalação via SSH; confirmar IP e impressão digital |
| Login sempre recusado | Definir senha pelo comando local; aguardar cinco minutos se houve tentativas excessivas |
| SteamCMD falha | Saída DNS/HTTPS/Steam, espaço livre, rootfs com loader i386 e log do supervisor |
| Jogo não inicia | Mensagens do FEX, versão registrada, permissões 10001, arquivo do mundo e plugins |
| Navegador: `ERR_SSL_PROTOCOL_ERROR` ao acessar pelo IP | O Caddyfile deve conter `default_sni {$SERVER_IP}` no bloco global para selecionar o certificado correto sem SNI atrás do NAT do Docker. Confirme o IP no `.env`, atualize o Caddyfile e recrie somente o proxy com `sudo docker compose up -d --no-deps --force-recreate proxy`. O instalador preserva arquivos existentes, portanto uma nova execução não substitui um Caddyfile antigo. Referência: [Caddy default_sni](https://caddyserver.com/docs/caddyfile/options#default-sni) |
| SteamCMD: `Missing configuration` no app 896660 | O supervisor conclui a autoatualização separadamente, seleciona Linux e tenta o download até três vezes, renovando apenas `appcache/appinfo.vdf`, com esperas de 10 e 20 segundos. Se persistir, consulte os logs Steam em `data/steamcmd/logs` e `data/home/Steam/logs`; a recuperação geral tenta iniciar novamente após cinco minutos |
| Tarefa fica adiada | Jogadores presentes, eventos recentes, A2S inacessível ou snapshot Connections ausente/expirado |
| Crossplay não aparece | Saída PlayFab, log com join code, `crossplay` efetivo e compatibilidade dos mods |
| Backup não restaura | Checksum lateral, integridade do tar.gz, espaço livre e jogo parado |
| Painel mostra CPU limitada ao supervisor | cgroup v2 indisponível; habilitar o ambiente de referência em vez de interpretar esse valor como consumo do jogo |

Comandos úteis:

```sh
sudo docker compose ps
sudo docker compose logs --tail=200 valheim
sudo docker compose logs --tail=100 proxy
sudo docker stats --no-stream
sudo docker compose exec valheim python -m app.cli preflight
sudo docker compose exec valheim cat /opt/build-info.json
sudo df -h ./data
```

Não publique `.env`, `data/config/overrides.json` ou backups em tickets. Logs removem as senhas conhecidas do jogo, mas ainda podem conter IDs dos jogadores e join codes. O supervisor tenta iniciar novamente após cinco minutos quando o processo do jogo cai ou a instalação inicial falha. Uma parada solicitada pelo painel desativa essas tentativas até você iniciar novamente. A recuperação não restaura backups por conta própria; erros persistentes ficam nos logs.

<a id="publicacao"></a>
## Publicar imagem

O workflow `.github/workflows/dockerhub.yaml` testa o supervisor, constrói a imagem em um runner **ARM64 nativo**, executa o preflight do FEX e publica a mesma imagem testada no Docker Hub. Não usa QEMU nem outro emulador para construir a imagem.

Este guia é para o mantenedor que publica a imagem. Para instalar o servidor, siga o [README](../README.md): somente Docker Compose e arquivos de configuração são necessários na Oracle. A compilação fica no GitHub Actions.

O repositório GitHub [hfgalmeida/valheim-arm](https://github.com/hfgalmeida/valheim-arm) é público: os arquivos de instalação e o código podem ser baixados sem autenticação GitHub. A visibilidade da imagem Docker é uma configuração própria do Docker Hub. Para distribuir a instalação sem exigir login na Oracle, publique a imagem em um repositório Docker Hub público.

### Configuração deste repositório

Configurações de um projeto não são copiadas automaticamente para outro. Secrets e variables de repositório precisam existir em **hfgalmeida/valheim-arm**. Se forem de uma organização, conceda acesso a este repositório na política desses secrets/variables. Secrets vinculados a um Environment não são lidos por este workflow, que não seleciona Environment.

No GitHub, abra **Settings → Secrets and variables → Actions** neste repositório:

| Tipo | Nome | Valor |
|---|---|---|
| Variable | `DOCKERHUB_USERNAME` | Seu usuário real no Docker Hub; não precisa ser igual ao GitHub |
| Secret | `DOCKERHUB_TOKEN` | Token Docker Hub com permissão de leitura e escrita no repositório de destino |
| Variable opcional | `DOCKERHUB_IMAGE` | Sobrescreve o destino padrão, por exemplo `suaorganizacao/valheim-arm`; sem registry ou tag |

Se `DOCKERHUB_IMAGE` estiver ausente, o workflow usa `GITHUB_REPOSITORY` em minúsculas: proprietário e nome do repositório GitHub. Neste projeto, o destino é automaticamente `hfgalmeida/valheim-arm`; não é necessário configurar o nome da imagem. `DOCKERHUB_USERNAME` é usado somente para autenticação no Docker Hub, e a conta deve ter permissão para publicar nesse destino. O workflow também aceita `DOCKERHUB_USERNAME` como secret para aproveitar esse padrão quando já utilizado em outros projetos.

Crie o repositório `valheim-arm` no namespace desejado no Docker Hub e selecione **Public** para permitir downloads públicos. Se ele já existir, abra **My Hub → Repositories → valheim-arm → Settings → Visibility settings → Make public** e confirme o nome, conforme o [guia de visibilidade Docker Hub](https://docs.docker.com/docker-hub/repos/manage/access/). Essa alteração é feita no Docker Hub; o workflow não muda a visibilidade do repositório.

Crie ou reutilize um token com permissão de escrita nesse destino e salve-o como `DOCKERHUB_TOKEN` nos secrets do GitHub Actions. A publicação exige autenticação mesmo para uma imagem pública. Tokens pessoais usam as permissões dos repositórios acessíveis pela conta; tokens de organização podem precisar de autorização específica para o destino. Não cadastre o token no código, no `.env` da Oracle ou nos logs.

Confirme que GitHub Actions está habilitado e que a política do repositório permite as actions `actions/*` e `docker/*`. O workflow usa o runner padrão ARM64 `ubuntu-24.04-arm`, disponível gratuitamente para repositórios públicos segundo a [documentação do GitHub](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). Mantenha esse runner ARM64, pois o Dockerfile exige compilação nativa.

Fontes: [secrets do GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets), [autenticação e publicação Docker](https://docs.docker.com/build/ci/github-actions/push-multi-registries/), [runners GitHub](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

### Disparos e tags

O arquivo [VERSION](../VERSION) na raiz do repositório define a versão da imagem. Ele começa em `0.1.0` e deve conter somente um número no formato `X.Y.Z`, sem prefixo `v` e sem zeros à esquerda. Essa é a versão deste projeto/imagem, independente das versões do Valheim e do FEX.

Para publicar a próxima versão, altere **apenas esse arquivo**:

```text
0.1.1
```

Faça commit e push da alteração para a `main`, ou edite o arquivo pela interface do GitHub e mescle o PR. O Actions lê o número, executa os testes, constrói e verifica a imagem ARM64, e publica `0.1.1`, `latest` e a tag do commit. Não é preciso criar tag Git, GitHub Release, token GitHub adicional ou usar Conventional Commits.

| Disparo | Tags publicadas |
|---|---|
| Qualquer push na `main`, se a versão ainda não estiver publicada | `<VERSION>`, `latest` e `sha-<commit completo>` |
| Execução manual na `main` | `<VERSION>`, `latest` e `sha-<commit completo>` |
| Execução manual em outra branch | Somente `sha-<commit completo>` |

Todo push na `main` inicia o workflow de publicação, mesmo sem alterar `VERSION`. Você pode enviar vários commits com o mesmo número enquanto a imagem dessa versão ainda não tiver sido publicada. Quando a publicação for concluída, os próximos builds com esse número serão bloqueados; altere `VERSION` para publicar novamente. Pushes de tags Git e pull requests não publicam imagens. O workflow `Checks` continua validando os commits independentemente da publicação.

Use um número novo para cada versão. O workflow não incrementa o arquivo por conta própria. Antes de compilar, ele consulta a tag exata no Docker Hub: se já existir, encerra com erro e pede para aumentar `VERSION`. A verificação é repetida antes do envio da imagem. Uma execução manual também passa por essa validação, incluindo execuções em outras branches. Falhas de rede, autenticação, limite de consultas ou respostas inesperadas bloqueiam a publicação; somente a confirmação de tag ausente permite continuar. `latest` pode ser atualizada ao publicar uma nova versão. Execuções de publicação são serializadas.

As duas consultas usam `DOCKERHUB_USERNAME` e `DOCKERHUB_TOKEN` já configurados no Actions para obter autorização no registry, inclusive na primeira publicação. O token é passado por variável de ambiente e não aparece nos argumentos do comando. Se ocorrer HTTP 401/403, confirme que o usuário é o Docker ID, que o token está válido com leitura/escrita e que `DOCKERHUB_IMAGE` aponta para um namespace em que essa conta pode publicar. Esses erros não são tratados como tag disponível. O download público na Oracle continua dispensando login.

Após enviar os arquivos ao GitHub e cadastrar as credenciais, abra **Actions → Publish Docker Hub → Run workflow**. O formulário permite `fex_version=latest` ou uma tag estável como `FEX-2611`. Pushes automáticos usam a última release estável.

A versão do FEX é resolvida antes do build e passada como argumento fixo. Assim, o cache não esconde uma release nova do emulador. A tag FEX aparece nos labels da imagem e sua proveniência completa em `/opt/build-info.json`. O cache de construção é armazenado no GitHub Actions.

Enquanto a versão ainda não tiver sido publicada, uma nova tentativa pode incorporar um FEX/rootfs/base atualizado. Após publicar, escolha outro número em `VERSION` para gerar uma nova imagem. Use o digest Docker para identificar exatamente o artefato publicado. O resumo do workflow lista as tags, e os logs de push informam o digest. A imagem é somente `linux/arm64`, ajustada a Neoverse N1.

O preflight não instala SteamCMD nem executa o Valheim, e não substitui a aceitação de crossplay, BepInEx ou desempenho na Oracle. Consulte [validação na Oracle](#validacao).

### Usar a imagem publicada na Oracle

O `compose.yaml` distribuído já baixa a imagem indicada por `VALHEIM_IMAGE`; não há bloco `build` na instalação. No `.env` da Oracle, configure a referência correspondente à publicação:

```dotenv
VALHEIM_IMAGE=hfgalmeida/valheim-arm:0.1.0
```

O destino padrão da publicação é `hfgalmeida/valheim-arm`, obtido do repositório GitHub. Use `DOCKERHUB_IMAGE` no CI somente para publicar em outro namespace/repositório. `VALHEIM_IMAGE` configura o que a Oracle baixa e pode incluir uma tag ou digest específico; seu padrão também é `hfgalmeida/valheim-arm:latest`.

Para seguir automaticamente a referência da publicação mais recente ao executar `pull`, use `:latest`. Para selecionar uma versão numerada, use exatamente o número de `VERSION` como tag e atualize o `.env` da Oracle ao decidir migrar. Publicar uma imagem não reinicia nem atualiza a instância por conta própria.

Com o repositório da imagem definido como **Public** no Docker Hub, a Oracle pode fazer `pull` sem login nem token. Se você optar por uma imagem privada, autentique-se antes com `sudo docker login` e uma credencial de leitura. Pare o jogo de forma segura pelo painel antes de trocar sua imagem e execute:

```sh
sudo docker compose pull valheim
sudo docker compose up -d --no-build
```

A primeira instalação por imagem pronta ainda exige criar `data` com proprietário 10001, configurar `.env`, executar `set-password` e configurar o HTTPS conforme o [README](../README.md). Atualizar a imagem troca supervisor/FEX; as atualizações do jogo continuam sob controle do supervisor.

<a id="api"></a>
## API

Base `/api/v1`, JSON. A API é acessada pelo mesmo HTTPS do painel. Não publique 8080. Há um administrador; não há registro nem alteração de senha pela web.

1. `POST /login` com `{"password":"..."}` e cabeçalho `Origin` exatamente igual a `PANEL_ORIGIN`.
2. Guardar o cookie `session` (Secure, HttpOnly, SameSite=Strict) e o `csrf` retornado.
3. Em mutações autenticadas, enviar `Origin` e `X-CSRF-Token`. O cookie autentica leituras e SSE.

| Método e caminho | Entrada / saída |
|---|---|
| GET `/session` | Recupera token CSRF da sessão atual |
| POST `/logout` | Invalida sessão |
| GET `/state` | Estado do jogo, contagem e motivo, crossplay e código de entrada, builds, operações, últimos 500 logs, histórico de até 720 métricas e proveniência da imagem |
| GET `/cloud` | Configuração pública e status do envio, sem tokens: `enabled`, `configured`, `provider`, `folder`, `pending`, `uploading`, `last_success`, `last_file`, `error` |
| PUT `/cloud` | `{"enabled":true,"folder":"Valheim/backups","config":"..."}`; `config` vazio mantém a conexão salva. Exige sessão e CSRF; recusa alterações durante um envio |
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

O estado e os eventos incluem `crossplay` (configuração em execução, ou a configuração efetiva quando parado) e `join_code` (texto de seis dígitos ou `null`). O código é extraído das mensagens de registro/atividade da sessão no log, preservando zeros iniciais; veja um [exemplo do formato do log](https://github.com/husjon/valheim_server_oci_setup#crossplay-console--game-pass). Ele é limpo ao iniciar uma nova execução, encerrar o jogo ou falhar a leitura do log. O painel aguarda o código quando crossplay está ativo e só o exibe dentro da sessão administrativa autenticada.

O campo `cloud` de `/state` e dos eventos contém o mesmo status público de GET `/cloud`, permitindo acompanhar os envios sem recarregar o painel.

HTTP 401 indica sessão ausente/expirada ou login recusado; 403 indica origem/CSRF incorretos; 404, recurso ausente; 409, conflito/fila cheia; 422, configuração inválida ou confirmação ausente. Erros de validação não retornam o valor de senha enviado.

`GET /healthz` não exige sessão, é interno e verifica a disponibilidade do supervisor. O estado do jogo é um campo separado. Métricas CPU/memória usam cgroup v2 quando disponível; a alternativa mede apenas o supervisor e é identificada no payload/painel. CPU usa 100% por núcleo; rede corresponde ao namespace do contêiner e disco ao sistema de arquivos que contém `/data`.

<a id="validacao"></a>
## Validação na Oracle

**Pendente:** a aceitação completa na instância Oracle ARM ainda precisa ser registrada. Os testes do supervisor e o preflight do FEX não substituem a validação real do jogo, crossplay e mods na plataforma de referência.

Execute em Ubuntu 24.04 ARM64, 2 OCPUs/12 GB, começando em um mundo descartável. Registre tag/commit do projeto, `uname -a`, `getconf PAGESIZE`, versão Docker/Compose e conteúdo de `/opt/build-info.json`. Guarde somente dados sem senhas.

### Etapa 1 — prova funcional obrigatória

- [ ] Configurar `VALHEIM_IMAGE` com a imagem publicada, executar `docker compose pull` e `preflight`, sem compilar na instância nem instalar binfmt, privilégios adicionais, FUSE ou outro emulador. Registrar também o digest da imagem baixada.
- [ ] Configurar senha administrativa, iniciar vanilla, observar conexão e entrar com cliente Steam.
- [ ] Identificar `Connections N ZDOS:` nos logs; confirmar estado desconhecido antes do primeiro snapshot e durante conexões/desconexões.
- [ ] Habilitar crossplay, reiniciar e entrar por join code com cliente compatível de outra plataforma. Confirmar que eventos PlayFab invalidam a contagem até o próximo snapshot. Guardar amostras anonimizadas para atualizar os testes.
- [ ] Instalar um BepInExPack Linux com SHA256 conhecido e plugin de teste compatível; confirmar carregamento no log e conexão. Repetir a verificação de contagem com BepInEx ativo.
- [ ] Validar que o SIGINT entregue através do FEX salva o mundo e encerra o processo. Se o texto de save/conexões diferir, adaptar o parser e os testes antes de continuar.

Interrompa a aceitação se qualquer combinação exigida não funcionar. Não substitua FEX por outro emulador para contornar a incompatibilidade.

### Etapa 2 — painel e manutenção

- [ ] Confiar na CA obtida via SSH e entrar em `https://IP`; verificar bloqueio de acesso não autenticado à API e aos backups.
- [ ] Conferir métricas contra `docker stats`: CPU por núcleo, memória do cgroup, rede e disco. Deixar a página aberta para observar gráficos em atualização.
- [ ] Alterar nome, senha e modificadores; confirmar indicação pendente, aplicação após reinício e origem dos valores. Usar “Usar Compose”, recriar o contêiner e confirmar precedência/persistência.
- [ ] Trocar mundo com confirmação e voltar ao anterior; verificar que os saves originais permanecem.
- [ ] Editar administradores, banidos e permitidos e conferir comportamento no jogo.
- [ ] Solicitar atualização/reinício com jogadores conectados: a operação manual deve recusar. Agendar tarefa e confirmar adiamento sem desconexão.
- [ ] Sair do jogo, aguardar snapshot completo e dois minutos vazios; confirmar execução da tarefa adiada, save e retorno do jogo.
- [ ] Tentar conectar durante a verificação final, em Steam e crossplay; verificar cancelamento caso o evento já tenha sido processado e comportamento de suspensão/retomada do grupo FEX.
- [ ] Simular contagem desconhecida e consulta A2S indisponível; confirmar que nenhum SIGINT é enviado automaticamente.
- [ ] Agendar backup e reinício no mesmo horário e combinar uma atualização pendente; observar uma única manutenção.
- [ ] Recriar o supervisor com manutenção adiada e confirmar retomada da intenção persistida.

### Etapa 3 — dados e falhas

- [ ] Criar backup com o jogo parado, baixar no navegador e validar manifesto/checksum.
- [ ] Alterar algo no mundo, parar, restaurar e confirmar o estado anterior no jogo. Verificar criação do backup pré-restauração.
- [ ] Em volume descartável, simular disco sem espaço e download Steam interrompido; confirmar preservação do backup/instalação anteriores e erro visível.
- [ ] Confirmar rejeição de arquivo adulterado e de entradas tar com caminhos externos ou links.
- [ ] Simular encerramento anormal em mundo descartável; confirmar marcador e bloqueio da manutenção até recuperação/parada limpa.
- [ ] Desligar e ligar a instância; verificar volumes, configurações, senha administrativa e certificado persistentes.

### Medição de desempenho

Use o mesmo mundo, mesmas áreas e jogadores, e registre três execuções por configuração: vanilla sem cache, vanilla com cache quente e BepInEx com a lista exata de plugins. Anote tempo até ficar online, consumo ocioso, pico de memória, CPU e atrasos de save durante pelo menos 30 minutos de jogo. Altere uma opção FEX de cada vez.

| Cenário | FEX / build jogo | Jogadores / mods | Inicialização | CPU média/pico | RAM pico | Save / observações |
|---|---|---|---|---|---|---|
| Vanilla sem cache | pendente | pendente | pendente | pendente | pendente | pendente |
| Vanilla cache quente | pendente | pendente | pendente | pendente | pendente | pendente |
| BepInEx cache quente | pendente | pendente | pendente | pendente | pendente | pendente |

A aceitação termina quando os cenários exigidos funcionarem e as evidências forem registradas. Não há promessa prévia de número de jogadores ou melhoria percentual.

<a id="desenvolvimento"></a>
## Desenvolvimento

Para publicar uma nova versão da imagem, altere somente [VERSION](../VERSION), por exemplo de `0.1.0` para `0.1.1`, e envie a mudança à `main`. O GitHub Actions publica as tags `0.1.1`, `latest` e a identificação do commit após os testes e o preflight. Detalhes em [publicação](#publicacao).

O ambiente abaixo serve aos testes do código-fonte na máquina do mantenedor/CI. A instalação na Oracle usa exclusivamente as imagens Docker Hub descritas acima. Para desenvolver, use Python 3.12 e Node:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
node --check app/static/panel.js
```

Os testes usam um runtime isolado, sem executar o jogo nem acessar Steam/Oracle. A API privada do painel está documentada em [API](#api).

O teste opcional de navegador usa a interface real com respostas de API determinísticas, verificando login, formulários, ações e layout móvel. Não representa uma sessão real do jogo:

```sh
pip install -r requirements-browser.txt
python -m playwright install chromium
RUN_BROWSER_TESTS=1 python -m pytest -q tests/test_panel_browser.py
```

No Windows com Edge instalado, use `$env:RUN_BROWSER_TESTS='1'` e `$env:BROWSER_CHANNEL='msedge'` no PowerShell; não é necessário baixar Chromium nesse caso. A captura gerada fica em `.artifacts/panel.png` e contém dados de teste. O workflow `Checks` executa os testes do supervisor e oferece build ARM/preflight manual por `workflow_dispatch`. O workflow `Publish Docker Hub` também constrói e verifica a imagem antes de publicar, nos disparos descritos em [publicação](#publicacao). Esses testes não substituem a aceitação OCI.

## Instalação e migração

O instalador aceita Ubuntu 24.04 e 26.04 LTS ARM64 com páginas de 4 KiB, usa o repositório oficial Docker correspondente ao host (`noble` ou `resolute`) quando necessário e grava a instalação em `./valheim-arm`. Para escolher outra pasta, execute `VALHEIM_INSTALL_DIR=/caminho/servidor bash install-valheim.sh`. O IP sugerido é obtido por HTTPS em api.ipify.org e pode ser alterado no prompt. As portas da OCI e do firewall local precisam estar liberadas antes de jogar; o instalador não altera regras da nuvem.

Para uma instalação existente, indique sua pasta com `VALHEIM_INSTALL_DIR`. O instalador preserva `.env`, Compose, Caddyfile, senhas e saves, e não reinicia um servidor que já esteja rodando. Se houver falha durante uma instalação nova, execute novamente usando a mesma pasta.

O `.env.example` contém somente o essencial. Todas as opções avançadas continuam disponíveis no `compose.yaml` e nas tabelas acima. A imagem padrão é pública, `hfgalmeida/valheim-arm:latest`, e não exige login. Modificadores, crossplay, horários, usuários e backups podem ser administrados pelo painel. Alterações do jogo ficam pendentes até uma reinicialização segura; ajustes de horário são lidos pelo agendador.

A imagem continua baseada em Ubuntu 24.04, independentemente da versão do host. A aceitação completa na Oracle permanece pendente; a inclusão de Ubuntu 26.04 no instalador não substitui esse teste real. O preflight é executado automaticamente na inicialização. Se houver erro de permissão em `data`, confirme o proprietário UID/GID 10001. Se Docker existente não tiver Compose ou houver pacotes conflitantes, siga a [instalação oficial Docker](https://docs.docker.com/engine/install/ubuntu/) e execute o instalador novamente. O instalador não remove engines existentes.
