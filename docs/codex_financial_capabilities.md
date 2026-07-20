# Capacidades Codex para o App4 financeiro

## Skills criadas

| Skill | Caminho | Finalidade | Recursos |
|---|---|---|---|
| `personal-financial-analyst` | `.agents/skills/personal-financial-analyst/` | Diagnósticos, metas, cenários e recomendações com evidência | `references/metrics-and-benchmarks.md` |
| `financial-calculations` | `.agents/skills/financial-calculations/` | Fórmulas Python, unidades, períodos e regressão | `references/formulas.md` |
| `expense-intelligence` | `.agents/skills/expense-intelligence/` | Recorrências, duplicidades, parcelas e anomalias | `references/detection-criteria.md` |
| `investment-portfolio-analysis` | `.agents/skills/investment-portfolio-analysis/` | Alocação, risco, liquidez, benchmarks e rebalanceamento humano | `references/portfolio-criteria.md` |
| `financial-data-security` | `.agents/skills/financial-data-security/` | Menor privilégio, segredos, SQL, migração e LLM | `references/security-checklist.md` |
| `streamlit-financial-app` | `.agents/skills/streamlit-financial-app/` | Arquitetura, cache, estado e UI Streamlit | `references/streamlit-checklist.md` |
| `financial-app-quality` | `.agents/skills/financial-app-quality/` | Testes, regressão e critérios de aceite | `references/test-matrix.md` |
| `streamlit-browser-validation` | `.agents/skills/streamlit-browser-validation/` | Teste local seguro no navegador oficial | `references/browser-test-checklist.md` |

Todas possuem `agents/openai.yaml`, passaram no `quick_validate.py` oficial e no `scripts/validate_skills.py` local.

## Plugins e apps avaliados

| Item / fornecedor | Conteúdo e finalidade | Permissões/dados | Necessidade e risco | Decisão/status |
|---|---|---|---|---|
| Browser 26.715.31925 / OpenAI | Skill `control-in-app-browser`; navegador local e Playwright interno | Ler/interagir com páginas e capturar tela | Necessário para localhost; risco de capturar dados | Já habilitado; usado apenas com mock, sem screenshot |
| GitHub 0.1.8 / OpenAI | Skills `github`, `gh-address-comments`, `gh-fix-ci`, `yeet`; app GitHub | Repositórios, PRs, issues e Actions; escrita possível com aprovação | Útil no fluxo Git; risco de publicação/alteração | Já habilitado; nenhuma ação remota executada |
| Supabase 1.0.0 / Supabase | Skills Supabase/Postgres e app de gestão | Pode ler/escrever SQL, schema, Auth, funções e branches | Poder excessivo para esta preparação | Disponível, mas app/MCP não usado nem configurado |
| Data Analytics 0.2.8 / OpenAI | Skills de qualidade, relatórios, KPIs, visualização e notebooks; app/MCP | Pode acessar e produzir dados/artefatos conectados | Útil futuramente, não necessário agora | Disponível; não usado nem configurado |
| Codex Security / OpenAI | Revisão de segurança documentada no manual oficial | Lê código; alguns fluxos exportam achados | Útil em revisão futura, instalação não justificada agora | Não instalado; aguardar necessidade/autorização |
| CircleCI / terceiro curado | CI/CD | Pipelines e projetos conectados | Não é o CI identificado como necessário | Preexistente global; não usado |
| CodeRabbit / terceiro curado | Revisão de código | Código e PRs | Duplicaria revisão atual | Preexistente global; não usado |
| Render / terceiro curado | Deploy e monitoramento | Serviços Render | App não foi preparado para deploy nesta etapa | Preexistente global; não usado |
| Figma / terceiro curado | Design e Code Connect | Arquivos Figma | Fora do escopo | Preexistente global; não usado |
| Documents, PDF, Presentations, Spreadsheets / OpenAI runtime | Artefatos de escritório | Arquivos fornecidos ao fluxo | Fora do escopo desta preparação | Preexistentes; não usados |
| Sites / OpenAI | Construção e hospedagem de sites | Arquivos e publicação de site | Streamlit existente não usa Sites | Preexistente; não usado |

O conector GitHub possui aprovações configuradas para criação de PR e marcação como pronto; nenhuma dessas permissões foi exercida. Não foram concedidas novas permissões nesta etapa.

## MCP servers

| Servidor | Escopo e ferramentas | Permissões | Resultado |
|---|---|---|---|
| `node_repl` global do Codex | Runtime do Browser/Chrome e Playwright interno | Controle de navegador conforme plugin | Funcionou no localhost; não é financeiro |
| MCP do projeto | Nenhum | Nenhuma nova | `.codex/config.toml` não foi criado porque não há lacuna funcional |

Não foi configurado MCP para banco, corretora, banco pessoal ou movimentação financeira.

## Dependências

Nenhum pacote foi instalado e nenhum arquivo de dependências foi alterado. O ambiente usado contém Python 3.12.10, Streamlit 1.57.0, pandas 2.3.3, Plotly 5.24.1, SQLAlchemy 2.0.49, OpenAI SDK 2.37.0 e pytest 9.0.3. `ruff` está disponível; `mypy` e `pip-audit` não estão. `pip check` não encontrou dependências quebradas.

Risco: o ambiente instalado usa OpenAI SDK 2.37.0, enquanto `requirements.txt` declara `openai>=1.63.0,<2.0.0`. A implementação do novo módulo deve escolher e testar uma faixa única antes de alterar o lock/requirements.

## Scripts e dados sintéticos

- `scripts/validate_skills.py`: valida descoberta, frontmatter, UI metadata e referências.
- `scripts/check_financial_formulas.py`: confere exemplos determinísticos.
- `scripts/check_secrets.py`: procura segredos de alta confiança sem revelar valores.
- `scripts/test_readonly_database.py`: executa `SELECT 1` somente leitura com rollback.
- `scripts/run_quality_checks.py`: agrega checks seguros; `--full` inclui a regressão.
- `tests/fixtures/synthetic_financial_data.json`: receitas, despesas, assinatura, parcela, duplicidade candidata, anomalia candidata, reserva, dívida, metas, carteira e dados ausentes fictícios.
- `tests/test_codex_environment.py`: valida Skills, fixture e fórmulas exemplo.

## Segurança e permissões

- Segredos permanecem em `.env`/secrets; somente nomes e presença foram auditados.
- Nenhum segredo foi incluído nos arquivos criados; o scanner passou.
- Banco remoto foi acessado apenas por `SELECT 1` em transação read-only revertida.
- Nenhuma escrita externa, deploy, push, PR, migração ou operação financeira ocorreu.
- O teste visual usou `MOCK_MODE`, UUID sintético e nenhuma captura de tela.
- Risco pendente: algumas views ainda consultam o banco durante `MOCK_MODE`; corrigir o isolamento antes de uma suíte visual que exija zero acesso remoto.

## Validação executada

| Verificação | Resultado |
|---|---|
| Validador oficial das oito Skills | 8/8 válidas |
| `python scripts/run_quality_checks.py` | Passou |
| Novos testes unitários | 3/3 passaram |
| Fórmulas sintéticas | Poupança 40%, patrimônio líquido R$ 45.000 e reserva 3 meses: passaram |
| Scanner de segredos | Passou; valores nunca exibidos |
| `ruff` nos arquivos novos | Passou |
| `pip check` | Passou |
| Banco somente leitura | `SELECT 1` passou e houve rollback |
| Regressão completa | 745 passaram, 3 falharam |
| Streamlit e navegador | Iniciou; Dashboard, Controle Financeiro e aba Análises carregaram |
| Layout 390×844 | Sem overflow horizontal; título presente |
| Console | Erros somente durante reinício controlado; nenhum novo após servidor estável |

Falhas preexistentes da regressão:

1. `tests/test_market_companies.py::test_view_americana_card_analisar_seleciona_ticker_e_muda_aba` — timeout após clique.
2. `tests/test_market_read.py::test_financeiro_sempre_market_para_qualquer_flag` — DataFrame usado em comparação booleana.
3. `tests/test_market_read.py::test_financeiro_market_erro_retorna_vazio_nao_legado` — fallback retornou dados em vez de vazio.

Também foram observados avisos do Streamlit para substituir `use_container_width` por `width`.

## Teste controlado do analista

Solicitação: “Analise os dados financeiros sintéticos e identifique três oportunidades de melhoria, separando fatos, inferências e recomendações.”

### Fatos

1. A fixture contém uma cobrança sintética recorrente de R$ 49,90 em três meses: R$ 149,70 observados.
2. Há duas despesas sintéticas de café, mesma data, descrição e valor de R$ 18,00; estão marcadas apenas como candidata a duplicidade.
3. A carteira sintética totaliza R$ 30.000; o maior ativo vale R$ 12.000 (40%) e a dívida sintética tem saldo de R$ 4.000 a 1,8% ao mês.

### Inferências

1. A recorrência pode ser assinatura, mas os dados não provam uso baixo nem desperdício.
2. Uma das compras de café pode ser duplicada; também pode representar duas compras legítimas.
3. A dívida pode ter custo relevante e a posição de 40% pode representar concentração; faltam CET, impostos, política-alvo e tolerância a risco para decidir.

### Simulações

1. Eliminar integralmente a cobrança recorrente equivaleria a R$ 598,80 por 12 meses, sem garantia de que a eliminação seja apropriada.
2. Confirmar uma duplicidade recuperaria R$ 18,00 uma única vez.
3. Não foi simulada quitação ou venda porque faltam CET, cronograma, liquidez, custos, impostos e política de investimentos.

### Recomendações

1. Revisar a assinatura e confirmar utilidade antes de cancelar; prioridade média, confiança média.
2. Conferir o comprovante das duas compras de café; prioridade baixa, confiança média.
3. Levantar CET da dívida e definir bandas de alocação antes de comparar amortização e rebalanceamento; prioridade alta, confiança média, sempre com decisão humana.

Limitações: fixture curta, sem histórico de uso, CET, retornos históricos, impostos, custos ou política pessoal. Nenhum retorno foi prometido e nenhuma ação foi executada.

## Ações manuais pendentes

- Corrigir ou aceitar formalmente as três falhas existentes da regressão.
- Escolher uma faixa compatível do OpenAI SDK e recriar ambiente isolado.
- Tornar `MOCK_MODE` totalmente independente de banco remoto.
- Revisar a credencial do App4 para confirmar role somente leitura onde aplicável.
- Decidir se plugins globais não usados devem ser desabilitados; esta etapa não alterou preferências globais.
- Autorizar separadamente qualquer push, PR, instalação, migração ou acesso adicional.

## Prontidão

O ambiente está preparado para receber o prompt de implementação: regras, Skills, scripts, fixture e validação básica existem. A implementação deve começar pela auditoria funcional do módulo e tratar como gates os três testes falhos, o isolamento incompleto de `MOCK_MODE` e a incompatibilidade de versão do SDK OpenAI.
