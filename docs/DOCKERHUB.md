# Publicar no Docker Hub com GitHub Actions

O workflow `.github/workflows/dockerhub.yaml` testa o supervisor, constrói a imagem em um runner **ARM64 nativo**, executa o preflight do FEX e publica a mesma imagem testada no Docker Hub. Não usa QEMU nem outro emulador para construir a imagem.

Este guia é para o mantenedor que publica a imagem. Para instalar o servidor, siga o [README](../README.md): somente Docker Compose e arquivos de configuração são necessários na Oracle. A compilação fica no GitHub Actions.

## Configuração deste repositório

Configurações de um projeto não são copiadas automaticamente para outro. Secrets e variables de repositório precisam existir em **hfgalmeida/valheim-arm**. Se forem de uma organização, conceda acesso a este repositório na política desses secrets/variables. Secrets vinculados a um Environment não são lidos por este workflow, que não seleciona Environment.

No GitHub, abra **Settings → Secrets and variables → Actions** neste repositório:

| Tipo | Nome | Valor |
|---|---|---|
| Variable | `DOCKERHUB_USERNAME` | Seu usuário real no Docker Hub; não precisa ser igual ao GitHub |
| Secret | `DOCKERHUB_TOKEN` | Token Docker Hub com permissão de leitura e escrita no repositório de destino |
| Variable opcional | `DOCKERHUB_IMAGE` | Nome completo, por exemplo `seuusuario/valheim-arm` ou `suaorganizacao/valheim-arm`; sem registry ou tag |

Se `DOCKERHUB_IMAGE` estiver ausente, o destino será `DOCKERHUB_USERNAME/valheim-arm`. O workflow também aceita `DOCKERHUB_USERNAME` como secret para aproveitar esse padrão quando já utilizado em outros projetos.

Crie o repositório `valheim-arm` no namespace desejado no Docker Hub e escolha sua visibilidade. Crie ou reutilize um token autorizado a escrever nesse destino. Um token restrito ao outro projeto precisa receber acesso ao novo destino ou ser substituído. Não cadastre o token no código, no `.env` da Oracle ou nesta conversa.

Confirme que GitHub Actions está habilitado e que a política do repositório permite as actions `actions/*` e `docker/*`. O runner é `ubuntu-24.04-arm`; disponibilidade e cobrança dependem do plano/visibilidade do repositório. Não substitua por um runner x86, pois o Dockerfile exige compilação nativa ARM64.

Fontes: [secrets do GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets), [autenticação e publicação Docker](https://docs.docker.com/build/ci/github-actions/push-multi-registries/), [runners GitHub](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Disparos e tags

| Disparo | Tags publicadas |
|---|---|
| Push na `main` | `latest` e `sha-<commit completo>` |
| Push de tag `v*`, por exemplo `v1.0.0` | `v1.0.0` e `sha-<commit completo>` |
| Execução manual na `main` | `latest` e `sha-<commit completo>` |
| Execução manual em outra branch | Somente `sha-<commit completo>` |

Tags de versão não atualizam `latest`; essa tag acompanha a `main`. Pull requests não publicam imagens. Execuções que alteram as mesmas tags são serializadas.

Após enviar os arquivos ao GitHub e cadastrar as credenciais, abra **Actions → Publish Docker Hub → Run workflow**. O formulário permite `fex_version=latest` ou uma tag estável como `FEX-2611`. Pushes automáticos usam a última release estável.

A versão do FEX é resolvida antes do build e passada como argumento fixo. Assim, o cache não esconde uma release nova do emulador. A tag FEX aparece nos labels da imagem e sua proveniência completa em `/opt/build-info.json`. O cache de construção é armazenado no GitHub Actions.

Uma nova execução do mesmo commit pode incorporar um FEX/rootfs/base atualizado; use o digest Docker para identificar exatamente a imagem publicada. O resumo do workflow lista as tags, e os logs de push informam o digest. A imagem é somente `linux/arm64`, ajustada a Neoverse N1.

O preflight não instala SteamCMD nem executa o Valheim, e não substitui a aceitação de crossplay, BepInEx ou desempenho na Oracle. Consulte [ACCEPTANCE.md](ACCEPTANCE.md).

## Usar a imagem publicada na Oracle

O `compose.yaml` distribuído já baixa a imagem indicada por `VALHEIM_IMAGE`; não há bloco `build` na instalação. No `.env` da Oracle, configure a referência correspondente à publicação:

```dotenv
VALHEIM_IMAGE=seuusuario/valheim-arm:latest
```

Use o namespace real do Docker Hub, que pode ser diferente do usuário GitHub. A variável `DOCKERHUB_IMAGE` configura o destino da publicação no CI; `VALHEIM_IMAGE` configura o que a Oracle baixa e pode incluir uma tag ou digest específico.

Para imagens privadas, faça `sudo docker login` na Oracle com uma credencial de leitura. Pare o jogo de forma segura pelo painel antes de trocar sua imagem e execute:

```sh
sudo docker compose pull valheim
sudo docker compose up -d --no-build
```

A primeira instalação por imagem pronta ainda exige criar `data` com proprietário 10001, configurar `.env`, executar `set-password` e configurar o HTTPS conforme o [README](../README.md). Atualizar a imagem troca supervisor/FEX; as atualizações do jogo continuam sob controle do supervisor.
