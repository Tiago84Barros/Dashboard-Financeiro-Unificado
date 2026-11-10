# Metodologia Integrada de FIIs v6

## Objetivo

A v6 transforma a seleção em um processo bitemporal e testável. A saída só
pode ser chamada de Carteira Modelo quando o backtest point-in-time, a cobertura
histórica, os regimes, os custos, a estabilidade e a segurança dos dados forem
aprovados. Até lá, permanece como Lista de Diligência.

## Novas camadas

1. **Universo histórico:** cadastro de fundos/classes da CVM preserva registro,
   situação e cancelamento. O COTAHIST da B3 registra tickers efetivamente
   negociados, inclusive os ausentes da lista corrente.
2. **Reconstrução PIT:** para cada fechamento mensal, o score usa somente dados
   com `knowledge_at <= decision_at`. Backfills sem data defensável não recebem
   aparência de informação histórica.
3. **Walk-forward:** seleção no fechamento, execução no próximo período,
   custos, slippage, turnover, cobertura de retorno, estabilidade do ranking,
   bootstrap em blocos e comparação com benchmark.
4. **Confiança empírica:** a confiança estrutural passa a incorporar posterior
   beta-binomial das revisões humanas por parser e métrica. Sem revisões, o prior
   é explícito e não substitui evidência.
5. **Entidades canônicas:** locatários, devedores, emissores, gestores e holdings
   são conciliados por identificador ou nome normalizado. Correspondências
   incertas ficam propostas para revisão.
6. **Otimização robusta:** os choques deixam de depender apenas do tipo do fundo.
   Vacância, inadimplência, LTV, duration, alavancagem, concentração e incerteza
   alteram as perdas de cenário de cada ativo. A função objetivo penaliza CVaR,
   concentração, correlação e falta de confiança.
7. **Monitoramento:** frescor, backlog documental, revisão humana, falhas de
   qualidade, cobertura, datas PIT e validação geram alertas persistentes.
8. **Releases regulatórias:** cada arquivo estruturado da CVM é identificado por
   hash, encadeado à revisão anterior e processado sob uma versão imutável do
   parser. O checkpoint combina tipo, ano, hash e parser; uma reapresentação não
   sobrescreve silenciosamente o vintage anterior.

## Gates mínimos

- ao menos 36 períodos elegíveis;
- cobertura histórica e das séries acima dos limites versionados;
- pelo menos 80% dos snapshots com disponibilidade comprovada;
- turnover anual abaixo do teto após custos;
- ranking estável;
- pelo menos três regimes macroeconômicos;
- intervalo bootstrap calculável;
- benchmark oficial IFIX e security master histórico carregados;
- dimensões críticas da carteira com cobertura suficiente.

Um gate reprovado não é convertido em nota média. Ele bloqueia a publicação e
é exibido ao usuário como limitação concreta.

## Comandos

```bash
python run_market_ingest.py fiis-registry --json
python run_market_ingest.py fiis-cvm-structured --years 11 --json
python run_market_ingest.py fiis-b3-history --years 17 --json
python run_market_ingest.py fiis-documents --limit 50 --recent-months 24 --json
python run_market_ingest.py fiis-entities --json
python run_market_ingest.py fiis-confidence --json
python run_market_ingest.py fiis-cotahist --warehouse --dry-run --json
python run_market_ingest.py fiis-pit-backtest --years 10 --json
python run_market_ingest.py fiis-monitor --json
```

## Cotação do fundo: a leitura mais nova vence

`market.fiis.price` não é mais só a cotação da brapi. A etapa `fiis-cotahist`
(`data_pipeline/market/fii_ingest.py::promover_fechamento_pregao`) compara cada
fundo com o fechamento mais novo do pregão da B3 em
`market.fii_b3_security_history` (COTAHIST; por `trade_date` vale a coleta mais
nova, só `close > 0`) e fica com a leitura de data mais nova. A regra é uma só,
`data_pipeline/market/fii.py::mais_nova_vence`, e é a mesma que a leitura
(`core/market_read._observacao_vence_base`, #707) usa para as observações.

- O fechamento vale às 18h de Brasília do dia do pregão, de modo que o
  fechamento do dia vence a cotação intradiária da brapi do mesmo dia.
- A data da brapi é o `regularMarketTime` do payload, não `updated_at`, que o
  gatilho renova a cada UPDATE, inclusive quando o `reprocess` regrava preço
  velho. Sem a coluna `price_at` (migration 088), a data-base é o limite
  superior: o mais novo entre o `regularMarketTime` do último payload e o
  `knowledge_at` das observações de preço.
- Quando o preço troca, o P/VP passa a ser preço ÷ VPA (`pvp_efetivo`) e o DY
  12m é reescalado pelo novo preço.
- A etapa roda na cadeia de FII da rotina local depois da carga do pregão e não
  depende da cota da brapi. Ela também roda dentro de `fiis`/`reprocess` e da
  carga v2.

## Limitação remanescente

O código não aprova retrospectivamente um histórico construído com dados que só
foram coletados hoje. A data de entrega CVM é aceita como evidência regulatória;
preços B3 são eventos observáveis de mercado. Fontes sem data defensável reduzem
a fração verificada. Essa restrição é intencional e evita look-ahead bias.
