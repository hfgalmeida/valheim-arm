# Operação e recuperação

## Atualizar jogo, FEX e mods

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

`VALHEIM_IMAGE` no `.env` seleciona a imagem: use `namespace/valheim-arm:latest`, uma tag publicada como `:v1.0.0` ou um digest `namespace/valheim-arm@sha256:...` para fixar exatamente o artefato. Ao escolher outra referência, salve `.env` antes do `pull`. Se o download ou preflight falhar, não avance para o `up`; confira o erro e volte à referência anterior se necessário. Os volumes de saves, configurações e backups são preservados.

O `pull` só obtém uma versão nova do FEX quando o mantenedor já publicou outra imagem. `FEX_VERSION` e `BUILD_JOBS` são opções de construção usadas no CI, não configurações da instalação. Para atualizar o Caddy, planeje uma janela para o painel, execute `sudo docker compose pull proxy` e `sudo docker compose up -d --no-build proxy`; a autoridade HTTPS permanece no volume persistente.

Para reverter **a imagem do supervisor/FEX**, pare o serviço, ajuste `VALHEIM_IMAGE` para a tag/digest anterior, execute `pull`, preflight e `up -d --no-build`. Preserve previamente um backup e confira compatibilidade dos dados. Isso não reverte automaticamente a instalação Valheim em `data/installs` nem os saves; o rollback do jogo é descrito abaixo.

Para atualizar BepInEx, faça backup, pare o jogo, altere URL e SHA256 do pack, mantenha os plugins/configurações persistentes e reinicie. Verifique a compatibilidade dos plugins com a versão do jogo. Para desativar a injeção, salve `bepinex=false` no painel e reinicie; as pastas são preservadas. Alguns mods alteram saves e podem exigir seus próprios procedimentos de remoção.

## Rollback de instalação

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

## Queda de energia ou parada incompleta

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

## Diagnóstico

| Sintoma | Verificação |
|---|---|
| Sem acesso ao painel | IP no `.env`, `docker compose ps`, log do proxy, NSG/Security List e cadeia DOCKER-USER |
| Certificado não confiável | Importar a CA desta instalação via SSH; confirmar IP e impressão digital |
| Login sempre recusado | Definir senha pelo comando local; aguardar cinco minutos se houve tentativas excessivas |
| SteamCMD falha | Saída DNS/HTTPS/Steam, espaço livre, rootfs com loader i386 e log do supervisor |
| Jogo não inicia | Mensagens do FEX, versão registrada, permissões 10001, arquivo do mundo e plugins |
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

Não publique `.env`, `data/config/overrides.json` ou backups em tickets. Logs removem as senhas conhecidas do jogo, mas ainda podem conter IDs dos jogadores e join codes. O healthcheck não reinicia automaticamente um jogo que falhou; use o estado do painel para decidir a recuperação.
