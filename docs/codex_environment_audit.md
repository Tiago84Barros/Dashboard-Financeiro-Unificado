# Auditoria do ambiente Codex — Analista Financeiro Pessoal IA

Data: 20/07/2026. Valores de segredos não foram lidos nem registrados.

| Item analisado | Situação encontrada | Necessidade | Risco | Ação proposta | Status |
|---|---|---|---|---|---|
| Sistema | Windows 11 Home Single Language 64-bit, build 26200; PowerShell; Codex Desktop 26.715.4045.0 | Compatível com o projeto | Baixo | Manter comandos PowerShell e caminhos Windows | Concluído |
| Superfície Codex | Aplicativo desktop; CLI localizado, mas `codex --version` retornou acesso negado nesta sessão | Desktop cobre a etapa | Baixo | Não depender da CLI | Concluído |
| Python | Sistema 3.12.10, pip 26.1.1; runtime empacotado 3.12.13 | Projeto e testes funcionam no Python do sistema | Médio | Padronizar ambiente virtual em etapa futura | Pendente |
| Git | Repositório GitHub em `main`, remoto `Tiago84Barros/Dashboard-Financeiro-Unificado`; checkpoint inicial `e3aab72` | Versionamento obrigatório | Baixo | Preservar alterações do usuário | Concluído |
| Estado inicial | `.gitignore` modificado e `data/` não rastreado antes deste trabalho | Não misturar mudanças | Alto se incluído por engano | Não adicionar aos commits desta etapa | Concluído |
| Entrada Streamlit | `app.py`; roteamento manual para `views/`; autenticação em `core/auth.py`; tema em `design/` | Base a preservar | Baixo | Não refatorar nesta etapa | Concluído |
| Banco | PostgreSQL/Supabase por SQLAlchemy; SQLite aceito em importações; schema e migrações em `supabase_unificado/` | Usar infraestrutura existente | Alto | Sem novo conector; leitura mínima e migrações reversíveis | Concluído |
| Conexão segura | `SELECT 1` executado com `SET TRANSACTION READ ONLY` e rollback; URL omitida | Confirmar acesso sem mutação | Baixo | Manter credencial de desenvolvimento com menor privilégio | Concluído |
| APIs externas | OpenAI, Gemini, BRAPI, SEC EDGAR, yfinance e FMP opcional aparecem na configuração/código | Reusar camadas existentes | Médio | Não adicionar integração nesta etapa | Concluído |
| Variáveis configuradas | `ACCOUNT_ID_C6`, `ACCOUNT_ID_CC`, `BRAPI_TOKEN`, `GEMINI_API_KEY`, `MARKET_READ_SOURCE`, `MOCK_MODE`, `OPENAI_API_KEY`, `OWNER_USER_ID`, `SEC_USER_AGENT`, `SOURCE_DB_APP2`, `SUPABASE_DB_URL`, `SUPABASE_ORIGEM_CONTROLE_URL` | Apenas nomes e presença foram verificados | Alto | Manter `.env` fora do Git e minimizar uso | Concluído |
| Dependências | `requirements.txt` já cobre Streamlit, pandas, Plotly, SQLAlchemy, PostgreSQL, OpenAI e análise; nenhuma dependência adicionada | Evitar duplicação | Médio | Revisar incompatibilidade do runtime `openai 2.37.0` com requisito `<2.0.0` | Pendente |
| Testes | pytest 9.0.3; 748 testes coletados | Regressão disponível | Médio | Corrigir 3 falhas pré-existentes separadamente | Pendente |
| Qualidade | `ruff` disponível; `mypy` e `pip-audit` ausentes; `pip check` sem conflitos instalados | Lint básico suficiente para esta etapa | Médio | Avaliar mypy/auditoria somente com escopo e dependências aprovados | Pendente |
| AGENTS.md | Existia com regras gerais | Regras financeiras permanentes necessárias | Baixo | Preservar conteúdo válido e ampliar | Concluído |
| Skills locais | `.agents/` existia sem Skills detectáveis | Oito Skills solicitadas | Baixo | Criar, registrar UI e validar | Concluído |
| Configuração Codex do projeto | `.codex/config.toml` ausente | Nenhum MCP adicional necessário | Baixo | Não criar configuração vazia ou privilegiada | Concluído |
| MCP global | `node_repl` já configurado pelo Codex para Browser/Chrome; nenhum MCP financeiro do projeto | Necessário apenas para navegador oficial | Médio | Reutilizar somente no teste local | Concluído |
| Navegador | Plugin oficial Browser 26.715.31925 habilitado, com API Playwright interna | Validação visual/funcional | Baixo em localhost | Não instalar Playwright separado | Concluído |
| Mock mode | Interface exibiu “Modo mock”, porém ainda fez uma consulta de modelo de portfólio no banco | Isolamento de testes | Alto | Tornar `MOCK_MODE` independente de banco antes de testes sensíveis | Pendente |
| Streamlit | App inicia e navega; emite aviso de remoção de `use_container_width` após 31/12/2025 | Compatibilidade futura | Médio | Migrar gradualmente para `width=` em trabalho separado | Pendente |

## Arquitetura observada

- Interface: `app.py`, `views/`, `design/`.
- Domínio e infraestrutura: `core/`.
- ETL e ingestão: `etl/`, `data_pipeline/`.
- Banco e schema: PostgreSQL/Supabase via SQLAlchemy, SQL em `supabase_unificado/`; SQLite apenas em fluxos compatíveis/importação.
- Testes: `tests/`, com cobertura ampla de mercado, carteira, qualidade, importação, LLM e Streamlit.
- Configuração: `.env`, `.env.example`, `.streamlit/config.toml`, `requirements*.txt`.

## Checkpoints

- Antes da preparação no App4: `e3aab72`.
- O checkpoint final é registrado após todas as validações e documentação.
