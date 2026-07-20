# AGENTS.md

## Objetivo do projeto

Aplicação Streamlit unificada para controle financeiro, investimentos, carteira, proventos, análise de empresas, indicadores econômicos e inteligência artificial aplicada. Repositórios de origem: `Tiago84Barros/Dashboard`, `Tiago84Barros/Controle_Financeiro` e `Tiago84Barros/Dashboard-Investimentos`.

## Regras permanentes

- Ler as Skills relevantes em `.agents/skills/` antes de modificar módulos financeiros.
- Preservar a navegação, autenticação, tema e arquitetura existentes; preferir alterações localizadas.
- Não apagar funcionalidades nem substituir arquivos completos sem necessidade e validação.
- Separar interface, serviços/regras, repositórios, cálculos e ETL; não duplicar conexão ou consulta.
- Implementar cálculos financeiros deterministicamente em Python, documentar fórmulas e testar regressões.
- Nunca inventar números, preços, saldos, classificações ou fontes. Dados ausentes permanecem ausentes.
- Informar origem, período, unidade, atualização, premissas e limitações dos dados.
- Separar fatos, inferências, simulações e recomendações. Não apresentar retorno futuro como garantia.
- Não executar movimentações, ordens, transferências ou alterações financeiras automáticas.
- Exigir confirmação humana para ações irreversíveis, migrações, compras, vendas e rebalanceamentos.
- Não colocar credenciais no código, documentação, logs ou capturas.
- Minimizar dados enviados a serviços externos e nunca enviar o conjunto financeiro completo sem necessidade.
- Trabalhar inicialmente com integrações e bancos remotos em modo somente leitura e menor privilégio.
- Usar consultas parametrizadas e filtrar dados pelo proprietário autorizado.
- Não executar migração destrutiva; criar backup verificável, plano de reversão e teste em dados descartáveis.
- Usar dados sintéticos em testes, exemplos, prompts, logs e validação visual.
- Executar testes, lint/checks configurados, varredura de segredos e inicialização do Streamlit antes de concluir.
- Registrar comandos executados, resultados, falhas, verificações não realizadas e riscos pendentes.

## Roteamento de Skills

| Tipo de tarefa | Skill obrigatória |
|---|---|
| Diagnóstico pessoal, metas e recomendações | `personal-financial-analyst` |
| Fórmulas, indicadores, projeções e retornos | `financial-calculations` |
| Gastos, recorrências, duplicidades e anomalias | `expense-intelligence` |
| Carteira, benchmarks, risco e rebalanceamento | `investment-portfolio-analysis` |
| Banco, credenciais, migração, logs, LLM e privacidade | `financial-data-security` |
| Páginas, componentes, cache e estado Streamlit | `streamlit-financial-app` |
| Testes, regressão, lint e critérios de aceite | `financial-app-quality` |
| Validação funcional e visual no navegador | `streamlit-browser-validation` e a Skill oficial `browser:control-in-app-browser` |

Leia todas as Skills envolvidas quando uma mudança cruza domínios.

## Estrutura e execução

- Entrada: `app.py`
- Interface: `views/` e `design/`
- Domínio e infraestrutura: `core/`
- Importação: `etl/` e `data_pipeline/`
- Banco e migrações: `supabase_unificado/`, sempre com backup e reversão
- Testes: `tests/`

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
python -m pytest
python scripts/run_quality_checks.py
```

## Definição de concluído

O aplicativo inicia, testes relevantes passam, a interface alterada é validada com dados sintéticos, nenhuma credencial é detectada, cálculos mantêm evidência e unidades, integrações continuam no menor privilégio e toda pendência é documentada.
