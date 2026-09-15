# Restauração do painel de carteira-modelo FII

## Escopo e causa

Em 15/09/2026, restaurada a apresentação rica solicitada pelo usuário.
O caminho de composição parcial retornava após uma tabela genérica e
não alcançava os componentes detalhados que continuavam presentes na página.

A composição parcial agora percorre os mesmos componentes da composição
integral: controles de personalização, tabela enriquecida, KPIs, cenários,
composição por tipo, cards por fundo, correlação, retrospectiva e curva de
volatilidade com IFIX, chat e indicação da possibilidade de publicação.
Os controles de elegibilidade abrem expandidos, mantendo limites, valores
iniciais, filtros e chaves de sessão.

Não foram alterados os motores de seleção, concessão, pontuação ou alocação.
As atualizações da metodologia 6.10.0 da main foram preservadas.
Não há alterações em B3, Americanas, autenticação ou bancos.

## Contrato da composição parcial

- Ativos, pesos sobre o capital total, saldo e bloqueios vêm do motor existente.
- Indicadores da parcela investida usam pesos relativos apenas para diagnóstico:
  r_i = peso_i / soma(pesos); número efetivo = 1 / soma(r_i²).
- DY histórico e cenários são médias ponderadas da parcela investida; o saldo
  não alocado não recebe retorno presumido.
- Renda recorrente reutiliza a regra vigente, com cobertura explícita;
  dados ausentes não são publicados como zero.
- Chat recebe o escopo dos indicadores e o saldo não alocado. A composição
  parcial não passa a ser publicável pela restauração da interface.
- Sem séries suficientes, a tela explica a ausência do gráfico; não inventa
  histórico. Sem provedor, mantém o aviso de configuração da LLM.

## Evidência reproduzível

Comandos executados com Python 3.12:

```powershell
python -m pytest tests/test_fii_rich_presentation.py tests/test_fiis_ui.py tests/test_llm_fii.py tests/test_portfolio_best_effort.py tests/test_fii_portfolio_v4.py tests/test_fii_concessao_de_protecao.py tests/test_fii_feasibility_diagnostics.py -q
python -m ruff check .
python scripts/run_quality_checks.py
python -m streamlit run tests/fii_rich_preview.py --server.address 127.0.0.1 --server.port 8513
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8514
```

Suite direcionada: 115 testes aprovados. Após a correção do número efetivo
na legenda histórica, os seis testes novos foram reexecutados e aprovados.
Lint global, exemplos financeiros, validação de Skills e varredura de segredos
aprovados. Inicialização de app.py e endpoint de saúde: HTTP 200, resposta ok.

AppTest executa a rota real da aba com limites ajustáveis, gráficos,
cards, chat simulado, atualização de contexto após alteração de pesos,
carteira integral, parcial, universo vazio e indisponibilidade de séries/LLM.
As fronteiras de banco e IA são substituídas por fixtures locais.

Inspeção no navegador local em 1280×720 e 390×844: cards, controles, gráficos e
chat presentes, sem erros de console relevantes. Dados e resposta de IA
sintéticos; nenhum provedor externo foi acionado. A skill oficial de browser
não estava disponível; foi usada a ferramenta de navegador disponível.
Os servidores de teste são encerrados ao fim da validação.

Limitações: esta validação não comprova disponibilidade de credenciais,
dados ou implantação no Streamlit Cloud; não executa persistência real de
carteira, ordens ou rebalanceamento.
