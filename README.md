# Valheim ARM

Servidor dedicado Valheim para **Oracle Ampere A1, 2 OCPUs, 12 GB e Ubuntu 24.04 ARM64**, instalado com Docker Compose. O supervisor, o painel e os backups são ARM64 nativos. Somente o jogo, SteamCMD e componentes x86 do BepInEx passam pelo FEX; não há Box64, QEMU ou Wine.

Inclui painel em português por HTTPS/IP, gráficos em tempo real, configurações persistentes, listas de usuários do jogo, crossplay opcional, modificadores individuais, BepInEx opcional, backups para download, atualização do jogo e reinício diário com verificação de jogadores.

Para gerar e publicar a imagem automaticamente pelo GitHub Actions, veja [Publicação no Docker Hub](docs/DOCKERHUB.md), com as variáveis/secrets necessários neste repositório e instruções para usar a imagem pronta na Oracle.

**Estado de validação:** testes locais do supervisor executados; imagem Docker/FEX, BepInEx, crossplay e desempenho ainda precisam da [aceitação na Oracle](docs/ACCEPTANCE.md). Não há credenciais OCI nem Docker nesta estação. A primeira implantação deve começar em um mundo de teste, seguindo a prova funcional antes de importar um save importante.

## 1. Preparar a instância

Use Ubuntu 24.04 ARM64, kernel >= 5.15, páginas de 4 KiB e CPU Ampere A1. Reserve aproximadamente 30 GB livres para construir a imagem, armazenar duas instalações do jogo e manter backups; o espaço necessário cresce com os mundos. O preflight exige pelo menos 6 GiB livres para a operação inicial. O contêiner do jogo tem limite de 10 GiB, deixando margem para o sistema e o proxy.

Confirme a plataforma:

```sh
uname -m
getconf PAGESIZE
uname -r
df -h .
```

Saídas esperadas: `aarch64`, `4096` e kernel suportado. Instale Docker pelo repositório oficial:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf '%s\n' \
  'Types: deb' \
  'URIs: https://download.docker.com/linux/ubuntu' \
  'Suites: noble' \
  'Components: stable' \
  'Architectures: arm64' \
  'Signed-By: /etc/apt/keyrings/docker.asc' | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker compose version
```

Se já houver Docker de outra origem, siga a [documentação oficial para resolver conflitos de pacotes](https://docs.docker.com/engine/install/ubuntu/).

Na VNIC/sub-rede OCI, configure NSG ou Security List com entrada TCP **443** para o administrador, SSH **22** restrito ao administrador e UDP **2456–2457** para os jogadores Steam. Preserve o acesso de saída necessário para DNS, HTTPS, Steam e PlayFab. Crossplay usa relay e pode dispensar entrada UDP, mas continua exigindo saída de rede. Consulte as [regras OCI](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securitylists.htm) e o [manual Valheim](https://www.valheimgame.com/support/a-guide-to-dedicated-servers/).

Verifique também o firewall local: `sudo iptables -S`, `sudo iptables -S DOCKER-USER` e `sudo ufw status`. As portas publicadas pelo Docker podem contornar regras UFW; a restrição do painel deve existir no NSG/Security List. Se houver política local de bloqueio no encaminhamento, autorize os mesmos destinos na cadeia `DOCKER-USER`, antes da regra de rejeição, e persista conforme a política da máquina. Não substitua o firewall existente nem remova regras de SSH.

## 2. Configurar e construir

Copie ou clone este repositório para a instância e entre em sua pasta:

```sh
cp .env.example .env
chmod 600 .env
nano .env
sudo install -d -o 10001 -g 10001 -m 0700 data
sudo docker compose config --quiet
sudo docker compose build --pull
sudo docker compose run --rm --no-deps valheim preflight
sudo docker compose run --rm --no-deps valheim set-password
sudo docker compose up -d
sudo docker compose logs -f valheim
```

Defina `SERVER_IP` com o IPv4 público real e `SERVER_PASSWORD` com a senha do jogo (mínimo cinco caracteres). A senha administrativa, com mínimo de 12 caracteres, é solicitada no terminal pelo comando `set-password`; ela é diferente da senha do jogo.

O primeiro build compila FEX nativamente e baixa um rootfs Ubuntu x86 com bibliotecas i386. Pode demorar em duas OCPUs. O build usa duas tarefas por padrão; `BUILD_JOBS=1` reduz pressão de memória. Não configure `platform: linux/amd64`: isso emularia também todo o contêiner.

O FEX mais recente é resolvido **durante a construção**, e sua tag, commit e hash são registrados em `/opt/build-info.json`. O rootfs é extraído para `/opt/rootfs`, sem FUSE e sem privilégios adicionais. O supervisor invoca FEX explicitamente, sem registrar binfmt no host.

## 3. Acessar o painel

O endereço é `https://SEU_IP`. O Caddy emite um certificado local com esse IP e renova os certificados automaticamente. Para confiar na autoridade dessa instalação, obtenha o certificado por SSH:

```sh
sudo docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./valheim-root.crt
openssl x509 -in valheim-root.crt -noout -fingerprint -sha256
```

Transfira `valheim-root.crt` via SCP para seu computador e importe como autoridade confiável no sistema/navegador. No Windows, use o gerenciador de certificados do usuário → Autoridades de Certificação Raiz Confiáveis → Importar. Compare a impressão digital com a exibida via SSH. Instale somente o certificado público `root.crt`; não copie a chave da autoridade. Consulte o [HTTPS local do Caddy](https://caddyserver.com/docs/automatic-https#local-https).

Entre com a senha definida no servidor. Para trocá-la:

```sh
sudo docker compose exec valheim python -m app.cli set-password
```

A troca invalida as sessões existentes. Sessões expiram em oito horas e também desaparecem ao reiniciar o supervisor. Depois de cinco tentativas de login em cinco minutos, novas tentativas ficam bloqueadas até a janela expirar. O proxy compartilha o limite entre os clientes.

Se mudar o IP, atualize `SERVER_IP` no `.env` e execute `sudo docker compose up -d`. O Caddy emitirá um certificado para o novo IP usando a mesma autoridade persistente.

## 4. Operação diária

As configurações seguem **padrões → Compose → ajustes do painel**. Um ajuste do painel continua prevalecendo após recriar o contêiner. Use “Usar Compose” para removê-lo. Valores do jogo são aplicados na próxima inicialização; horários e retenção são lidos pelo agendador sem reiniciar o jogo. A indicação de alterações pendentes compara a configuração atual com a da última inicialização.

O preset é enviado antes dos modificadores individuais. Um modificador vazio não envia argumento. As chaves `KEY_*` enviam `-setkey` quando habilitadas; desabilitá-las não remove uma chave já persistida no mundo. Para remover uma chave existente, administre o mundo com os recursos do jogo ou aplique um preset adequado, após backup. Veja [CONFIGURATION.md](docs/CONFIGURATION.md) para os valores e efeitos.

Trocar `WORLD_NAME` seleciona um save existente ou cria outro mundo, sem renomear ou excluir o anterior. O painel exige confirmação. Admins, banidos e permitidos são as listas do jogo, não contas do painel. Use o ID exato da plataforma exibido no F2/log. Uma lista de permitidos não vazia impede a entrada dos demais jogadores.

Por padrão, o supervisor consulta atualizações do jogo a cada 30 minutos e agenda reinício e backup às **05:00 de America/Sao_Paulo**. `off` desativa um horário. Tarefas vencidas no mesmo dia são consideradas ao ligar a instância. Tarefas coincidentes são combinadas; intenções de manutenção agendada são persistidas.

Todas as operações passam por uma fila. A parada exige snapshot de zero conexões por dois minutos. Em Steam, uma consulta A2S deve confirmar zero; em crossplay, o supervisor usa o snapshot dos logs. Eventos de conexão/desconexão invalidam a contagem até outro snapshot completo. Snapshots expiram em 11 minutos; formato ausente, alterado ou divergente impede manutenção. Como o jogo pode emitir snapshots aproximadamente a cada dez minutos, o adiamento pode durar além do horário configurado.

Antes do SIGINT, o supervisor suspende brevemente o grupo de processos, drena a saída capturada e verifica novamente a contagem; isso impede que o jogo processe novas admissões durante a verificação final, incluindo pelo relay PlayFab. Se detectar conexão, retoma o processo e cancela a parada. A compatibilidade desse fluxo com FEX e os formatos atuais dos logs faz parte da aceitação ARM; não é garantia para qualquer mod ou versão futura.

Tarefas automáticas ocupadas/incertas tentam novamente a cada cinco minutos, sem expulsar jogadores. Operações manuais recusadas mostram o erro e devem ser solicitadas novamente. `docker compose stop/down` é uma ordem explícita do administrador e encerra o jogo graciosamente mesmo com jogadores; avise-os antes. O prazo Docker é de seis minutos. Não use `kill` para uma parada normal.

O healthcheck verifica o supervisor; consulte o estado do jogo e o log no painel. Uma falha do processo do jogo fica visível e exige ação do administrador, evitando reinícios em ciclo por um mod quebrado.

## 5. Backups, mods e recuperação

Backups nativos permanecem habilitados durante o jogo. Arquivos completos são criados diariamente, antes de atualizações e sob solicitação, sempre após parada limpa com confirmação `World saved`. Se houver jogadores continuamente, os arquivos completos serão adiados; os backups nativos continuam funcionando.

Arquivos completos contêm saves, listas, ajustes do painel e configurações de mods, com manifesto e hashes SHA256. A senha administrativa não é exportada. Ajustes podem incluir a senha do jogo: trate os arquivos como privados. A retenção mantém representantes de sete dias e quatro semanas, além do mais recente, podendo descartar backups intermediários do mesmo dia.

Baixe um `.tar.gz` pelo painel para guardar uma cópia fora da instância. Backups no mesmo volume não protegem contra perda desse disco. A primeira versão não inclui envio para Object Storage.

Para restaurar: pare o jogo pelo painel, selecione um arquivo, confirme a restauração e acompanhe a operação. Um backup prévio é criado. A restauração substitui o diretório de saves, incluindo listas; configurações do supervisor e mods arquivadas são preservadas para recuperação manual, mas não são reaplicadas automaticamente. Selecione no painel o mundo indicado pelo resultado e inicie o jogo. Para importar um backup baixado anteriormente, envie o `.tar.gz` por SCP para `data/backups`, preserve seu nome original, gere o sidecar com `sha256sum ARQUIVO.tar.gz | cut -d ' ' -f 1 > ARQUIVO.tar.gz.sha256` e ajuste proprietário para `10001:10001`.

Para importar um mundo do PC: pare o jogo, copie os arquivos `.db` e `.fwl` correspondentes para `data/saves/worlds_local/`, ajuste o proprietário e selecione o mesmo nome em `WORLD_NAME`. Preserve uma cópia externa dos arquivos originais.

Para BepInEx, obtenha um **BepInExPack_Valheim Linux** de fonte confiável, calcule seu SHA256 e configure `BEPINEX=true`, `BEPINEX_URL=https://...` e `BEPINEX_SHA256=...`. O pacote deve conter `libdoorstop.so` junto de `BepInEx/core`. As opções podem ser definidas no painel. Plugins ficam em `data/mods/BepInEx/plugins`, configurações em `data/mods/BepInEx/config` e patchers em `data/mods/BepInEx/patchers`. Copie-os com o jogo parado e proprietário `10001:10001`. O [BepInEx](https://docs.bepinex.dev/articles/user_guide/installation/) deve corresponder ao jogo Linux x86_64 e ao runtime esperado pelos plugins. Não há atualização automática de mods. Clientes de console não instalam plugins; use mods compatíveis com clientes vanilla ao habilitar crossplay.

Consulte [OPERATIONS.md](docs/OPERATIONS.md) para atualização do FEX, rollback e diagnóstico, e [ACCEPTANCE.md](docs/ACCEPTANCE.md) para o roteiro de validação em ARM.

## Desenvolvimento

Python 3.12 e Node para conferir a sintaxe do JavaScript:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
node --check app/static/panel.js
```

Os testes usam um runtime isolado, sem executar o jogo nem acessar Steam/Oracle. A API privada do painel está documentada em [API.md](docs/API.md).

O teste opcional de navegador usa a interface real com respostas de API determinísticas, verificando login, formulários, ações e layout móvel. Não representa uma sessão real do jogo:

```sh
pip install -r requirements-browser.txt
python -m playwright install chromium
RUN_BROWSER_TESTS=1 python -m pytest -q tests/test_panel_browser.py
```

No Windows com Edge instalado, use `$env:RUN_BROWSER_TESTS='1'` e `$env:BROWSER_CHANNEL='msedge'` no PowerShell; não é necessário baixar Chromium nesse caso. A captura gerada fica em `.artifacts/panel.png` e contém dados de teste. O workflow `Checks` executa os testes do supervisor e oferece build ARM/preflight manual por `workflow_dispatch`. O workflow `Publish Docker Hub` também constrói e verifica a imagem antes de publicar, nos disparos descritos em [DOCKERHUB.md](docs/DOCKERHUB.md). Esses testes não substituem a aceitação OCI.
