# Valheim ARM

Seu mundo Valheim na Oracle ARM, com instalação pelo Docker Hub e manutenção automática.

**Precisa de:** Ubuntu 24.04 ARM64, 2 OCPUs, 12 GB de RAM, aproximadamente 30 GB livres e acesso sudo. A imagem pública é `hfgalmeida/valheim-arm:latest`.

## Instalar

Na instância Oracle, libere **UDP 2456–2457** para jogar e **TCP 443** para o painel, tanto na rede OCI quanto no firewall local. Mantenha o acesso SSH. Depois execute:

```sh
curl -fL https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main/install.sh -o install-valheim.sh
bash install-valheim.sh
```

Informe o IP público, o nome do servidor, a senha do jogo e uma senha para o painel. O instalador prepara o Docker quando necessário, baixa as imagens prontas, verifica a plataforma e inicia o servidor. A instalação fica em `./valheim-arm`.

O primeiro download do jogo demora alguns minutos. Para acompanhar:

```sh
cd valheim-arm
sudo docker compose logs -f valheim
```

Quando aparecer `Game server connected`, adicione **SEU_IP:2456** no Valheim, entre com a senha do jogo e jogue. Use **Ctrl+C** para sair da visualização do log; o servidor continua ligado.

## O que acontece sozinho

| Tarefa | Padrão |
|---|---|
| Atualização do jogo | Verifica a cada 30 minutos e atualiza quando o servidor está vazio |
| Save do mundo | A cada 30 minutos, com backups internos do jogo |
| Backup completo | Diário às 05h e antes de atualizar o jogo |
| Retenção dos backups | Sete dias e quatro semanas representados nos arquivos |
| Reinício diário | Às 05h, junto com o backup |
| Recuperação de falhas | Nova tentativa de iniciar após cinco minutos |

Os horários usam **America/Sao_Paulo**. Manutenções que param o jogo aguardam os jogadores saírem; quando a contagem é incerta, a tarefa é adiada. Backups completos também aguardam essa janela. Os backups internos do jogo continuam durante a partida.

Os saves ficam em `valheim-arm/data`. Baixe uma cópia dos backups pelo painel para guardá-la fora da instância.

## Painel

Acesse **https://SEU_IP** e entre com a senha administrativa definida durante a instalação. Lá você pode mudar configurações, habilitar crossplay, administrar usuários e baixar/restaurar backups.

O HTTPS por IP usa um certificado local. Para confiar nele no navegador, siga a [orientação do certificado](docs/REFERENCE.md#painel). Isso é opcional para começar a jogar.

Uma parada solicitada pelo painel permanece parada até você iniciar novamente. Atualizar a imagem Docker do painel/FEX e restaurar backups são tarefas do administrador; o jogo recebe suas próprias atualizações automaticamente.

## Precisa ajustar algo?

As opções extras, importação de mundos, mods e recuperação estão reunidas na [referência avançada](docs/REFERENCE.md). Para instalar e jogar, não é preciso seguir esse guia.

O instalador preserva uma instalação existente e não reinicia um mundo que já esteja rodando. Para retomá-lo após uma falha de instalação, execute novamente na mesma pasta de origem.

Para quem mantém o projeto: todo push na `main` tenta publicar a versão de [VERSION](VERSION). Você pode manter o mesmo número até a imagem ser publicada; depois, aumente a versão. Veja [publicação](docs/REFERENCE.md#publicacao).

A validação completa de FEX, crossplay, BepInEx e desempenho na Oracle é acompanhada no [roteiro de aceitação](docs/REFERENCE.md#validacao).
