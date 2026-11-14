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

Prefira a aba **Configuração** do painel. Ajustes do painel prevalecem sobre o `.env`; **Usar Compose** remove essa substituição. Alterações do jogo são aplicadas na próxima inicialização; horários são lidos pelo agendador. Trocar o nome do mundo seleciona ou cria outro save, preservando o anterior.

Para alterar o `.env`, use `sudo nano .env` e aplique com `sudo docker compose up -d`. Isso pode recriar os contêineres; faça um backup e solicite a parada pelo painel antes. `VALHEIM_IMAGE`, `SERVER_IP` e opções `FEX_*` são configurados somente pelo `.env`.

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
| `AUTO_UPDATE` | true | Consulta uma vez por dia, no horário do reinício, e agenda atualização do jogo |
| `RETAIN_DAILY` / `RETAIN_WEEKLY` | 7 / 4 | Dias e semanas distintos representados nos arquivos completos |
| `BEPINEX` | false | Injeção do pack Linux, com plugins locais |
| `BEPINEX_URL` / `BEPINEX_SHA256` | vazios | URL HTTPS e hash obrigatório quando habilitado |

A porta do jogo é fixada em 2456 no Compose, com UDP 2456–2457 publicado. O painel usa TCP 443; a API 8080 não é publicada.

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

Acesse **https://SEU_IP**. As abas são **Visão geral**, **Configuração**, **Jogadores**, **Backups** e **Atividade**. Ações manuais solicitam a parada imediatamente, mesmo com jogadores conectados, e aguardam o jogo salvar e encerrar.

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

Na aba **Configuração**, defina `BEPINEX=true`, `BEPINEX_URL` e `BEPINEX_SHA256` de um **BepInExPack_Valheim Linux** confiável, contendo `libdoorstop.so` e `BepInEx/core`. Para calcular o hash do pacote baixado:

```sh
sha256sum BepInExPack_Valheim.zip
```

No PowerShell: `Get-FileHash .\BepInExPack_Valheim.zip -Algorithm SHA256`.

Na aba **Configuração → Enviar plugins**, pare o jogo, selecione uma DLL .NET de até **16 MiB** e clique em **Enviar plugin**. O arquivo aparece na lista de instalados e será carregado quando você iniciar o jogo. O envio não ativa o BepInEx automaticamente; mantenha o pacote configurado acima.

Para atualizar uma DLL, use o mesmo nome e confirme a substituição. A versão anterior fica preservada; **Restaurar anterior** permite voltar pelo painel, também com o jogo parado. A lista informa arquivos instalados, não comprova que o plugin carregou ou é compatível. Confira os logs depois de iniciar.

Configurações ficam em `data/mods/BepInEx/config`. Para editá-las, use **Configuração → Arquivos dos plugins**: atualize a lista, selecione o arquivo, altere o texto e clique em **Salvar arquivo** com o jogo parado. Inicie novamente para aplicar. Os arquivos aparecem depois que o plugin os gerar, normalmente na primeira inicialização.

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

Manutenções automáticas aguardam dois minutos de zero jogadores confirmado; no modo Steam, exigem também A2S. Informações desconhecidas adiam a tarefa. As ações manuais do painel dispensam essas verificações, preservando a confirmação de salvamento. A aba **Atividade** mostra o resultado.

Depois de cinco falhas consecutivas, a recuperação automática fica suspensa; corrija a causa e use **Iniciar**. Uma parada manual não é desfeita pela recuperação automática. Não há restauração automática de backups.

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
