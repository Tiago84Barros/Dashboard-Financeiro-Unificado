# Valuations da aba Carteira

Painel abaixo dos totais em Investimentos → Carteira: DY, P/L, P/VP, PSR,
P/EBIT, EV/EBITDA e EV/EBIT. Implementação localizada, sem alterar navegação.

Fórmula por indicador: `soma(valor_mercado_BRL * indicador) /
soma(valor_mercado_BRL com indicador válido)`. É média aritmética ponderada
descritiva, não múltiplo contábil consolidado ou média ponderada por enterprise value.
DY recebe pontos percentuais dos adaptadores existentes; múltiplos recebem vezes.
Não se somam lucros de companhias às distribuições de FIIs.

Ausências, não finitos e múltiplos não positivos são excluídos, sem imputação zero.
DY zero é válido; DY negativo não. Valores de posição não positivos ou ausentes
não compõem os pesos. Lotes acumulam exposição e contam o ticker uma só vez.
Cobertura é valor das posições com indicador válido dividido pelo valor positivo
conhecido de toda a carteira. Não é cobertura apenas da renda variável.

Fontes reutilizadas: reconciliação B3/Fundamentus e batch de FIIs Fundamentus.
Cache público por tickers de uma hora; não recebe quantidades, saldos ou dono.
Falha de uma fonte não apaga a outra e é indicada sem expor conexão.
Não há escrita de dados nem novo banco. Ações fracionárias usam o ticker-base.
Tesouro, renda fixa, ETFs, BDRs e exterior não são preenchidos artificialmente.
Falta adaptar suas fontes a indicadores e janelas comparáveis para ampliar cobertura.

Consulta não é data-base contábil: o painel explicita que fontes podem ter datas
e janelas distintas. DY não é retorno futuro nem rendimento realizado do usuário.

## Verificação — 07/09/2026

- RED: teste inicialmente falhou pela ausência do novo módulo.
- `python -m pytest -q tests/test_portfolio_valuations.py tests/test_investimentos_analise_ui.py tests/test_investimentos_correlacao.py`:
  47 passaram; inclui AppTest sintético do painel e falha parcial de fonte.
- `python -m ruff check core/portfolio_valuations.py design/portfolio_valuations.py tests/test_portfolio_valuations.py`: aprovado.
- `python scripts/run_quality_checks.py`: aprovado, incluindo varredura de segredos,
  fórmulas, skills e três testes de ambiente.
- `python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8513 --server.headless true`:
  servidor iniciado; encerrado após smoke test.
- Navegador real não revalidado: controle havia sido bloqueado por impossibilidade
  de confirmar a URL. AppTest não certifica responsividade ou aparência no deploy.
- Não houve publicação, commit ou push; a página hospedada só muda após deploy.
