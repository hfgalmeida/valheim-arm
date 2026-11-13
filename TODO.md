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
- [ ] Aceitação completa conforme docs/ACCEPTANCE.md.
