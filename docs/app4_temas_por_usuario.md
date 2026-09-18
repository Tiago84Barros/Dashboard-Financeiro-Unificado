# Aparência por usuário

Na barra lateral, **Tema da minha conta** oferece **Dark (escuro)** e **Light (claro)**. A alteração é salva imediatamente para a pessoa autenticada em `user_settings.extra_settings.app4_theme`; reaparece no próximo login. Contas sem preferência usam dark. A preferência em outras sessões já abertas é atualizada no próximo login, não por sincronização em tempo real.

A leitura e a gravação exigem autenticação. A gravação usa o lock de preferências existente, preservando mensagens e demais chaves. Não há migração, alteração financeira ou configuração global compartilhada entre usuários. Falhas de leitura bloqueiam a gravação até uma leitura válida; falhas de gravação mantêm o tema anterior e mostram mensagem genérica.

O seletor personaliza a camada visual do App4, não o menu nativo de aparência do Streamlit. A API pública `st.set_option` não permite alterar configurações de tema por sessão (https://docs.streamlit.io/develop/api-reference/configuration/st.set_option). Não usamos configurações globais mutáveis nem inversão de cores.

## Limites visuais

Gráficos, tabelas em canvas e blocos de código mantêm painéis escuros de contraste no modo claro. Isso evita recolorir dados financeiros e logotipos. Componentes HTML específicos de páginas ainda podem ter cores fixas; a validação realizada cobre a estrutura compartilhada, não cada cartão de cada rota. A versão local usada na inspeção é Streamlit 1.63.0; o requirements fixa 1.57.0. Os seletores incluem os componentes antigos e novos, mas a inspeção visual na versão hospedada permanece pendente.

## Verificação

- PostgreSQL descartável, somente loopback; nenhuma preferência de produção modificada nos testes.
- `python -m pytest tests/test_user_theme.py tests/test_multiuser.py tests/test_chat_memory.py -q`: 23 passaram, incluindo persistência, isolamento A/B, rejeição de entradas, erros de banco e restauração no AppTest.
- `python scripts/run_quality_checks.py`: skills, fórmulas, segredos e testes de ambiente aprovados.
- Ruff nos arquivos novos/alterados aprovado.
- Regressão final com `tests/test_user_theme.py tests/test_multiuser.py tests/test_chat_memory.py tests/test_controle_tabs_theme.py tests/test_configuracoes_modernization.py`: 32 testes passaram; Ruff novamente aprovado.
- Prévia sintética: `python -m streamlit run tests/theme_preview.py --server.headless=true --server.address=127.0.0.1 --server.port=18548 --browser.gatherUsageStats=false`.
- Inspeção via navegador disponível (fallback: skill oficial solicitada pelo projeto indisponível). Alternância dark/light verificada; corrigido contraste de campos e expansores após inspeção. Sem erros de console na prévia; viewport de 390 px não apresentou overflow horizontal nem exceções. Não foi realizada auditoria completa de acessibilidade ou de todas as rotas.
- Servidor de prévia e banco descartável encerrados após verificação. Alterações não publicadas nesta etapa.
