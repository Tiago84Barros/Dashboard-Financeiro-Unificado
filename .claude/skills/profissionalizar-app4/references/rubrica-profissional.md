# Rubrica profissional do App 4

Esta rubrica define um piso auditável, não uma promessa de retorno ou certificação
regulatória. Um gate sem evidência é `NÃO_VERIFICADO`, nunca aprovado.

## Escala

- `APROVADO`: comportamento demonstrado por teste ou inspeção independente.
- `APROVADO_COM_RESSALVAS`: risco baixo/médio conhecido, com limite e plano.
- `REPROVADO`: falha de requisito, evidência contraditória ou achado alto/crítico.
- `NÃO_VERIFICADO`: ambiente/evidência indisponível; bloqueia o veredito final.

## G1 — Contrato analítico e governança

- objetivo, público, horizonte, universo elegível e limites de uso definidos;
- fatos, inferências, estimativas e cenários identificados separadamente;
- versão de metodologia/modelo e changelog reproduzíveis;
- fontes, períodos, moedas, unidades, premissas, defasagens e limitações visíveis;
- disclaimer e revisão humana; nenhuma execução automática de investimento.

## G2 — Dados, proveniência e temporalidade

- linhagem da fonte ao indicador, identidade de entidade e data de referência;
- schemas/contratos versionados, validação de tipos, escalas, unicidade e range;
- ausência continua ausência; imputação, quando usada, é marcada e testada;
- frescor, cobertura e qualidade medidos por módulo, com gate proporcional ao uso;
- snapshots atômicos, hash/proveniência, idempotência e last-known-good;
- point-in-time real: publication lag, corporate actions, universo histórico e
  controles contra look-ahead/survivorship;
- reconciliação entre fontes tem tolerância justificada e divergência visível.

## G3 — Análise de empresas e FIIs

- fórmulas determinísticas com exemplos independentes e testes de borda;
- normalização contábil e tratamento coerente de setor/tipo de veículo;
- qualidade, valuation, crescimento, risco, liquidez e sustentabilidade avaliados
  sem dupla contagem silenciosa;
- pesos, thresholds e penalidades justificados, versionados e testados;
- score acompanhado por decomposição, confiança/incerteza e evidência-fonte;
- RAG/LLM apenas sintetiza contexto permitido, cita evidência e recusa inventar;
  cálculos e gates materiais permanecem determinísticos.

## G4 — Construção de portfólio

- política de investimento explicita objetivo, benchmark, horizonte e bandas;
- universo e filtros são anteriores ao ranking e reproduzíveis;
- limites por ativo, emissor, setor, classe, país e moeda conforme aplicável;
- exposição look-through, liquidez, capacidade, custos e impostos considerados;
- risco inclui concentração, correlação, volatilidade/drawdown quando a amostra
  permite e cenários de estresse; quantidade de ativos não prova diversificação;
- otimização é comparada com baseline simples (incluindo 1/N quando coerente),
  tem sensibilidade e fallback determinístico;
- rebalanceamento mostra valores/pesos antes e depois, custos, liquidez e impacto,
  mas exige decisão humana.

## G5 — Portfólio Global

- adapters B3, FII e EUA respeitam contrato canônico sem perder proveniência;
- conversão de moeda usa taxa, timestamp e convenção explícitos;
- retorno local e retorno cambial não são somados incorretamente;
- agregados reconciliam com componentes e tolerância documentada;
- risco consolidado cobre país, moeda, classe, setor/emissor e exposições
  correlacionadas, sem dupla contagem;
- falha de um adapter degrada de forma visível, não zera o patrimônio.

## G6 — Validação quantitativa

- backtest walk-forward/point-in-time com benchmark, moeda e período alinhados;
- separa desenho/calibração de avaliação fora da amostra;
- inclui custos e turnover realistas, delistings/corporate actions quando materiais;
- reporta tamanho de amostra, dispersão/intervalo, estabilidade e regimes;
- testes de sensibilidade, ablação e estresse desafiam as conclusões;
- nenhuma seleção oportunista de período, benchmark ou métrica.

## G7 — Engenharia, banco e segurança

- camadas e contratos do projeto respeitados, consultas parametrizadas e acesso
  remoto no menor privilégio;
- migrações versionadas, aditivas quando possível, idempotentes, com backup,
  rollback e teste em banco descartável;
- credenciais e dados pessoais ausentes de código, logs e artefatos;
- falhas de API/banco/LLM têm timeout, retry limitado, circuit/fallback coerente e
  estado de erro observável;
- cache não mistura usuários nem serve dado vencido silenciosamente.

## G8 — Qualidade de software e experiência

- requisitos mapeados para testes unitários, integração e regressão;
- casos de ausência, negativo, NaN/inf, duplicidade, data inválida, timeout,
  fonte stale, permissão negada e LLM malformado cobertos conforme aplicável;
- `python scripts/run_quality_checks.py --full` passa sem enfraquecer gates;
- Streamlit inicia e os quatro fluxos são validados no navegador com dados
  sintéticos, incluindo estados vazio, loading, erro e mobile;
- UI informa atualização, qualidade/confiança e limitações no ponto de decisão;
- documentação do app e cofre concordam com o comportamento observado.

## Regra de aprovação final

Todos os gates G1–G8 devem estar `APROVADO` nos quatro módulos em que forem
aplicáveis. `APROVADO_COM_RESSALVAS` pode compor uma entrega intermediária, mas
não autoriza `APP4_PROFISSIONAL_APROVADO`. Não pode haver achado crítico/alto,
skip novo, `NÃO_VERIFICADO` obrigatório ou divergência não explicada.
