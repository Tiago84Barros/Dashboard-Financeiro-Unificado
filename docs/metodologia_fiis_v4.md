# Metodologia FIIs v4 — renda, resiliência e rastreabilidade

## Estado do produto

A seção **Seleção de FIIs** produz uma **Lista de Diligência**. A promoção para
Carteira Modelo permanece bloqueada até que dados, backtest point-in-time e
validações estatísticas atendam aos gates definidos. Um score alto com baixa
cobertura não é tratado como evidência forte.

Versões atuais:

- metodologia: `4.1.0`;
- fórmula: `br-fii-income-resilience-4.1.0`;
- objetivo: renda recorrente, crescimento patrimonial sustentável e resiliência
  em diferentes regimes brasileiros.

## Modelos por categoria

O ranking é calculado **dentro da categoria**, sem comparar diretamente um FII
de CRI com um fundo de shopping ou um FoF.

| Categoria | Blocos específicos |
|---|---|
| Tijolo | vacância financeira, WAULT, locatários, geografia, cap rate implícito, qualidade, contratos, vencimentos e alavancagem |
| Papel | duration, indexadores, spread, LTV, rating, subordinação, inadimplência, devedores e emissões |
| FoF | desconto sobre NAV, dupla taxa, sobreposição, liquidez e qualidade dos fundos investidos |
| Híbrido | combinação explícita dos riscos aplicáveis, com cobertura reduzida quando o look-through não existe |

Todos recebem ainda renda por cota, recorrência, valuation específico, liquidez,
disciplina de emissões, taxas, conflitos e aderência ao mandato. Valor ausente
não vira zero, mediana ou nota neutra: sai do numerador, reduz cobertura e
confiança e, se crítico, bloqueia a publicação.

## Proveniência e qualidade

As migrations `023_fii_methodology_v4.sql` a
`026_fii_observation_maintenance_indexes.sql` criam e evoluem:

- `fii_metric_observations`: valor, data de referência, `available_at`, vintage,
  fonte, URL, payload bruto, qualidade e metadados;
- `fii_exposures`: gestor, setor, locatário, devedor, emissor, indexador, região
  e holdings com pesos e proveniência;
- `fii_universe_history`: fundos listados, encerrados, liquidados ou incorporados;
- `fii_score_snapshots`: inputs, componentes, faltantes, versões, cobertura,
  confiança e status de publicação;
- `fii_validation_runs` e `fii_rebalance_events`.
- releases imutáveis, `knowledge_at`, qualidade de disponibilidade, reconciliação
  Brapi×CVM, resultados de qualidade, documentos, parsers, evidências e linhagem.

O schema `market` continua server-side. Não foram criadas tabelas no schema
`public`, evitando exposição acidental pela Data API.

O comando abaixo audita duplicidade, datas impossíveis, stale data, tipos
inválidos, somas de exposições e ausência de look-through:

```bash
python run_market_ingest.py fiis-v4-audit --json
```

## Fontes e viabilidade

### Integração imediata, aberta

- Brapi Pro dedicada: `/api/v2/fii/indicators`, `indicators/history`, `reports`,
  `properties`, `properties/history`, `portfolio`, `portfolio/history` e
  `dividends`, além de `annual-reports` e `financials`. A integração preserva o envelope bruto, separa coleta por tipo e
  normaliza vacância, passivos, taxas, CRIs, emissores e holdings.
- Brapi legada: preço, histórico longo, proventos e liquidez via `/api/quote/`.
- CVM Informe Mensal: fonte regulatória para patrimônio, passivos, alavancagem,
  cotistas, liquidez e composição de ativos.
- CVM Informe Trimestral: composição e informações periódicas, com histórico
  aberto e reapresentações semanais.
- CVM documentos eventuais: fatos relevantes, regulamentos, relatórios
  gerenciais, ratings e documentos de emissões.
- CVM demonstrações financeiras: trilha contábil e análise de recorrência.

A CVM é tecnicamente integrável por arquivos. Métricas presentes apenas em PDF
ou texto exigem extração, reconciliação e revisão humana; por isso começam como
`observed` e não como `validated`.

### Extração pública auditada

Não há dependência prevista de Bloomberg, Economatica, Quantum, ComDinheiro,
UP2DATA ou fornecedores privados. WAULT, contratos, locatários, devedores,
garantias, LTV, subordinação, rating, indexadores e spreads entram apenas quando
constarem da Brapi/CVM ou de documentos públicos. PDFs são versionados por hash,
extraídos por texto/OCR e geram evidências pendentes de revisão. Emissor nunca é
rebatizado como devedor sem evidência explícita.

### Estado operacional em 12/07/2026

- migrations 024, 025 e 026 aplicadas e verificadas;
- 475.188 observações, 188.271 exposições e 19.378 documentos eventuais
  encontrados após os backfills Brapi Pro e CVM estruturada;
- 301.265 observações mensais, 58.002 trimestrais, 8.298 anuais, 5.816 de
  demonstrações financeiras e 3.846 de documentos eventuais provenientes da CVM;
- universo prospectivo, releases, linhagem, reconciliação e validation runs
  materializados;
- zero duplicidade, data futura, referência impossível, soma inválida de
  exposição ou histórico rotulado indevidamente como PIT;
- valores fora do domínio foram colocados em `rejected`, sem exclusão física;
- zero conflito de reconciliação Brapi×CVM permanece aberto;
- cobertura média de 57,89%, confiança média de 69,19% e 129 de 392 fundos
  (32,91%) com dados suficientes segundo os gates individuais da versão 4.1;
- a publicação permanece bloqueada exclusivamente pela ausência do backtest PIT
  walk-forward aprovado.

O acesso Brapi Pro foi validado nos endpoints dedicados e o backfill foi
concluído. O pipeline diferencia falha de autenticação, limite de requisições e
falha transitória, sem registrar ou expor o token nos artefatos de auditoria.

A carga melhorou a completude sem alterar o status de publicação: todos os
fundos continuam em diligência até a validação point-in-time ser aprovada.

## Carteira e cenários

A otimização SLSQP combina score, confiança, renda e perdas estruturais. Há
limites para ativo, gestor, setor, locatário, devedor, emissor, indexador,
região e parcela ilíquida. Os limites look-through só são avaliados quando a
dimensão tem cobertura mínima; abaixo disso a carteira fica **não publicável**.

A formação do conjunto candidato usa programação inteira-mista para respeitar
simultaneamente o número máximo de ativos, as bandas por tipo e os limites de
concentração antes do refinamento dos pesos pelo SLSQP. Para cada fundo
selecionado, a interface apresenta posição entre pares do mesmo tipo, componentes
fortes, diferenças de score, confiança, cobertura e renda, além das métricas
críticas ainda ausentes. Essas explicações justificam prioridade de diligência,
não constituem recomendação automática de compra.

As bandas de tijolo, papel, FoF e híbrido mudam nos regimes de juro real alto,
queda de Selic, inflação alta e estresse. Os cenários de Selic, inflação,
vacância e crédito são testes de sensibilidade, não previsões.

O rebalanceamento é disparado por eventos: mudança de regime, evento relevante,
violação de limite, perda de confiança, redução material do risco ou ganho de
renda líquido de custos. A passagem do tempo, isoladamente, não dispara giro.

## Validação necessária

Antes da liberação, o processo exige:

1. snapshots com `available_at <= data da decisão`;
2. universo histórico incluindo fundos encerrados/incorporados;
3. retorno total subsequente, custos e slippage;
4. comparação com a série **oficial** do IFIX — XFIX11 pode ser diagnóstico,
   mas não substitui o benchmark de validação;
5. turnover, estabilidade Spearman/Jaccard, bootstrap do excesso de retorno;
6. resultados separados por alta/queda da Selic, inflação e estresse;
7. número mínimo de períodos, cobertura e regimes.

Sem esses itens, `validation_status` permanece `blocked` e todos os snapshots
são `diligence_only`.

## Implantação

1. Aplicar, em ordem, as migrations pendentes até `026_fii_observation_maintenance_indexes.sql`.
2. Executar as coletas CVM/Brapi já existentes.
3. Executar `python run_market_ingest.py fiis-v2 --json` para coletar os
   endpoints dedicados, derivar métricas e materializar snapshots.
4. Executar `python run_market_ingest.py fiis-cvm-structured --years 5 --json`.
5. Executar `python run_market_ingest.py fiis-v4-audit --json`.
6. Processar documentos públicos com `python run_market_ingest.py fiis-documents --json` e revisar evidências.
7. Acumular/reconstruir vintages históricos e executar a validação PIT.

Não se deve mudar manualmente o status da metodologia para `passed`; ele deve
ser consequência de um `fii_validation_run` reproduzível e aprovado.

## Referências oficiais

- CVM — Informe Mensal: https://dados.cvm.gov.br/dataset/fii-doc-inf_mensal
- CVM — Informe Trimestral: https://dados.cvm.gov.br/dataset/fii-doc-inf_trimestral
- CVM — Documentos eventuais: https://dados.cvm.gov.br/dataset/fi-doc-eventual
- CVM — Demonstrações Financeiras: https://dados.cvm.gov.br/dataset/fii-doc-dfin
- CVM — Resolução 175, Anexo III: https://conteudo.cvm.gov.br/legislacao/resolucoes/resol175.html
- Brapi — FIIs: https://brapi.dev/docs/fiis
