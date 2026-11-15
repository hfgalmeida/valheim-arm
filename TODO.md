## Criação de um servidor Valheim para uso em instância da Oracle ARM, com 2 OCPUS e 12GB

### Premissas
 - Ter a configuração o mais amigável possível
 - Documentação completa de como instalar o servidor
 - Otmização máxima para uso em ARM

### Requisitos
 - Instalação via docker compose
 - FEX Emu somente, na sua última versão, com recursos otimizados e alguns opcionais para configuração via docker compose
 - Mínimo de emulação possível
 - Permitir habilitar Crossplay
 - Backups automáticos
 - Permitir modificadores de mundo, configurados individualmente no docker compose
 - Atualizações automáticas com verificação de jogadores online
 - Reinício diário configurável também com verificação de jogadores online
 - MOD's Bepinex
 - Página web de gerenciamento, com visualização de gráficos de consumo de recursos em tempo real, permtir alterar as configurações do servidor, como nome do mundo, senha, modificadores, gerenciamento de usuários, backup dos arquivos de save localmente (admin configura senha diretamente no servidor)

### Acompanhamento da implementação

- [x] Estrutura Docker Compose ARM64, build FEX e configuração individual.
- [x] Supervisor nativo, painel, autenticação e métricas.
- [x] Cadastro assistido nas listas de administradores, banidos e permitidos, com seleção de IDs conhecidos, inclusão manual e remoção por botão.
- [x] Fila de manutenção, persistência de tarefas adiadas e backups com restauração.
- [x] Integração opcional BepInEx com pacote verificado e plugins locais.
- [x] Incluir BepInExPack_Valheim padrão na imagem Docker, com versão/hash fixos e ativação pelo painel sem URL ou SHA256.
- [x] Upload de DLLs pelo painel com jogo parado, confirmação de substituição e restauração da versão anterior.
- [x] Editor de configurações de plugins pelo painel, com gravação com jogo parado, detecção de conflitos e recuperação da versão anterior.
- [x] Documentação de instalação, configuração, API e recuperação.
- [x] Testes locais de configuração, jogadores, autenticação, backups e manutenção.
- [x] Prova funcional FEX/SteamCMD/Valheim na Oracle ARM, com inicialização e conexão confirmadas pelo administrador.
- [x] Conexão ao servidor com crossplay desativado (Steam).
- [x] Conexão ao servidor com crossplay ativado (PlayFab).
- [x] Validar BepInEx embutido e ServerInfo 2.2.7 com JsonDotNET 13.0.4 na Oracle: conexão e jogo normal confirmados pelo administrador com e sem crossplay. Essa validação não garante compatibilidade de outros plugins.
- [x] Conexão e jogo normal confirmados pelo administrador com BepInEx, ServerInfo e crossplay ativado (14/11/2026).
- [x] Conexão e jogo normal confirmados com os mesmos plugins sem crossplay; edição de configuração via backend do painel e recuperação da versão anterior verificadas.
- [x] Desativação do BepInEx confirmada com preservação de DLLs/configuração e retorno online ao estado original após reinício controlado; falha fatal da primeira partida registrada na seção de recuperação abaixo.
- [x] Carregamento real na Oracle do pacote embutido 5.4.2360 (engine 5.4.23.6), ServerInfo 2.2.7 e JsonDotNET 13.0.4: 2 plugins carregados, 0 falhas, API interna funcional e mundo online com crossplay. Conexão pelo cliente posteriormente confirmada nos dois modos.
- [ ] Completar a validação da contagem de jogadores e dos formatos reais dos logs na Oracle.
- [ ] Medições e ajustes de desempenho no Ampere A1.
- [ ] Aceitação completa conforme [validação na Oracle](docs/MAINTAINER.md#validacao).

### Instalação pública simplificada

- [x] Imagem pública padrão hfgalmeida/valheim-arm:latest.
- [x] Instalador interativo com Docker, configuração e inicialização automáticos.
- [x] README direto para jogadores, referência de configuração e guia separado para mantenedores.
- [x] Recuperação automática com intervalo de cinco minutos e respeito à parada manual.

### Melhoria futura: identificação de jogadores de outras plataformas

- [ ] Descobrir automaticamente IDs de permissão de outras plataformas no crossplay, usando mensagens explícitas do servidor ou um plugin BepInEx validado.
- [ ] Associar nome, plataforma e ID quando essa informação estiver disponível, sem deduzir permissões a partir de join codes, ZDOIDs ou identificadores temporários de conexão PlayFab.
- [ ] Validar com clientes das plataformas atendidas e manter o cadastro manual como alternativa; separar histórico de IDs de presença online.

### Melhoria futura: usuários e permissões do painel

- [ ] Adicionar contas individuais com login e senha e perfis de administrador e operador, separados das permissões dos usuários do jogo.
- [ ] Permitir ao administrador cadastrar e desativar usuários e redefinir suas senhas, preservando o acesso administrativo existente na migração.
- [ ] Exigir troca da senha temporária no primeiro acesso e após redefinição pelo administrador; bloquear as demais funcionalidades até a troca ser concluída.
- [ ] Permitir ao operador visualizar estado, métricas e join code e solicitar início, parada e reinício do servidor, mantendo as verificações de zero jogadores para paradas e reinícios.
- [ ] Restringir ao administrador alterações de mundo, modificadores, mods, nomes, senhas e demais configurações, além do gerenciamento de usuários, backups e envio para a nuvem.
- [ ] Restringir logs completos e download/restauração de backups ao administrador.
- [ ] Validar permissões na API e adaptar a interface de acordo com o perfil; ocultar botões não substitui a autorização no servidor.
- [ ] Invalidar sessões ao desativar uma conta ou redefinir sua senha.
- [ ] Manter logs persistentes de auditoria das ações dos usuários: início, parada, reinício, atualização, criação/restauração de backups, mudanças de configurações e gerenciamento de contas e permissões.
- [ ] Registrar em cada ação o usuário responsável, data/hora, operação, recurso afetado e resultado (solicitado, concluído, falha ou cancelado), correlacionando a solicitação ao resultado das tarefas em segundo plano e distinguindo ações automáticas das ações dos usuários.
- [ ] Registrar quais configurações mudaram e seus valores anteriores/novos, sem gravar senhas, tokens ou outras credenciais; para campos secretos, registrar apenas que foram alterados.
- [ ] Disponibilizar consulta dos logs de auditoria ao administrador no painel, com filtros por usuário, período e operação e retenção definida para limitar o uso de disco.
- [ ] Testar os perfis, o bloqueio de acesso direto à API, a troca obrigatória de senha e a migração do acesso administrativo atual.
- [ ] Testar a persistência e a atribuição dos logs de auditoria, o registro de falhas, as restrições de acesso ao histórico e a ausência de credenciais nos registros.

### Melhorias de operação e diagnóstico

- [x] Diagnóstico do painel: distinguir conexão com supervisor, jogo iniciando, pronto, parado, parando, em recuperação ou com falha; mostrar prazo da recuperação e última falha da sessão com horário.
- [x] Atualizador no host para imagem Docker (FEX e painel), com backup, parada limpa, verificação de inicialização e retorno à imagem anterior; versão instalada e atualização pública no painel.
- [x] Validar na Oracle restauração dos arquivos no formato atual: parada limpa, backup preservado fora da retenção, restauração de 9 arquivos de saves com hashes idênticos, remoção de arquivo adicional e retorno do mundo ao estado online (14/11/2026).
- [x] Confirmar pelo cliente do jogo a entrada e o estado visual do mundo após a restauração validada em 14/11/2026: administrador conectou e confirmou que o mundo está correto.
- [x] Alertas opcionais no Discord para recuperação suspensa, falha de backup/envio à nuvem e pouco espaço em disco, com teste no painel e aviso de normalização.
- [x] Persistir a intenção de Iniciar/Parar pelo painel, preservando-a ao reiniciar contêiner/instância; manter painel disponível com jogo parado e respeitar a preferência durante manutenção e atualização da imagem.
- [x] Diagnóstico BepInEx por sessão: versão, tentativas de carregamento dos plugins, conclusão do carregador, falhas/dependências e DLLs alteradas aguardando reinício; compatibilidade real continua pendente.
- [x] Histórico persistente de manutenção com início, término, duração em execução, origem, resultado, tentativas e backup associado; filtros por origem, resultado e período, retenção de 30 dias/200 operações concluídas e preservação das pendentes.
- [ ] Concluir validação de desempenho no Ampere A1, contagem de jogadores e compatibilidade de plugins BepInEx adicionais.

### Melhoria futura: versão mobile do painel

- [ ] Criar uma experiência mobile dedicada, com navegação, métricas, formulários e operações adaptados a telas pequenas; validar acessibilidade e uso por toque.

### Recuperação de falha fatal durante inicialização

- [x] Detectar sinal fatal mesmo quando o processo permanece ativo; mostrar a falha no painel em vez de manter iniciando. Não confundir o Loading: Done inicial do Unity com a conclusão do carregamento do mundo.
- [x] Recuperação serializada da falha fatal na primeira cena, com SIGINT, prazo de 30 segundos e SIGKILL apenas antes de qualquer indício de carregamento do mundo; manter saves, marcador de saída incompleta e proibição de backup nessa saída.
- [x] Compartilhar o limite de cinco falhas e intervalos de recuperação existentes, respeitar parada manual e suspender se a fase do mundo for desconhecida ou tiver avançado. Não persistir tarefas ligadas a um PID nem bloquear a limpeza por falha de persistência.
- [x] Validar no Linux da Oracle com processo sintético isolado que ignora SIGINT: encerrado com SIGKILL, uma falha contabilizada, saves de teste intactos e nenhum backup criado; servidor real preservado.
- [ ] Aplicar a nova imagem e validar recuperação de uma ocorrência real do problema no FEX/Valheim. A causa do SIGSEGV não foi identificada nem atribuída ao BepInEx.

### Atualização da imagem pelo painel

- [x] Botão com confirmação HTML, API autenticada/CSRF e acompanhamento persistente do progresso, inclusive após novo login.
- [x] Agente local com socket Unix, verificação de UID, montagem somente leitura e operação fixa de atualização; sem Docker socket nos contêineres, comandos livres ou novas portas de rede.
- [x] Reutilizar backup, verificação, preferência de execução e retorno à imagem anterior do atualizador; impedir operações simultâneas e pausar manutenção durante atualização.
- [x] Testar navegador, API e comunicação Unix em contêiner descartável no Linux, sem instalar serviço permanente nem executar atualização real.
- [x] Integrar criação/habilitação automática do serviço privilegiado ao instalador; preservar configurações e não reiniciar o mundo ao reexecutar o script.
- [x] Instalar e habilitar o agente no host de testes, recriar o contêiner com socket somente leitura e confirmar resposta disponível pelo cliente do painel (15/11/2026).
- [ ] Validar uma atualização real pelo painel após instalar o agente e publicar a nova imagem.
