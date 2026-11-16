# Referência do servidor

Para instalar e jogar, siga o [README](../README.md). Esta referência reúne configurações e comandos para administrar sua instância. Desenvolvimento, publicação, API e testes estão no [guia do mantenedor](MAINTAINER.md).

Execute os comandos do servidor na pasta da instalação: `cd ~/valheim-arm`. Ajuste esse caminho se escolheu outra pasta.

- [Configuração](#configuracao)
- [Rede e firewall](#rede)
- [Painel e certificado](#painel)
- [Jogadores e permissões](#jogadores)
- [Backups e restauração](#backups)
- [Backup na nuvem](#nuvem)
- [Mods BepInEx](#mods)
- [Operação e atualização](#operacao)
- [Diagnóstico](#diagnostico)

<a id="configuracao"></a>
## Configuração

Use **Configurações do jogo** para mundo, acesso e regras; **Configurações do servidor** para salvamento, rotina automática e alertas; e **Mods** para BepInEx e plugins. Cada aba salva seus próprios ajustes. Ajustes do painel prevalecem sobre o `.env`; **Usar Compose** remove essa substituição. Alterações do jogo são aplicadas na próxima inicialização; horários são lidos pelo agendador. Trocar o nome do mundo seleciona ou cria outro save, preservando o anterior.

Para alterar o `.env`, use `sudo nano .env` e aplique com `sudo docker compose up -d`. Isso pode recriar os contêineres; faça um backup e solicite a parada pelo painel antes. `VALHEIM_IMAGE`, `SERVER_IP`, limites `VALHEIM_MEMORY_*` e opções `FEX_*` são configurados somente pelo `.env`.

| Variável | Padrão | Efeito |
|---|---|---|
| `VALHEIM_IMAGE` | hfgalmeida/valheim-arm:latest | Imagem publicada no Docker Hub, por exemplo `hfgalmeida/valheim-arm:latest`; aceita digest para fixar versão |
| `VALHEIM_MEMORY_LIMIT` | 10g | Máximo de RAM do contêiner Valheim |
| `VALHEIM_MEMORY_SWAP_LIMIT` | 14g | Máximo combinado de RAM + swap; depende de swap disponível no host |
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
| `AUTO_UPDATE` | true | Consulta uma vez por dia, no horário do reinício, e agenda atualização do jogo |
| `RETAIN_DAILY` / `RETAIN_WEEKLY` | 7 / 4 | Dias e semanas distintos representados nos arquivos completos |
| `BEPINEX` | false | Habilita o BepInEx padrão incluído na imagem, com plugins locais |

A porta do jogo é fixada em 2456 no Compose, com UDP 2456–2457 publicado. O painel usa TCP 443; a API 8080 não é publicada.

### Memória e swap

Os padrões permitem até **10 GiB de RAM** e **14 GiB no total de RAM + swap**, ou seja, até **4 GiB de swap**. Os limites abrangem jogo, FEX e painel; não reservam memória antecipadamente. Na instância de 12 GiB, o teto de RAM deixa margem para Ubuntu, Docker e proxy.

O Docker não cria swap. Confira a disponível na instância:

```bash
swapon --show
free -h
```

Para ajustar, edite o `.env`:

```dotenv
VALHEIM_MEMORY_LIMIT=10g
VALHEIM_MEMORY_SWAP_LIMIT=14g
```

O total de RAM + swap deve ser maior ou igual ao limite de RAM; valores iguais desabilitam swap para o contêiner. Sem swap no host, o limite de RAM continua valendo. Swap pode reduzir o risco de falta de memória, mas seu uso intenso causa lentidão e não garante proteção contra OOM.

Crie um backup e pare o jogo pelo painel antes de aplicar:

```bash
sudo docker compose config --quiet
sudo docker compose up -d --no-deps valheim
```

Isso recria o contêiner quando necessário, preservando os dados. Reiniciar somente o jogo pelo painel não aplica limites do Docker. Instalações antigas precisam também atualizar as linhas `mem_limit` e `memswap_limit` do serviço `valheim` em `compose.yaml` para as expressões deste projeto; atualizar apenas a imagem não substitui o Compose local.

### Modificadores do mundo

Valor vazio usa o padrão do mundo. Os modificadores individuais prevalecem sobre `PRESET`.

| Variável | Valores além de vazio |
|---|---|
| `MODIFIER_COMBAT` | veryeasy, easy, hard, veryhard |
| `MODIFIER_DEATHPENALTY` | casual, veryeasy, easy, hard, hardcore |
| `MODIFIER_RESOURCES` | muchless, less, more, muchmore, most |
| `MODIFIER_RAIDS` | none, muchless, less, more, muchmore |
| `MODIFIER_PORTALS` | casual, hard, veryhard |

As opções `KEY_NOBUILDCOST`, `KEY_PLAYEREVENTS`, `KEY_PASSIVEMOBS` e `KEY_NOMAP` aceitam `true` ou `false`. Desativar uma opção deixa de enviá-la ao jogo; não remove uma chave já gravada no save.

```dotenv
PRESET=hard
MODIFIER_COMBAT=easy
MODIFIER_RESOURCES=more
KEY_NOMAP=true
CROSSPLAY=true
```

<a id="fex"></a>
### FEX: opções avançadas

Mantenha os padrões até medir sua carga. O cache persistente fica desativado; quando habilitado, usa `data/cache`. Alterações exigem recriar o contêiner. A versão do FEX é definida pela imagem baixada.

| Variável | Padrão | Efeito |
|---|---|---|
| `FEX_DISKCACHE` | 0 | Cache JIT persistente, opcional |
| `FEX_DISKCACHEMAXFILESIZE` | 1073741824 | Limite do arquivo principal do cache; índice pode usar espaço adicional |
| `FEX_MAXINST` | 5000 | Máximo de instruções por bloco |
| `FEX_MULTIBLOCK` | 1 | Compilação multibloco |
| `FEX_TSOENABLED` | 1 | Preserva ordenação de memória x86 |

Alterar blocos ou desativar TSO pode comprometer a estabilidade. Consulte a [configuração do FEX](https://wiki.fex-emu.com/index.php/Development%3AConfiguring_FEX).

<a id="rede"></a>
## Rede e firewall

Na OCI: **Compute → Instances → sua instância → VNIC → Subnet → Security Lists → Add Ingress Rules**. Se usar NSG, confira também suas regras.

| Uso | Protocolo / destino | Origem |
|---|---|---|
| Jogo | UDP 2456–2457 | `0.0.0.0/0` |
| Painel | TCP 443 | `0.0.0.0/0` |
| SSH/SCP | TCP 22 | Preserve a regra existente |

Use porta de origem **All** e regra **stateful**. `0.0.0.0/0` permite acessar o painel de qualquer rede, sem cadastrar o IP do seu computador; a administração exige senha. `SERVER_IP` é o IP público da Oracle.

O instalador libera as portas quando o UFW está ativo. Para configurar manualmente:

```sh
sudo ufw allow 2456:2457/udp
sudo ufw allow 443/tcp
sudo ufw status verbose
```

A publicação do Docker pode passar antes do UFW. Para restringir o painel por rede, limite a origem na OCI; confira outros firewalls locais se existirem. [Docker e UFW](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw).

<a id="painel"></a>
## Painel e certificado

Acesse **https://SEU_IP**. As abas são **Visão geral**, **Configurações do jogo**, **Configurações do servidor**, **Mods**, **Jogadores**, **Backups** e **Atividade**. Ações manuais solicitam a parada imediatamente, mesmo com jogadores conectados, e aguardam o jogo salvar e encerrar.

Para redefinir a senha administrativa:

```sh
sudo docker compose exec valheim python -m app.cli set-password
```

A troca invalida as sessões atuais. Se o IP da instância mudar, atualize `SERVER_IP` no `.env` e execute `sudo docker compose up -d`.

<a id="certificado"></a>
### Confiar no certificado: uma vez por computador

**No servidor**, exporte o certificado público e confira sua impressão digital:

```sh
sudo docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./valheim-root.crt
sudo chmod 644 valheim-root.crt
openssl x509 -in valheim-root.crt -noout -fingerprint -sha256
```

Nos comandos abaixo, substitua `SEU_IP` pelo IP da Oracle e ajuste a chave SSH, o usuário e a pasta da instalação. Copie somente o certificado público; a chave da autoridade permanece no servidor.

#### Windows 10/11 — no computador do navegador

Se `Get-Command scp` não encontrar o SCP, instale o cliente em um PowerShell **como administrador**:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

No PowerShell do usuário que usa o navegador, baixe o certificado e compare o SHA256 com o exibido no servidor:

```powershell
scp -i "$env:USERPROFILE\.ssh\oracle.key" ubuntu@SEU_IP:/home/ubuntu/valheim-arm/valheim-root.crt "$env:USERPROFILE\Downloads\valheim-root.crt"
$rootCert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new("$env:USERPROFILE\Downloads\valheim-root.crt")
$sha256 = [System.Security.Cryptography.SHA256]::Create()
[BitConverter]::ToString($sha256.ComputeHash($rootCert.RawData)).Replace("-", ":")
$sha256.Dispose()
$rootCert.Dispose()
```

Se as impressões forem iguais, importe para seu usuário e reabra o navegador:

```powershell
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\valheim-root.crt" -CertStoreLocation Cert:\CurrentUser\Root
```

#### Linux Ubuntu/Debian — no computador do navegador

```sh
sudo apt-get update
sudo apt-get install -y openssh-client ca-certificates
scp -i ~/.ssh/oracle.key ubuntu@SEU_IP:/home/ubuntu/valheim-arm/valheim-root.crt ./valheim-root.crt
openssl x509 -in valheim-root.crt -noout -fingerprint -sha256
```

Se o SHA256 coincidir com o servidor:

```sh
sudo cp valheim-root.crt /usr/local/share/ca-certificates/valheim-root.crt
sudo update-ca-certificates
```

Se o navegador usar um repositório próprio de certificados, importe também nas autoridades confiáveis dele. A confiança é preservada enquanto o volume `caddy_data` for mantido.

<a id="jogadores"></a>
## Jogadores e permissões

Na aba **Jogadores**, escolha **Administradores**, **Banidos** ou **Permitidos**. Selecione um ID conhecido ou informe um ID, clique em **Adicionar à lista** e **Salvar lista**. Há remoção por botão e edição em lote. **Atualizar jogadores** renova as sugestões sem descartar sua edição.

As sugestões incluem IDs Steam identificados no log e IDs já cadastrados. Esse histórico não representa jogadores online. Para outras plataformas, cole o ID completo exibido no F2 ou no log, preservando maiúsculas e minúsculas. Não use join code, ZDOID ou identificador de conexão PlayFab. [IDs no Valheim](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/).

Preencher **Permitidos** restringe o acesso aos IDs dessa lista. Essas permissões são do jogo; não criam contas no painel web.

<a id="backups"></a>
## Backups e restauração

Na aba **Visão geral**, **Criar backup** solicita o salvamento e a parada do jogo, cria o arquivo e volta a iniciá-lo se estava rodando. Baixe os arquivos na aba **Backups**. Os backups diários automáticos aguardam o servidor vazio.

Os arquivos incluem toda a árvore de saves — também os chunks —, listas, ajustes do painel e configurações de mods. Não incluem a senha administrativa nem as credenciais da nuvem; podem incluir a senha do jogo. Mantenha uma cópia fora da instância.

Para restaurar, pare o jogo pelo painel, escolha um arquivo em **Backups** e confirme **Restaurar**. Um backup prévio é criado. A restauração substitui os saves e listas; ajustes do painel e mods não são reaplicados automaticamente. Confira o mundo selecionado antes de iniciar.

### Importar um backup do projeto

No seu computador, envie um `.tar.gz` criado por este projeto:

```sh
scp -i ~/.ssh/oracle.key NOME.tar.gz ubuntu@SEU_IP:/tmp/NOME.tar.gz
```

No servidor, instale o arquivo e gere seu checksum. Substitua `NOME.tar.gz` pelo nome original:

```sh
sudo install -d -o 10001 -g 10001 -m 0700 data/backups
sudo install -o 10001 -g 10001 -m 0600 /tmp/NOME.tar.gz data/backups/NOME.tar.gz
sudo sha256sum data/backups/NOME.tar.gz | cut -d ' ' -f 1 | sudo tee data/backups/NOME.tar.gz.sha256 >/dev/null
sudo chown 10001:10001 data/backups/NOME.tar.gz.sha256
```

Atualize a lista de backups no painel e restaure. Esse procedimento exige o formato de backup do projeto, não um `.zip` ou arquivos avulsos do mundo. Se o mundo não iniciar, consulte a [recuperação avançada](MAINTAINER.md#recuperacao-avancada).

<a id="nuvem"></a>
## Backup na nuvem

1. No seu computador, [instale o rclone](https://rclone.org/downloads/) e execute:

   ```sh
   rclone config --config ./valheim-cloud.conf
   ```

   No Windows, use `./rclone.exe` se estiver na pasta atual. Crie apenas uma conexão OAuth padrão para **OneDrive**, **Dropbox** ou **Google Drive** e autorize pelo navegador.
2. Na aba **Backups → Backup na nuvem**, importe `valheim-cloud.conf`, informe uma pasta como `Valheim/backups`, ative o envio e salve. Para manter a conta ao trocar a pasta, deixe a conexão vazia.
3. Crie um backup ou aguarde o próximo automático. O painel mostra pendências, último envio e erros. Não é necessário reiniciar o jogo para configurar.

São enviados o `.tar.gz` e seu `.sha256`. Falhas são tentadas novamente a cada cinco minutos; os pendentes são preservados pela retenção local enquanto o envio estiver ativado. Trocar conta/pasta muda o destino dos pendentes; arquivos já enviados não são reenviados. A nuvem não tem exclusão automática.

O arquivo de conexão contém credenciais: guarde-o em privado. Se a autorização expirar, autorize novamente e reimporte. Para restaurar da nuvem, baixe o backup e siga a importação acima.

<a id="mods"></a>
## Mods BepInEx

Na aba **Mods**, ative **Habilitar BepInEx**, salve e reinicie o jogo. O pacote padrão já está incluído na imagem Docker: não é necessário informar URL, calcular hash ou baixar o BepInEx no servidor. Depois, envie seus plugins pelo painel.

Desativado, o BepInEx não é injetado no processo do jogo. Seus arquivos ocupam espaço em disco, mas não são carregados. Quando ativado, o consumo depende dos plugins utilizados. Atualizações do pacote padrão acompanham novas imagens Docker; seus plugins e configurações existentes são preservados.

Em **Mods → Diagnóstico dos mods**, consulte a versão do BepInEx detectada nos logs, a conclusão do carregador, nomes/versões dos plugins e avisos de dependências ou erros de carga. O diagnóstico acompanha a sessão atual; com o jogo parado, mantém a última execução do supervisor. Reiniciar o contêiner limpa esse histórico e a próxima execução começa um diagnóstico novo. Logs antigos em disco não são usados para confirmar a sessão atual.

A lista de DLLs distingue arquivos presentes no início de arquivos novos ou alterados aguardando reinício. Clique em **Atualizar plugins** após alterações pelo SSH. Os nomes dos plugins nos logs não são necessariamente os nomes das DLLs, portanto as listas são separadas. Uma tentativa de carga ou a conclusão do carregador não comprova funcionamento do plugin: valide sua compatibilidade entrando no jogo. Se o BepInEx estiver habilitado sem confirmação nos logs, confira se o pacote está correto e se a saída de console do BepInEx está ativada.

Na aba **Mods → Enviar plugins**, pare o jogo, selecione uma DLL .NET de até **16 MiB** e clique em **Enviar plugin**. O arquivo aparece na lista de instalados e será carregado quando você iniciar o jogo. O envio não ativa o BepInEx automaticamente; ative o BepInEx conforme indicado acima.

Para atualizar uma DLL, use o mesmo nome e confirme a substituição. A versão anterior fica preservada; **Restaurar anterior** permite voltar pelo painel, também com o jogo parado. A lista informa arquivos instalados, não comprova que o plugin carregou ou é compatível. Confira os logs depois de iniciar.

Configurações ficam em `data/mods/BepInEx/config`. Para editá-las, use **Mods → Arquivos dos plugins**: atualize a lista, selecione o arquivo, altere o texto e clique em **Salvar arquivo** com o jogo parado. Inicie novamente para aplicar. Os arquivos aparecem depois que o plugin os gerar, normalmente na primeira inicialização.

O editor aceita arquivos existentes `.cfg`, `.ini`, `.json`, `.yaml`, `.yml`, `.toml`, `.txt` e `.xml`, em UTF-8, de até 32 KiB, inclusive em subpastas. Antes de salvar, mantém uma cópia anterior; **Carregar versão anterior** a coloca no editor para você conferir e salvar. Se outro processo já alterou o arquivo, atualize antes de tentar novamente. A sintaxe e as opções continuam sendo específicas do plugin. O envio aceita DLLs individuais. Pacotes ZIP, dependências nativas, arquivos auxiliares e configurações fora da pasta padrão ainda exigem instalação pelo SSH.

 Inicie pelo painel e confira o carregamento nos logs. Mods não são atualizados automaticamente. Para desativar a injeção, salve `BEPINEX=false` e reinicie; arquivos são preservados. No crossplay com consoles, escolha plugins compatíveis com clientes sem mods. A compatibilidade deste projeto com BepInEx ainda está [em validação](MAINTAINER.md#validacao).

<a id="operacao"></a>
## Operação e atualização

### Comandos do dia a dia

| Ação | Comando |
|---|---|
| Iniciar os serviços | `sudo docker compose up -d` |
| Ver estado | `sudo docker compose ps` |
| Acompanhar o jogo | `sudo docker compose logs -f valheim` |
| Reiniciar o contêiner do servidor | `sudo docker compose restart valheim` |
| Parar os serviços | `sudo docker compose stop` |
| Remover os contêineres preservando os dados | `sudo docker compose down` |

Para parar ou reiniciar só o jogo, use o painel. Comandos Docker podem deixar o painel temporariamente indisponível. Aguarde a saída graciosa, que pode levar alguns minutos. Não use `down -v`: ele também remove os volumes dos certificados.

### Rotina automática

A consulta de atualizações ocorre uma vez por dia, no horário de `RESTART_AT`. Se estiver desativado, usa `BACKUP_AT`; com ambos desativados, usa 05:00. Horários usam `TIMEZONE`. Backup e reinício coincidentes são combinados; uma atualização disponível aproveita a mesma parada.

A indicação de jogadores mostra a contagem do último registro `Connections` do jogo, a fonte, o horário e a idade da observação. Durante conexões, desconexões ou reconexões, aparece `?` até chegar um novo registro completo; a última contagem fica apenas como referência. Registros sem renovação por 11 minutos expiram. Mensagens de saída ou contagens temporárias do PlayFab não confirmam que o servidor está vazio.

Manutenções automáticas aguardam dois minutos de zero jogadores confirmado; no modo Steam, exigem também A2S. Informações desconhecidas adiam a tarefa. As ações manuais do painel dispensam essas verificações, preservando a confirmação de salvamento. A aba **Atividade** mostra o resultado.

Se o jogo registrar sinal fatal na primeira cena e permanecer ativo, o painel mostra a falha e o supervisor tenta encerrá-lo com SIGINT por até 30 segundos. Somente quando a primeira cena foi reconhecida e ainda não houve indício de carregamento do mundo, pode encerrar o processo travado com SIGKILL e agendar uma nova tentativa. Essa saída não é tratada como salvamento confirmado e não gera backup.

Se o mundo já começou a carregar ou a fase é desconhecida, o supervisor suspende essa recuperação e pede intervenção pelo painel/SSH. Um carregamento demorado, sozinho, não autoriza encerramento forçado. A recuperação não restaura nem substitui saves automaticamente.

Depois de cinco falhas consecutivas, a recuperação automática fica suspensa; corrija a causa e use **Iniciar**. Os botões **Iniciar** e **Parar** guardam a preferência do administrador. Uma parada pelo painel permanece após reiniciar ou recriar o contêiner e após reboot da instância; o painel continua disponível. Use **Iniciar** para reativar o jogo. Backup, atualização e reinício automático preservam essa preferência. Em instalações sem preferência gravada, o jogo continua iniciando automaticamente, como antes. Se não for possível gravar a preferência, a operação é recusada e o painel informa o erro. Uma parada manual não é desfeita pela recuperação automática. Não há restauração automática de backups.

### Atualizar a imagem

Crie um backup e solicite parada pelo painel. Depois:

```sh
sudo docker compose stop valheim
sudo docker compose pull valheim
sudo docker compose run --rm --no-deps valheim preflight
sudo docker compose up -d --no-build
```

Os dois serviços usam a mesma imagem; um único `pull valheim` atualiza o conteúdo disponível para ambos. Saves, configurações e certificados permanecem. Se o preflight falhar, não avance para o `up`.

`VALHEIM_IMAGE` no `.env` aceita `hfgalmeida/valheim-arm:latest`, uma tag publicada ou digest. Para voltar a uma imagem anterior, ajuste essa referência e repita os comandos. Trocar a imagem atualiza supervisor, FEX e Caddy; as atualizações do Valheim são gerenciadas separadamente pelo supervisor.

### Dados e instalação existente

| Local | Conteúdo |
|---|---|
| `data/saves` | Mundos e listas de acesso |
| `data/backups` | Backups completos e checksums |
| `data/config` | Ajustes do painel, autenticação e histórico de IDs |
| `data/mods/BepInEx` | Plugins e configurações |
| `data/cloud` | Conexão e estado dos envios para nuvem |
| Volume `caddy_data` | Autoridade e certificados HTTPS |

Para outra pasta de instalação: `sudo env VALHEIM_INSTALL_DIR=/caminho/servidor bash install-valheim.sh`. Reexecutar o instalador preserva `.env`, Compose, senhas e dados existentes e não reinicia um servidor já rodando. Os arquivos de `data` usados pelo serviço devem pertencer ao UID/GID `10001:10001`.

### Acesso automático aos arquivos

O instalador identifica o usuário SSH que executou `sudo bash install-valheim.sh` e prepara seu acesso sem comandos extras de permissão. Você pode entrar em `data`, editar ou baixar **saves**, **backups**, **mods**, configurações dos plugins e o arquivo `data/config/overrides.json`. Pare o jogo antes de alterar saves ou mods diretamente.

O supervisor sincroniza as permissões automaticamente a cada 30 segundos, inclusive após arquivos serem recriados como privados ou restaurados. Um arquivo novo pode levar até esse prazo para ficar acessível. O proprietário do serviço continua sendo UID/GID 10001; autenticação do painel, tokens de nuvem e arquivos internos não recebem essa liberação.

As permissões são preparadas para o usuário que chamou sudo. Uma instalação feita diretamente como root não identifica automaticamente outro usuário SSH. Para aplicar a melhoria a uma instalação existente, use o instalador atualizado pelo usuário SSH; ele preserva arquivos e um jogo já em execução. A sincronização contínua exige a imagem atualizada do supervisor.

### Persistência ao remover os contêineres

Todo o diretório `data` é montado do host em `/data` (`./data:/data`). Remover os contêineres, executar `docker compose down` ou trocar a imagem não remove esses arquivos. Ao reinstalar, use a mesma pasta da instalação ou indique-a por `VALHEIM_INSTALL_DIR`.

Essa persistência não cobre apagar manualmente a pasta `data`, o disco ou a instância Oracle junto com seu armazenamento. Para esse cenário, configure o backup externo na nuvem. Os certificados HTTPS ficam nos volumes `caddy_data` e `caddy_config`; não use `down -v` se quiser preservá-los.

<a id="diagnostico"></a>
## Diagnóstico

```sh
sudo docker compose logs --tail=200 valheim
sudo docker compose logs --tail=100 proxy
sudo docker stats --no-stream valheim-server valheim-proxy
sudo df -h ./data
```

| Situação | Verificação |
|---|---|
| Painel inacessível | `SERVER_IP`, estado dos serviços, regras OCI e log do proxy |
| Certificado não confiável | Importar a CA desta instalação no computador do navegador |
| Senha do painel recusada | Redefinir com `set-password`; após tentativas repetidas, aguardar cinco minutos |
| Jogo não inicia | Espaço, permissões de `data`, logs do jogo e plugins instalados |
| Tarefa automática adiada | Contagem desconhecida, jogadores presentes ou consulta Steam indisponível |
| Sem join code | Confirmar crossplay ativo, prontidão do jogo e log PlayFab |
| Restauração falha | Jogo parado, espaço livre e integridade do backup/checksum |

Não publique `.env`, credenciais ou backups em pedidos de suporte. Para investigar builds, rollback do jogo e falhas de encerramento, consulte o [guia do mantenedor](MAINTAINER.md).

## Alertas no Discord

Na aba **Configurações do servidor**, em **Alertas no Discord**, cole a URL de um webhook criado nas configurações do canal do Discord, em **Integrações → Webhooks**. Ative as notificações, salve e clique em **Enviar teste**. Deixar a URL vazia mantém a credencial salva; desativar interrompe os envios.

Os avisos cobrem falha na criação de backups, falha de envio à nuvem, suspensão da recuperação automática e menos de 6 GiB livres no disco de dados. A normalização também é notificada; para disco, exige ao menos 7 GiB livres para evitar oscilações. O monitor verifica a cada 30 segundos, sem depender de o painel estar aberto.

Cada categoria avisa uma vez ao entrar em falha e uma vez ao normalizar, com intervalo mínimo de cinco minutos. Entregas malsucedidas são tentadas novamente após cinco minutos. O estado é preservado em `data/config/alert-state.json`; o webhook fica protegido em `data/config/alerts.json`, fora dos saves, backups e arquivos compartilhados. Mensagens não contêm senhas, tokens ou logs. A situação dos alertas e erros de entrega aparecem no painel. Uma interrupção da instância ou da rede pode impedir qualquer envio; este recurso não substitui monitoramento externo.

Detalhes do protocolo: [webhooks do Discord](https://docs.discord.com/developers/resources/webhook#execute-webhook).

<a id="atualizacao-da-imagem"></a>
## Atualização da imagem Docker

Na aba **Configurações do servidor → Atualização da imagem Docker**, clique em **Atualizar imagem** e confirme. O painel acompanha download, validação, backup, troca, verificação e eventual retorno à imagem anterior. A conexão pode cair e exigir novo login; o progresso e o resultado continuam disponíveis depois de entrar novamente. A preferência de manter o jogo ligado ou parado é preservada.

O instalador prepara automaticamente o serviço local necessário ao botão. Em instalações anteriores, execute novamente o instalador atualizado; ele preserva as configurações e o jogo em execução. Quando puder interromper o jogo, execute `sudo docker compose up -d --no-deps valheim` na pasta da instalação para conectar o contêiner existente ao serviço. É necessário usar uma imagem que já contenha o botão. Nenhuma porta adicional precisa ser liberada na OCI ou no firewall. Detalhes técnicos: [agente local](MAINTAINER.md#agente-local-de-atualizacao).

O jogo é atualizado pela rotina automática; painel e FEX são atualizados pelo host. Na pasta que contém `.env`, `compose.yaml` e `update-server.py`, execute:

```bash
sudo python3 update-server.py
```

Em instalações anteriores sem o script, baixe-o uma vez na pasta da instalação:

```bash
curl -fsSL https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main/update-server.py -o update-server.py
```

O atualizador baixa e executa o preflight da nova imagem antes da parada. Depois encerra o jogo, exige a confirmação de saída limpa, gera um backup em `data/backups` e recria os dois contêineres com a nova imagem. Jogadores podem ser desconectados. O volume de dados e os certificados são preservados; o Compose não é substituído.

A validação aguarda 30 segundos de estabilidade no estado desejado: jogo online quando a preferência é ligado, ou painel disponível com jogo parado quando a preferência é parado. Também valida a configuração do proxy, com prazo padrão de 15 minutos. Não equivale a testar uma conexão de jogador. Falhas após a troca tentam retornar à imagem anterior; os saves não são revertidos automaticamente. Consulte os logs se o retorno também falhar.

```bash
# Voltar à imagem anterior preservada localmente
sudo python3 update-server.py --rollback
# Instalar uma versão específica
sudo python3 update-server.py --image hfgalmeida/valheim-arm:0.14.0
# Ampliar o prazo para mundos grandes (30 minutos)
sudo python3 update-server.py --timeout 1800
```

A imagem aplicada fica fixada em `VALHEIM_IMAGE` no `.env` usando uma tag local. O próximo comando de atualização consulta novamente `hfgalmeida/valheim-arm:latest`. Não execute `docker compose`, o instalador ou outro atualizador simultaneamente. Não remova as imagens locais `valheim-arm-local:*` enquanto precisar do retorno. Se o host reiniciar durante a troca, execute `--rollback` antes de tentar novamente.

O painel consulta a versão pública ARM64 do Docker Hub a cada seis horas e exibe a versão instalada. A indicação se refere à imagem oficial `hfgalmeida/valheim-arm`; imagens personalizadas devem ser atualizadas com `--image`. Uma falha de consulta não interrompe o jogo. O painel não recebe acesso ao Docker do host.

O atualizador também respeita o jogo parado: nessa condição valida o painel e o proxy, sem exigir que o jogo fique online. Para usar esse comportamento em uma instalação existente, mantenha `update-server.py` atualizado junto com a imagem; versões antigas do script esperavam sempre o jogo online. O retorno a uma imagem antiga sem suporte à preferência persistida é recusado enquanto a preferência for manter o jogo parado, evitando religamento inesperado.


### Histórico de manutenção

Na aba **Atividade**, consulte as operações por origem (administrador, agendamento ou recuperação), resultado e período. Os horários seguem o fuso configurado no servidor. Cada registro mostra solicitação, início, término, tempo efetivo em execução, tentativas, resultado/motivo de adiamento ou falha e nome do backup criado ou restaurado, quando houver. O tempo aguardando jogadores ou na fila não entra na duração.

O histórico permanece após recriar os contêineres. São mantidas até 200 operações concluídas dos últimos 30 dias, além das pendentes. Registros antigos podem não ter duração ou horário de início disponíveis. O nome do backup no histórico não garante que o arquivo ainda esteja disponível: a retenção dos backups é independente. Falhas ao gravar o histórico aparecem nessa aba e não impedem a parada do jogo.
