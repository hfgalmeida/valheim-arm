# Valheim ARM

Seu mundo Valheim na Oracle ARM, com instalação pelo Docker Hub e manutenção automática.

**Precisa de:** Ubuntu 24.04 ou 26.04 LTS ARM64, páginas de memória de 4 KiB, 2 OCPUs, 12 GB de RAM, aproximadamente 30 GB livres e acesso sudo. A imagem pública é `hfgalmeida/valheim-arm:latest`.

## Instalar

Na instância Oracle, libere **UDP 2456–2457** para jogar e **TCP 443** para o painel, tanto na rede OCI quanto no firewall local. Mantenha o acesso SSH. Depois execute:

<details>
<summary>Como liberar as portas na OCI e no Ubuntu</summary>

**Na OCI:** abra **Compute → Instances → sua instância**. Na VNIC principal, abra a **Subnet** e depois **Security Lists**. Selecione a lista associada à sub-rede e use **Add Ingress Rules**:

| Uso | Protocolo | Porta de destino | Origem (CIDR) |
|---|---|---|---|
| Jogo | UDP | `2456-2457` | `0.0.0.0/0` |
| Painel | TCP | `443` | IP público do seu computador seguido de `/32` |
| SSH/SCP | TCP | `22` | Preserve a regra de acesso SSH existente |

Deixe a porta de origem como **All** e **Stateless** desmarcado (regra stateful). Para o painel, por exemplo, o IP `203.0.113.25` vira `203.0.113.25/32`. Se usar um **Network Security Group**, adicione as mesmas regras de entrada no NSG associado à VNIC. Veja o [guia de regras da OCI](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm).

**No Ubuntu, se você usa UFW**, execute na sessão SSH do servidor (porta SSH padrão 22):

```sh
sudo ufw allow 22/tcp
sudo ufw allow 2456:2457/udp
sudo ufw allow 443/tcp
sudo ufw status verbose
```

Se o status for `inactive`, o UFW não está filtrando o tráfego. O Docker configura suas próprias regras para as portas publicadas e esse tráfego pode passar antes do UFW; use as regras da OCI para restringir quem acessa o painel. Preserve regras de firewall existentes. Consulte [Docker e UFW](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw).

</details>

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

Quer automatizar essa cópia? No painel, **Backup na nuvem** permite enviar os novos backups para **OneDrive, Dropbox ou Google Drive**. Autorize sua conta uma vez no computador e importe a conexão no painel; veja [como conectar](docs/REFERENCE.md#nuvem).

## Painel

Acesse **https://SEU_IP** e entre com a senha administrativa definida durante a instalação. Lá você pode mudar configurações, habilitar crossplay, administrar usuários e baixar/restaurar backups. Com crossplay ativo, o código de entrada aparece no topo do painel assim que o jogo registra a sessão. Compartilhe esse código e a senha do jogo com os jogadores; após reiniciar, consulte o código novamente.

O HTTPS por IP usa um certificado local. Baixe e importe o certificado para confiar nele no navegador. Isso é opcional para começar a jogar.

<a id="certificado"></a>
<details>
<summary>Baixar o certificado com SCP no Windows ou Linux</summary>

**1. Prepare o certificado no servidor**, pela sessão SSH, depois de iniciar a instalação:

```sh
cd ~/valheim-arm
sudo docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./valheim-root.crt
sudo chmod 644 valheim-root.crt
```

Copie somente esse certificado público. Nos exemplos abaixo, substitua `SEU_IP` pelo IP da Oracle e o caminho `oracle.key` pela chave privada que você usa no SSH. Os comandos seguintes são executados **no seu computador**, onde está o navegador. O usuário padrão do Ubuntu na OCI é `ubuntu`; ajuste o usuário e `/home/ubuntu/valheim-arm` se sua instalação usar outros caminhos.

**2. No Windows 10/11:** o SCP vem no **OpenSSH Client**. Confira no PowerShell com `Get-Command scp`. Se não estiver instalado, abra o **PowerShell como administrador** e execute:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

Depois, no PowerShell do seu usuário, baixe o arquivo para Downloads:

```powershell
scp -i "$env:USERPROFILE\.ssh\oracle.key" ubuntu@SEU_IP:/home/ubuntu/valheim-arm/valheim-root.crt "$env:USERPROFILE\Downloads\valheim-root.crt"
```

Para confiar no certificado apenas para seu usuário Windows:

```powershell
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\valheim-root.crt" -CertStoreLocation Cert:\CurrentUser\Root
```

Feche e reabra o navegador. A instalação do cliente segue o [guia OpenSSH da Microsoft](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse).

**3. No Linux (Ubuntu/Debian):** instale o cliente OpenSSH e baixe para a pasta atual:

```sh
sudo apt-get update
sudo apt-get install -y openssh-client ca-certificates
scp -i ~/.ssh/oracle.key ubuntu@SEU_IP:/home/ubuntu/valheim-arm/valheim-root.crt ./valheim-root.crt
```

Para adicionar à lista de autoridades confiáveis do sistema:

```sh
sudo cp valheim-root.crt /usr/local/share/ca-certificates/valheim-root.crt
sudo update-ca-certificates
```

Se o navegador usar uma lista própria de certificados, importe também `valheim-root.crt` nas suas autoridades confiáveis. Mais detalhes na [referência do certificado](docs/REFERENCE.md#painel).

</details>

Uma parada solicitada pelo painel permanece parada até você iniciar novamente. Atualizar a imagem Docker do painel/FEX e restaurar backups são tarefas do administrador; o jogo recebe suas próprias atualizações automaticamente.

## Precisa ajustar algo?

As opções extras, importação de mundos, mods e recuperação estão reunidas na [referência avançada](docs/REFERENCE.md). Para instalar e jogar, não é preciso seguir esse guia.

O instalador preserva uma instalação existente e não reinicia um mundo que já esteja rodando. Para retomá-lo após uma falha de instalação, execute novamente na mesma pasta de origem.

Para quem mantém o projeto: todo push na `main` tenta publicar a versão de [VERSION](VERSION). Você pode manter o mesmo número até a imagem ser publicada; depois, aumente a versão. Veja [publicação](docs/REFERENCE.md#publicacao).

A validação completa de FEX, crossplay, BepInEx e desempenho na Oracle é acompanhada no [roteiro de aceitação](docs/REFERENCE.md#validacao).
