# Revisão da integração macro — 7 de setembro de 2026

## Entrega e limites

As correções abrangem criação B3, criação US, carteira modelo FII e contexto de
rebalanceamento Global. Não executam ordens nem substituem revisão humana.
O histórico exibido é sensibilidade contrafactual da composição atual, não um
backtest de retorno ou uma carteira com constituintes históricos.

| Lacuna | Correção | Evidência |
|---|---|---|
| Aplicação repetida do ajuste no histórico | Restaura peso fundamental; callbacks reutilizam os motores B3, US e FII | `test_macro_end_to_end.py` |
| Séries vencidas e revisões contadas como períodos | Deduplicação por período após filtro temporal; validade por frequência | `test_macro_postgres_temporal.py`, `test_macro_portfolio_context.py` |
| Sensibilidades sem calibração empírica | Avaliador móvel com dados conhecidos no corte, validação fora da amostra e aprovação humana | `test_macro_end_to_end.py`, `evaluate_macro_calibration.py` |
| Limites finais inconsistentes | Limita desvio relativo e turnover após projeção; FII conserva baseline fundamental independente | `test_macro_integration_revision.py`, `test_macro_end_to_end.py` |
| Contexto Global e LLM incompleto | Snapshot serializável e identificado; fontes, datas e limitações no prompt; Global compara impactos atuais com salvos | `test_macro_end_to_end.py` |
| Histórico obsoleto na interface | Assinatura de composição, modo, parâmetros, snapshot e data; bloqueio US quando configuração muda | `test_macro_history_ui.py` |

## Convenções

- Pesos são frações; impactos macro são pontos de -100 a 100, não retornos.
- Turnover de pesos é `0.5 * soma(abs(peso_final - peso_base))`.
- O limitador interpola entre base e proposta, respeitando teto de desvio relativo
  de 15% por ativo e turnover de 10% na configuração padrão. Restrições lineares
  convexas são preservadas quando ambos os extremos são viáveis.
- No Global, delta é impacto atual menos impacto salvo, limitado a [-100, 100].
  É uma heurística incremental limitada, não inversão exata do otimizador original.
  Sem comparação rastreável ou com números inválidos, o ativo fica somente contextual.
- A taxonomia setorial salva é preservada no Global, inclusive tipos de FII.
- A LLM recebe evidência do snapshot salvo, sem substituir silenciosamente a data
  de criação por dados atuais. Os cálculos continuam determinísticos e locais.

## Calibração: não ativada

A avaliação local desde 2010 encontrou zero cortes mensais utilizáveis em modo
estrito nas três classes. Séries reconstruídas disponíveis não demonstram quais
valores eram conhecidos em cada data histórica. Portanto os coeficientes seguem
como padrões setoriais iniciais, explicitamente identificados, sem alegação de
calibração empírica ou ganho de rentabilidade.

O avaliador exige treino móvel, amostra fora do treino, R² fora da amostra positivo
e estabilidade de sinal; um resultado aprovado é apenas candidato para revisão.
Ainda é necessário acervo de vintages com disponibilidade histórica verificável,
constituintes históricos, retornos totais e custos para certificar desempenho.

## Verificações realizadas

- `python -m pytest -q --tb=short`: 4.432 passaram, 3 ignorados, 20 avisos,
  537,25 segundos. Rodada anterior às duas últimas correções defensivas.
- `python -m pytest -q --tb=short tests/test_macro_end_to_end.py tests/test_macro_history_ui.py tests/test_macro_integration_revision.py tests/test_global_advisor.py tests/test_fii_portfolio_v4.py`:
  57 passaram após as correções defensivas.
- Teste novo de impacto inválido: primeiro falhou com `ValueError`; passou após
  correção. Valores textuais inválidos, NaN, infinito e objeto cobertos.
- Teste PostgreSQL opt-in com `APP4_TEST_MACRO_TEMP_TABLES=1`: 1 passou;
  tabelas temporárias sintéticas, conexão local verificada e rollback.
- AppTest sintético validou cálculo e invalidação por peso, modo e versão.
- Streamlit iniciou em loopback para `app.py` e harness sintético; processos
  temporários encerrados. Isso não equivale a validar toda a navegação.
- `python -m pytest -q --tb=short tests/test_macro_portfolio_context.py tests/test_macro_portfolio_tilt.py tests/test_us_portfolio_creation.py tests/test_llm_fii.py tests/test_macro_view.py tests/test_macro_runtime.py`:
  mais 38 passaram após as correções defensivas.
- Ruff passou nos arquivos Python alterados; nova execução nos três arquivos
  desta rodada também passou.
- `python scripts/run_quality_checks.py`: passou novamente após as correções;
  skills, fórmulas, varredura de segredos e 3 testes de ambiente aprovados.

## Pendências e segurança

Validação visual real, teclado e viewport estreito continuam pendentes: o controle
do navegador interrompeu a operação porque não conseguiu verificar a URL com
segurança. Não houve tentativa de contornar esse bloqueio.

Não houve migração, gravação financeira permanente, chamada paga a LLM, ordem,
commit ou push nesta revisão. Consultas reais usaram o Docker local; testes usaram
dados sintéticos. Alterações preexistentes de RAG e arquivos de staging foram
preservadas. A suíte completa não foi repetida após as últimas correções localizadas;
a regressão direcionada correspondente foi executada novamente.
