# Aceitação na Oracle ARM

**Pendente:** esta estação é Windows x86_64 e não dispõe de Docker ou acesso OCI. Testes locais não comprovam compatibilidade com FEX, crossplay ou mods.

Execute em Ubuntu 24.04 ARM64, 2 OCPUs/12 GB, começando em um mundo descartável. Registre tag/commit do projeto, `uname -a`, `getconf PAGESIZE`, versão Docker/Compose e conteúdo de `/opt/build-info.json`. Guarde somente dados sem senhas.

## Etapa 1 — prova funcional obrigatória

- [ ] Construir a imagem nativamente e executar `preflight`, sem binfmt, privilégios adicionais, FUSE ou outro emulador.
- [ ] Configurar senha administrativa, iniciar vanilla, observar conexão e entrar com cliente Steam.
- [ ] Identificar `Connections N ZDOS:` nos logs; confirmar estado desconhecido antes do primeiro snapshot e durante conexões/desconexões.
- [ ] Habilitar crossplay, reiniciar e entrar por join code com cliente compatível de outra plataforma. Confirmar que eventos PlayFab invalidam a contagem até o próximo snapshot. Guardar amostras anonimizadas para atualizar os testes.
- [ ] Instalar um BepInExPack Linux com SHA256 conhecido e plugin de teste compatível; confirmar carregamento no log e conexão. Repetir a verificação de contagem com BepInEx ativo.
- [ ] Validar que o SIGINT entregue através do FEX salva o mundo e encerra o processo. Se o texto de save/conexões diferir, adaptar o parser e os testes antes de continuar.

Interrompa a aceitação se qualquer combinação exigida não funcionar. Não substitua FEX por outro emulador para contornar a incompatibilidade.

## Etapa 2 — painel e manutenção

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

## Etapa 3 — dados e falhas

- [ ] Criar backup com o jogo parado, baixar no navegador e validar manifesto/checksum.
- [ ] Alterar algo no mundo, parar, restaurar e confirmar o estado anterior no jogo. Verificar criação do backup pré-restauração.
- [ ] Em volume descartável, simular disco sem espaço e download Steam interrompido; confirmar preservação do backup/instalação anteriores e erro visível.
- [ ] Confirmar rejeição de arquivo adulterado e de entradas tar com caminhos externos ou links.
- [ ] Simular encerramento anormal em mundo descartável; confirmar marcador e bloqueio da manutenção até recuperação/parada limpa.
- [ ] Desligar e ligar a instância; verificar volumes, configurações, senha administrativa e certificado persistentes.

## Medição de desempenho

Use o mesmo mundo, mesmas áreas e jogadores, e registre três execuções por configuração: vanilla sem cache, vanilla com cache quente e BepInEx com a lista exata de plugins. Anote tempo até ficar online, consumo ocioso, pico de memória, CPU e atrasos de save durante pelo menos 30 minutos de jogo. Altere uma opção FEX de cada vez.

| Cenário | FEX / build jogo | Jogadores / mods | Inicialização | CPU média/pico | RAM pico | Save / observações |
|---|---|---|---|---|---|---|
| Vanilla sem cache | pendente | pendente | pendente | pendente | pendente | pendente |
| Vanilla cache quente | pendente | pendente | pendente | pendente | pendente | pendente |
| BepInEx cache quente | pendente | pendente | pendente | pendente | pendente | pendente |

A aceitação termina quando os cenários exigidos funcionarem e as evidências forem registradas. Não há promessa prévia de número de jogadores ou melhoria percentual.
