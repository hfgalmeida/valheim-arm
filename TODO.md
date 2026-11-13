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
- [x] Fila de manutenção, persistência de tarefas adiadas e backups com restauração.
- [x] Integração opcional BepInEx com pacote verificado e plugins locais.
- [x] Documentação de instalação, configuração, API e recuperação.
- [x] Testes locais de configuração, jogadores, autenticação, backups e manutenção.
- [ ] Construção e prova funcional FEX/SteamCMD/Valheim na Oracle ARM.
- [ ] Validação de crossplay, BepInEx e formatos reais dos logs na Oracle.
- [ ] Medições e ajustes de desempenho no Ampere A1.
- [ ] Aceitação completa conforme docs/REFERENCE.md#validacao.

### Instalação pública simplificada

- [x] Imagem pública padrão hfgalmeida/valheim-arm:latest.
- [x] Instalador interativo com Docker, configuração e inicialização automáticos.
- [x] README direto para jogadores e referência avançada única.
- [x] Recuperação automática com intervalo de cinco minutos e respeito à parada manual.

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
