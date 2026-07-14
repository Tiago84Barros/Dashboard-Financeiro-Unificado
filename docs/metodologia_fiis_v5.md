# Metodologia Integrada de FIIs v5

## Estado

A seção **Seleção de FIIs** usa um único processo de seleção. As antigas visões
“Score padrão” e “Qualidade diversificada” deixaram de produzir carteiras
concorrentes: seus sinais válidos foram incorporados ao motor específico por
tipo. A saída permanece uma **Lista de Diligência** até a aprovação dos gates de
dados e do backtest point-in-time.

- metodologia: `5.0.0`;
- fórmula: `br-fii-integrated-income-resilience-5.0.0`;
- objetivo: renda recorrente, crescimento patrimonial sustentável e resiliência
  em diferentes regimes do mercado brasileiro.

## Fluxo único

1. **Elegibilidade:** liquidez, DY, faixa plausível de P/VP, histórico,
   drawdown e filtros patrimoniais opcionais. Métrica ausente exigida reprova o
   fundo e gera uma razão auditável; não vira zero ou mediana.
2. **Score por categoria:** Tijolo, Papel, FoF e Híbrido são comparados somente
   com seus pares. Renda, P/VP e liquidez entram uma única vez.
3. **Estabilidade histórica:** tendência do retorno total e drawdown passam a
   integrar o componente de estabilidade. O uso é diagnóstico até a validação
   PIT, evitando interpretar desempenho in-sample como evidência preditiva.
4. **Qualidade e confiança:** cobertura, frescor, fonte, consistência e histórico
   permanecem separados da nota econômica.
5. **Otimização:** combina score, confiança e renda, com penalizações por
   concentração, perdas em cenários e correlação dos retornos mensais.
6. **Transparência:** a interface mostra elegibilidade, exclusões, pesos,
   métricas específicas, correlação, cenários, comparação retrospectiva e razões
   de seleção perante os pares.

## Correlação

A matriz exige ao menos 12 retornos mensais por fundo. Pares ausentes não são
tratados como correlação zero. Quando há cobertura parcial, o otimizador utiliza
a mediana dos pares observados como fallback explícito e informa a cobertura.
A matriz é projetada para uma forma semidefinida positiva antes de entrar na
função objetivo, evitando um termo de risco numericamente inconsistente.

## Função de seleção e risco

O componente de utilidade mantém a decomposição:

- score específico por tipo: 45%;
- confiança dos dados: 30%;
- renda observada: 25%;
- penalização por perdas estruturais: 35% sobre a perda média adversa.

Na etapa de pesos também entram penalização por concentração, perda de cauda e
correlação, esta última controlada pelo usuário. Os percentuais acima não devem
ser somados como uma média simples porque parte deles é penalização, não retorno.

## Publicação

A seleção só pode ser salva como Carteira Modelo quando:

- cobertura e confiança superam os mínimos vigentes;
- dimensões críticas de concentração têm look-through suficiente;
- consistência e atualização estão aprovadas;
- a versão `5.0.0` possui validação point-in-time aprovada.

Até lá, os resultados são prioridades reproduzíveis de diligência, não uma
recomendação definitiva de investimento.

## Evolução da base auditável

- **Preços Brapi Pro:** o endpoint dedicado de histórico alimenta uma tabela
  corrente para compatibilidade e uma tabela append-only por conteúdo. Cada
  observação preserva `available_at`, `knowledge_at`, qualidade temporal, hash e
  payload bruto. Backfills são marcados como retrospectivos e não simulam dados
  que estariam disponíveis no passado.
- **CRIs da CVM:** informes mensais de securitizadoras são versionados por hash.
  O vínculo com o FII exige CNPJ da emissora, emissão e série (ou identificador
  regulatório forte). Duration, LTV, rating, subordinação, inadimplência,
  indexadores e devedores só chegam ao fundo quando ao menos 60% da carteira é
  conciliável; valores impossíveis ficam em quarentena.
- **Relatórios públicos:** a fila usa claim atômico, retry com backoff e versão
  do parser. PDFs são endereçados por SHA-256, OCR é acionado quando necessário,
  evidências guardam página e contexto, e mudanças de layout exigem revisão.
- **Segurança:** as novas tabelas permanecem no schema `market`, com RLS ativo,
  política restritiva e privilégios revogados de `anon` e `authenticated`.

Comandos incrementais:

```bash
python run_market_ingest.py fiis-v2-history --years 20 --json
python run_market_ingest.py fiis-cvm-cri --years 5 --json
python run_market_ingest.py fiis-documents --limit 25 --json
```
