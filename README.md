# Valheim ARM

Servidor Valheim na Oracle ARM: instale com Docker, jogue e gerencie pelo navegador. Atualizações do jogo, backups e reinícios são automáticos.

## Funcionalidades

- **Instalação via Docker Compose**, com imagem pronta para ARM64 e execução do Valheim pelo FEX.
- **Painel web com HTTPS**, métricas em tempo real e controles para iniciar, parar e reiniciar o servidor. [Detalhes](docs/REFERENCE.md#painel).
- **Atualizações e manutenção automáticas**, com verificação de jogadores, salvamento antes das paradas e recuperação após falhas. [Detalhes](docs/REFERENCE.md#operacao).
- **Backups automáticos**, download e restauração pelo painel, com envio opcional para OneDrive, Dropbox ou Google Drive. [Detalhes](docs/REFERENCE.md#backups).
- **Configuração do mundo e crossplay**, modificadores e listas de administradores, banidos e jogadores permitidos. [Detalhes](docs/REFERENCE.md#configuracao).
- **Suporte opcional a mods BepInEx**, com instalação de pacote verificado. [Detalhes](docs/REFERENCE.md#mods).

## Instalar

Você precisa de Ubuntu 24.04 ou 26.04 LTS ARM64, páginas de memória de 4 KiB, 2 OCPUs, 12 GB de RAM, cerca de 30 GB livres e acesso sudo.

Na OCI, libere as portas abaixo, preservando seu acesso SSH:

| Uso | Porta | Origem |
|---|---|---|
| Jogo | UDP 2456–2457 | `0.0.0.0/0` |
| Painel | TCP 443 | `0.0.0.0/0` |

Use regras de entrada stateful e porta de origem **All**. [Detalhes de rede e firewall](docs/REFERENCE.md#rede). O instalador libera essas portas quando o UFW está ativo; outros firewalls locais precisam permitir o tráfego.

A origem `0.0.0.0/0` permite acessar o painel de qualquer rede, sem cadastrar o IP do seu computador. A tela de login fica pública; proteja o acesso com uma senha administrativa forte.

Na sessão SSH do servidor, execute:

```sh
curl -fL https://raw.githubusercontent.com/hfgalmeida/valheim-arm/main/install.sh -o install-valheim.sh
sudo bash install-valheim.sh
```

Informe o **IP público da instância Oracle**, o nome do servidor, o nome do mundo, a senha do jogo e uma senha administrativa para o painel. O instalador prepara o Docker, baixa a imagem pública `hfgalmeida/valheim-arm:latest` e inicia os serviços em `./valheim-arm`.

## Jogar

O primeiro download e carregamento do mundo podem levar alguns minutos. Para acompanhar:

```sh
cd valheim-arm
sudo docker compose logs -f valheim
```

Após o carregamento, adicione **SEU_IP:2456** no Valheim e entre com a senha do jogo. **Ctrl+C** sai da visualização dos logs e mantém o servidor ligado.

## Acessar o painel

Abra **https://SEU_IP** e entre com a senha administrativa definida na instalação. O HTTPS já vem configurado; para o navegador confiar no certificado local, siga uma vez o [guia de certificado para Windows ou Linux](docs/REFERENCE.md#certificado).

Pelo painel, você pode ajustar o servidor, administrar usuários do jogo e baixar/restaurar backups. Ao habilitar crossplay e reiniciar, o código de entrada aparece quando a sessão fica disponível.

## Manutenção automática

| Tarefa | Padrão |
|---|---|
| Atualizações do jogo | Uma vez ao dia, junto ao reinício diário |
| Save do mundo | A cada 30 minutos, com backups internos |
| Backup completo e reinício | Diariamente às 05h; backup também antes de atualizar |
| Retenção | Sete dias e quatro semanas |
| Recuperação de falhas | Novas tentativas automáticas, com recuperação rápida na inicialização |

Os horários usam **America/Sao_Paulo**. Manutenções automáticas aguardam o servidor ficar vazio e confirmam o salvamento antes de criar backups. Você pode configurar o [envio automático para OneDrive, Dropbox ou Google Drive](docs/REFERENCE.md#nuvem) pelo painel.

Saves, configurações editáveis, mods e backups ficam em `valheim-arm/data`, com acesso preparado automaticamente para o usuário SSH. Eles permanecem após remover os contêineres. Guarde também uma cópia dos backups fora da instância.

Configurações avançadas, certificado, importação de mundos, mods e administração estão na [referência](docs/REFERENCE.md).

Para desenvolver ou publicar imagens, consulte o [guia do mantenedor](docs/MAINTAINER.md).
