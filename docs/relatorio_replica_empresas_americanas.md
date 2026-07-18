# Relatório de replicação — Empresas B3 → Empresas Americanas

## Resultado

A seção **Empresas Americanas** passa a usar o mesmo contrato de navegação da
seção **Empresas B3**:

1. Empresas por Setor;
2. Análise de Empresa;
3. Análise Avançada;
4. Criação de Portfólio;
5. Avaliação de Portfólio.

A camada visual permanece em Streamlit e usa um componente compartilhado real
entre B3 e EUA para botões, busca, cabeçalhos setoriais e cards. A diferença fica
na inteligência, nos filtros do universo e na origem dos dados.

A camada de apresentação também foi integralmente localizada para português do
Brasil. Descrições SEC/SIC em inglês são mantidas como campos brutos para busca e
auditoria, mas aparecem traduzidas e agrupadas em setores macroeconômicos nos
cards, filtros, tabelas, gráficos, comparações, dossiês e carteiras.

## Elementos preservados

- navegação principal em cinco áreas e persistência da área ativa na sessão;
- vitrine em quatro cards por linha, com logo/placeholder, ticker, nome,
  setor/indústria, seleção e botão Analisar;
- busca por ticker, nome, setor ou indústria, tolerante a caixa e acentuação;
- exploração por setor, indústria, ticker e nome;
- análise fundamentalista individual com KPIs, demonstrações históricas e
  gráficos de resultados, margens, caixa e balanço;
- score 0–100, trilhas de fatores, classificação e explicação;
- ranking do universo e filtros setoriais;
- comparação simultânea de empresas e comparação de pares da mesma indústria;
- indicadores avançados, dashboards, tabelas e gráficos;
- criação de carteira com número de ativos, modo de ponderação e limites por
  posição e setor;
- simulação do capital por posição e visualização da alocação;
- backtest walk-forward point-in-time, Rank-IC, retorno, excesso, Sharpe,
  Sortino, Calmar, drawdown, volatilidade e turnover;
- avaliação de carteira com score consolidado, cobertura, concentração,
  diversificação, exposição setorial, diagnóstico por posição e ações sugeridas;
- transparência metodológica, auditoria de qualidade e operação offline-first.

## Adaptações para o mercado americano

| Camada B3 | Equivalente americano |
|---|---|
| CVM/DFP/ITR | SEC EDGAR, 10-K e XBRL |
| código CVM/ticker B3 | CIK permanente + histórico de ticker |
| BRL | USD |
| setores/segmentos B3 | setores macro dos EUA em português + indústria SEC/SIC traduzida |
| padrões contábeis locais | US GAAP e regras de disclosure da SEC |
| Selic | Federal Funds Rate |
| IPCA | CPI dos EUA |
| PIB Brasil | PIB real dos EUA |
| desemprego/atividade Brasil | unemployment rate e mercado de trabalho dos EUA |
| curva DI/NTN | Treasury 10Y–2Y |
| risco de crédito local | US high-yield spread |
| dividend yield/JCP | shareholder yield (dividendos + recompras − emissões) |
| juros sobre capital próprio | sem equivalente; removido da inteligência americana |
| taxonomia B3 | NYSE/Nasdaq/NYSE American + setor/indústria |
| documento conhecido pela publicação local | `available_at` pela filing date da SEC |

## Novos indicadores e critérios

- **Shareholder Yield**, incluindo recompras e diluição, mais adequado aos EUA;
- **FCF Yield** e **P/FCF**, com maior relevância para empresas intensivas em
  ativos intangíveis e remuneração baseada em ações;
- **Piotroski F-Score** com contagem explícita dos critérios avaliáveis;
- **Altman Z-Score** com retained earnings e market cap;
- **Accruals de Sloan** para qualidade do lucro;
- **ROIC incremental** para medir retorno do capital novo;
- **cobertura do score**, evitando tratar ausência como zero;
- **HHI e número efetivo de ativos** na avaliação de carteira;
- **regime macro EUA 0–100** com Fed Funds, CPI, PIB real, desemprego, curva
  10Y–2Y e high-yield spread;
- **impacto macro por setor**, aplicado como ajuste separado e visível — nunca
  misturado silenciosamente ao score fundamentalista base.

## Metodologia de score americana

O score continua em escala 0–100 e preserva a lógica de múltiplas dimensões. A
versão americana usa seis trilhas: Qualidade, Crescimento, Solidez, Eficiência de
Capital, Valuation e Retorno ao Acionista. Cada métrica é winsorizada e ranqueada
por percentil dentro da indústria; grupos pequenos usam fallback para o universo.
Ausência recebe valor neutro, não zero. Os pesos são renormalizados e podem ser
adaptados por setor — por exemplo, Real Estate e Financial Services.

As faixas visuais são: Excelente (≥75), Forte (≥65), Neutra (≥50), Fraca (≥35)
e Crítica (<35). Elas servem para triagem e não constituem recomendação.

## Ambiente regulatório e tributário

- a fonte primária é a SEC; `available_at` usa a filing date para evitar
  look-ahead;
- o CIK ancora a identidade quando o ticker muda ou é reutilizado;
- empresas deslistadas permanecem no histórico para reduzir survivorship bias;
- REITs e Financial Services recebem leitura setorial diferenciada;
- recompras, stock-based compensation e emissão/diluição são economicamente mais
  relevantes nos EUA que JCP;
- tributação do investidor (withholding, residência fiscal, estate tax e tratados)
  não altera o score da empresa: depende do perfil do usuário e deve ficar fora
  do ranking fundamentalista automático;
- o módulo não emite conclusão jurídica ou tributária individual.

## Melhorias que também beneficiariam Empresas B3

1. Levar HHI, número efetivo de ativos e cobertura ponderada à avaliação B3.
2. Separar explicitamente score fundamentalista e ajuste macro também na B3.
3. Exibir a cobertura de métricas por empresa em todos os rankings B3.
4. Adotar identificação permanente/alias temporal mais forte para mudanças de
   ticker na B3, análoga ao CIK.
5. Padronizar shareholder yield (proventos + recompras − emissões) quando houver
   dados brasileiros confiáveis.
6. Consolidar os testes PIT e anti-survivorship das duas áreas em contratos
   compartilhados, mantendo fontes e calendários específicos.

## Arquivos principais

- `views/empresas_americanas.py`: experiência unificada em cinco áreas;
- `core/market_companies.py`: normalização, universo acionário e busca comum;
- `design/market_companies.py`: navegação, cabeçalhos e cards usados por B3/EUA;
- `core/us_score.py`: score relativo por indústria;
- `core/us_portfolio.py`: construção e limites da carteira;
- `core/us_portfolio_analysis.py`: avaliação consolidada da carteira;
- `core/us_macro.py`: cenário e regime macroeconômico dos EUA;
- `core/us_read.py` / `core/us_data.py`: leitura offline-first;
- `data_pipeline/us/`: EDGAR, normalização, identidade, preços e PIT.
