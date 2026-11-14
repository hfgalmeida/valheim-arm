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
- [x] Upload de DLLs pelo painel com jogo parado, confirmação de substituição e restauração da versão anterior.
- [x] Editor de configurações de plugins pelo painel, com gravação com jogo parado, detecção de conflitos e recuperação da versão anterior.
- [x] Documentação de instalação, configuração, API e recuperação.
- [x] Testes locais de configuração, jogadores, autenticação, backups e manutenção.
- [x] Prova funcional FEX/SteamCMD/Valheim na Oracle ARM, com inicialização e conexão confirmadas pelo administrador.
- [x] Conexão ao servidor com crossplay desativado (Steam).
- [x] Conexão ao servidor com crossplay ativado (PlayFab).
- [ ] Validar carregamento do BepInEx e compatibilidade dos plugins escolhidos, incluindo entrada com e sem crossplay.
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
- [ ] Facilitar atualização da imagem Docker (FEX e painel), preservando dados e permitindo retorno à imagem anterior.
- [ ] Validar na Oracle restauração completa de backups no formato atual de saves.
- [x] Alertas opcionais no Discord para recuperação suspensa, falha de backup/envio à nuvem e pouco espaço em disco, com teste no painel e aviso de normalização.
- [ ] Persistir a intenção de parada pelo painel e definir seu comportamento após reiniciar a instância.
- [ ] Diagnóstico BepInEx: versão, plugins detectados e erros de carregamento.
- [ ] Histórico de manutenção com início, término, duração e origem (administrador, agendamento ou recuperação).
- [ ] Concluir validação de desempenho no Ampere A1, plugins BepInEx e contagem de jogadores.

### Melhoria futura: versão mobile do painel

- [ ] Criar uma experiência mobile dedicada, com navegação, métricas, formulários e operações adaptados a telas pequenas; validar acessibilidade e uso por toque.
