# Contas individuais e memória privada no App4

## Como usar

1. O administrador original entra com `administrador` e a senha atual do aplicativo (`APP_PASSWORD`). O UUID configurado em `OWNER_USER_ID` precisa existir em `profiles`.
2. Em **Configurações → Segurança**, o administrador encontra **Cadastrar usuário**. Informe nome, e-mail e senha inicial de exatamente 8 caracteres (limite solicitado pelo operador; senhas maiores seriam mais seguras).
3. Cada pessoa entra com seu próprio e-mail e senha. Em **Minha conta**, pode alterar a senha e criar suas contas financeiras antes de importar arquivos.
4. Em dispositivos compartilhados, use **Sair / trocar usuário** antes de entregar o acesso a outra pessoa. O aplicativo não consegue identificar quem está usando uma sessão deixada aberta.

Após o administrador definir uma senha individual, a senha antiga do aplicativo deixa de autenticar esse perfil. Trocar a senha encerra a sessão atual e invalida as demais na próxima validação. Sessões expiram em 12 horas; após reinício pode ser necessário entrar novamente.

Se não houver senha inicial local, execute `python scripts/setup_app4_admin.py` em um terminal interativo. Confirme com `CONFIGURAR` e digite a senha duas vezes, sem exibição. O script grava somente o hash scrypt no perfil original e recusa substituir uma senha individual já definida. Não é um mecanismo público de recuperação. Senhas antigas maiores continuam válidas para login; novas senhas precisam ter 8 caracteres.

## O que é compartilhado e o que é privado

As páginas e informações públicas de mercado continuam comuns. O proprietário autenticado é usado nas consultas pessoais, caches de finanças e modelos de carteira. Novas contas começam sem os dados financeiros do administrador. Jobs executados fora do Streamlit continuam usando o proprietário configurado, por compatibilidade.

Os chats guardam contexto por usuário e por assunto/seção no PostgreSQL existente. Ao voltar, o histórico restaurado entra no contexto enviado à LLM, mas não é renderizado na tela. As novas mensagens da sessão aparecem normalmente. Limpar um chat remove a memória daquele usuário e assunto, não a de outras pessoas.

Não é memória ilimitada: até 40 mensagens, 12 mil caracteres por mensagem e 100 mil caracteres por conversa; até 30 conversas recentes por pessoa. Conversas sem atualização por 30 dias não são carregadas; a remoção física de conversas vencidas ocorre em gravações posteriores. Duas sessões simultâneas escrevendo no mesmo chat usam a última gravação, sem fusão automática.

O antigo histórico compartilhado em SQLite não é importado, pois não é possível identificar com segurança a pessoa que escreveu cada mensagem. Dados financeiros existentes permanecem ligados ao UUID original.

## Operação e segurança

- Reutiliza `profiles` e `user_settings.extra_settings`; não exige nova migração nem altera contas de produção durante a implementação.
- Senhas individuais usam scrypt com salt aleatório; cinco falhas bloqueiam o login da conta por 15 minutos.
- O acesso ao banco ocorre no servidor, com SQL parametrizado e filtro de proprietário. Não é uma implantação de Supabase Auth/JWT nem uma nova política RLS. Credenciais do banco nunca devem ser expostas ao navegador.
- O serviço precisa das permissões existentes de leitura/escrita nessas tabelas; não conceder permissões de schema para habilitar o cadastro.
- Use HTTPS na hospedagem e `MOCK_MODE=false` para dados reais. O modo de demonstração não representa as finanças de qualquer usuário.
- Os textos persistidos continuam disponíveis ao administrador do banco e os trechos de contexto são enviados ao provedor LLM configurado. Ocultar mensagens na interface não significa criptografia de ponta a ponta.
- Recuperação de senha por e-mail e administração visual de desativação de contas não fazem parte desta entrega.
- Para reversão, preserve perfis e preferências. Não volte à versão de senha compartilhada enquanto usuários individuais tiverem acesso: isso removeria esta separação de autenticação.

## Evidências de verificação — 17/09/2026

Complemento de senha: `python -m pytest tests/test_multiuser.py tests/test_chat_memory.py -q` passou com 18 testes em PostgreSQL descartável, incluindo configuração inicial e recusa de sobrescrita. Ruff dos quatro arquivos alterados passou; health check do Streamlit retornou `ok`. A configuração real depende da digitação e confirmação do operador no terminal, não realizada pelos testes.

Dados sintéticos em PostgreSQL descartável, publicado somente em loopback. Nenhuma conta real foi criada ou modificada.

- Suíte de regressão: 164 testes passaram antes da adição do último caso SQL de isolamento; nova execução focalizada de `tests/test_multiuser.py tests/test_portfolio_repository.py`: 21 passaram, incluindo esse caso.
- Cobertura: cadastro exclusivo do administrador, login, bloqueio, alteração de senha, revogação, caches A/B, contas pessoais, rejeição de lançamento em conta de outro usuário, snapshots privados, contexto restaurado oculto e limpeza de sessão no logout.
- `python scripts/run_quality_checks.py`: skills, fórmulas sintéticas, varredura de segredos e três testes de ambiente passaram.
- `python -m ruff check` nos arquivos desta adaptação: aprovado após organização dos imports e remoção de imports sem uso.
- Inicialização: `python -m streamlit run app.py --server.headless=true --server.address=127.0.0.1 --server.port=18547 --browser.gatherUsageStats=false`; health check respondeu `ok`; processo encerrado após o teste.
- Formulários e chat foram exercitados com Streamlit AppTest e LLM simulada. Validação visual em navegador real, responsividade e teclado permanecem pendentes: a skill oficial de browser requerida pelo projeto não estava disponível. AppTest não substitui essa validação.
- Não foi executada a suíte completa de todo o repositório nem um teste de LLM pago/real. A varredura automática de segredos detecta padrões conhecidos e não constitui auditoria completa.
- Alterações preexistentes de outras funcionalidades foram preservadas e excluídas do commit desta adaptação.

Verificação pré-publicação: cópia isolada do índice Git, sem arquivos de credenciais locais, passou nos 166 testes do comando abaixo (PostgreSQL descartável), checks de qualidade, varredura de segredos, lint focalizado e inicialização do Streamlit. A validação visual completa continua pendente conforme descrito acima.

Integração na main: merge sem conflitos sobre d32f1e6. A primeira execução encontrou dois testes antigos de FIIs incompatíveis com a sessão obrigatória; o cenário foi atualizado com identidade sintética e repositório de chat simulado. A asserção de troca de contexto agora exige histórico vazio, em vez da ausência da chave (o carregador inicializa a lista). Nenhum controle de autenticação foi relaxado. A execução final de `python -m pytest tests/test_multiuser.py tests/test_chat_memory.py tests/test_portfolio_repository.py tests/test_fii_rich_presentation.py tests/test_fiis_ui.py -q` passou com 72 testes em banco descartável. Checks de qualidade, lint, varredura de segredos e startup também passaram. Confirmação visual da hospedagem após deploy permanece pendente.

Comando de regressão executado (definir `APP4_USERS_TEST_DATABASE_URL` para um PostgreSQL local descartável):

```powershell
python -m pytest tests/test_multiuser.py tests/test_chat_memory.py tests/test_import_idempotency.py tests/test_investment_imports.py tests/test_xp_proventos_import.py tests/test_controle_transaction_classification.py tests/test_controle_fatura_cartao.py tests/test_investimentos_correlacao.py tests/test_configuracoes_modernization.py tests/test_portfolio_models.py tests/test_us_portfolio_model.py tests/test_proventos_periodos.py tests/test_portfolio_repository.py tests/test_portfolio_capture.py tests/test_backfill_portfolio_snapshots.py -q
```
