# Proteção ao investidor — Empresas Americanas

**Data:** 15/09/2026
**Escopo:** módulo EUA (dossiê determinístico, Criação de Portfólio, prompts de
LLM e as três telas que consomem `red_flags`).
**Antecedentes:** mesma linha de trabalho já aplicada a Seleção de FIIs (PR
#251) e a Empresas B3 (PR #254).

A regra que governa tudo abaixo: **nunca deixar zerada a criação de qualquer
portfólio.** Toda proteção acrescentada aqui tem uma saída declarada, e nenhuma
delas pode entregar tela em branco — quem recebe carteira vazia não recebe
proteção nenhuma, recebe a ausência do motor e decide sozinho.

---

## 1. O defeito: a bandeira que aparecia em tudo

Até 15/09/2026 toda red flag de `core/us_dossie.red_flags` lia o dicionário de
métricas do **último exercício**: `net_debt_ebitda > 4`, `interest_coverage < 2`,
`_fcf < 0`. A linha emitida era a mesma, palavra por palavra, para quem teve um
ano ruim em cinco e para quem teve cinco em cinco. As três telas imprimiam
todas em `st.warning` (amarelo) ou com 🚩.

Medido no armazém local sobre o universo elegível: **2.419 das 3.737 empresas
(65%) acendiam pelo menos uma linha.** Uma bandeira que aparece em dois terços
do universo não informa nada — ela desliga a atenção do leitor exatamente onde
deveria prendê-la.

### Por que "qualificar" e não "rebaixar"

Na B3 o padrão dominante era o período isolado, e lá coube reclassificar
famílias inteiras de sinal (`MOMENTUM:`, `DADOS:`). Nos EUA a medição diz o
contrário. Entre as 1.578 empresas com fluxo de caixa livre negativo no último
exercício:

| persistência na janela de 5 | empresas |
|---|---|
| só o último ano | 133 |
| 2 a 4 dos últimos 5 | 764 |
| todos os 5 | 681 |

A maioria é crônica. **Rebaixar por prefixo, como na B3, teria afrouxado
proteção genuína.** O que faltava não era severidade menor: era a frequência
dita em voz alta. Por isso aqui cada sinal é *qualificado* pela persistência
medida, não reclassificado em bloco.

---

## 2. O que passou a existir

### 2.1 `core/us_risco_historico.py` — a medição

Motor puro, por exercício, sobre janela de 5 anos (`JANELA_PADRAO`). Seis
condições: alavancagem, cobertura de juros, FCL negativo, conversão de caixa,
dívida/patrimônio e patrimônio líquido negativo.

Três decisões que mudam o resultado:

- **Ano não avaliável sai do denominador.** Denominador nulo ou campo ausente
  devolve `None` e o ano não conta nem a favor nem contra. Contar ausência como
  ano limpo inflaria a saúde de quem não reportou — o defeito de
  `faixa-de-validacao-apaga-evidencia`, já pago uma vez neste projeto.
- **Maioria é estrita** (`acesos * 2 > anos`). Metade exata é empate, e empate
  cai do lado de contexto, que continua visível.
- **Abaixo de 3 exercícios avaliáveis não se julga** (`MIN_ANOS_PARA_JULGAR`).
  A ignorância é declarada como limitação de cobertura — nem risco, nem
  atestado de saúde.

### 2.2 `core/severidade_flags.py` — o vocabulário, em fonte única

Três severidades: `risco_confirmado`, `contexto_observado`,
`limitacao_cobertura`, derivadas do prefixo da linha (`CONTEXTO:`,
`COBERTURA:`, sem prefixo). **Prefixo desconhecido cai em risco confirmado** —
o lado seguro é aparecer.

A regra mora num módulo só, e há um teste de AST que falha se qualquer outro
arquivo de `core/` ou `views/` voltar a comparar esses prefixos na mão. É a
resposta direta a `guarda-duplicada-diverge`: três cópias, duas divergências.

### 2.3 A qualificação em texto (`_linha_de_persistencia`)

| medição | saída |
|---|---|
| maioria estrita, 3+ exercícios | risco confirmado, "em N dos últimos M exercícios" |
| maioria, mas não no último ano | risco confirmado + "(não no último exercício)" |
| acendeu sem maioria | `CONTEXTO:` |
| menos de 3 exercícios avaliáveis | `COBERTURA:` |
| nunca acendeu | nenhuma linha |

**Mudança de comportamento deliberada:** uma condição que acende na maioria dos
anos mas **não** no último agora emite linha de risco onde o código antigo não
emitia nada. Isso amplia a proteção, e a ressalva vai junto para não virar
alarme falso. Qualidade histórica é o critério, não a foto.

Rede de segurança: `compute_company_metrics` monta cada métrica com `_latest`,
que aceita o valor não nulo mais recente ainda que venha de exercício anterior;
a medição por ano não aceita. Quando nenhum ano da janela é avaliável e a foto
mesmo assim acusa, a linha aparece como **limitação de cobertura** — sumir com
ela seria pior, carimbá-la de risco confirmado seria falso.

### 2.4 Os quatro consumidores

Os três blocos saem apartados, com cabeçalho próprio, em:

- `core/us_dossie.dossie_to_text` (o que a LLM lê);
- `core/portfolio_report_us` — regra 4b do prompt de carteira, espelho da regra
  5.4 da B3: ensina a LLM que contexto observado **não** justifica veto nem
  ressalva por si só;
- `views/empresas_americanas.py` — `st.warning` só no risco confirmado,
  `st.info` no contexto, `st.caption` na cobertura; a seção "Sinais de alerta"
  do dossiê completo só existe se houver risco confirmado;
- `views/analise_portfolio_us.py` — o 🚩 só marca risco confirmado.

---

## 3. O piso de qualidade não pode zerar a carteira

Auditando o passo anterior apareceu um defeito estrutural **independente** das
red flags: `core/us_quality_floor.apply_with_substitution` não tinha guarda de
viabilidade (a B3 tem), e `select_industry_leaders` podia devolver DataFrame
vazio. Isto é, o piso de qualidade — introduzido para proteger — podia entregar
carteira vazia, que é a única saída que a regra do projeto proíbe.

Duas travas, em níveis diferentes:

**Guarda de viabilidade (por indústria)** — `_cede_por_viabilidade`, versão
`us-quality-floor-1.1.0`. Se nenhum candidato da indústria sobrevive ao piso, o
líder é readmitido **somente** quando *todos* os motivos da exclusão são
acessórios: Altman em aflição, payout acima de 1,5×, accruals elevados,
Piotroski fraco. Cada um deles pesa abaixo do corte de 10 em
`build_entry_scores` — o próprio motor declara que nenhum exclui sozinho, só a
soma. Ceder a soma é a cessão mínima.

Falha estrutural **nunca** é cedida: alavancagem, liquidez, margem líquida,
fluxo de caixa livre e cobertura de juros são o que o piso existe para barrar.
E "Excluída" por `entry_score < 30` traz "sem alerta crítico" como motivo, que
não cabe no conjunto acessório — logo também não é cedida.

Os nove rótulos de risco viraram constantes em `core/us_advanced_lab.py`
(`RISCOS_ESTRUTURAIS`, `RISCOS_ACESSORIOS`) porque o piso decide **lendo esses
textos**: literal repetido em dois módulos diverge com o tempo, e a vaga da
indústria passaria a ser cedida por erro de digitação.

**Rede final (carteira inteira)** — a guarda acima age por indústria e não tem
como saber que aquela era a última. Se o piso esvaziou todas,
`select_industry_leaders` remonta a carteira **pré-piso** e grava
`carteira_preservada` no log. A carteira volta, a cessão é declarada.

**Transparência obrigatória:** as duas cessões são renderizadas no painel
"⛔ Empresas barradas pelo piso de qualidade" — aviso, tabela de readmitidos com
motivo, e `st.error` no caso da rede final. Proteção que cede em silêncio não é
proteção; é o próprio defeito que o piso veio corrigir.

---

## 4. Efeito medido

Medido em 15/09/2026 contra o armazém local, sobre as 3.701 empresas com
símbolo distinto em `load_scoring_frame`. É população ligeiramente diferente
das 3.737 citadas na seção 1, que vinham do filtro de elegibilidade — as duas
medições não se substituem. Nenhuma empresa ficou sem leitura (`lidas=3701`,
`erro=0`).

| | regra antiga (foto) | regra nova (persistência) |
|---|---|---|
| empresas com pelo menos um alarme de topo | **2.394** (64,7%) | **2.096** (56,6%) |
| linhas emitidas, total | 4.373 | 6.810 |
| — risco confirmado | 4.373 (todas) | 3.187 |
| — contexto observado | — | 2.147 |
| — limitação de cobertura | — | 1.476 |

Duas leituras, e a segunda importa mais que a primeira.

**Menos empresas carregam o alarme de topo** — 298 a menos, de 2.394 para
2.096. São as que acendiam por um exercício isolado e agora saem como contexto
observado, visíveis mas sem gritar.

**E ainda assim o motor emite mais linhas** — 6.810 contra 4.373. A regra
antiga só via o último exercício; a nova enxerga a janela inteira e passa a
dizer o que a foto não mostrava: condição que acendeu na maioria dos anos mas
não no último, e condição que a série curta não permitiu apurar. **A proteção
foi ampliada, não afrouxada.** O que mudou foi a repartição: de tudo amarelo
para 47% risco confirmado, 32% contexto e 22% cobertura declarada.

Distribuição das condições acesas na foto (regra antiga), para referência: FCL
negativo 1.564, cobertura de juros 1.450, patrimônio líquido negativo 442,
conversão de caixa 368, alavancagem 312, dívida/patrimônio 237.

---

## 5. Testes

`tests/test_us_severidade_historica.py` (14) e os 6 novos casos em
`tests/test_us_quality_floor.py`. **Todos verificados por mutação, executada
neste trabalho:** cada teste foi confrontado com a remoção ou inversão da linha
de produção que diz cobrir, e falhou em todos os 16 casos.

Os casos que interessam:

- a carteira **não sai vazia** quando o piso reprova o universo inteiro —
  teste direto da regra do projeto;
- falha estrutural mantém a vaga vazia (`sem_substituto`), cessão não vaza;
- substituto aprovado tem precedência sobre a cessão: ela é o último recurso;
- prefixo desconhecido cai no lado seguro;
- nenhum outro módulo compara os prefixos de severidade (AST).

`tests/test_us_dossie.py::test_red_flags` foi renomeado para
`test_red_flags_sem_serie_cai_na_rede_da_foto` e passou a afirmar o que de fato
exercita — a rede da foto, saindo como limitação de cobertura. O nome antigo
descrevia uma cobertura que o teste tinha deixado de ter.

A trava de arquitetura `test_o_piso_nao_define_limiar_proprio` passou a ler a
**AST** em vez do texto: docstring e comentário precisam poder nomear os
critérios — é assim que a guarda de viabilidade explica o que cede —, e filtrar
por substring transformava documentar em defeito.

---

## 6. Limitações conhecidas

- A janela de 5 anos é a que o EDGAR sustenta para a maioria do universo;
  empresa com série mais curta sai como limitação de cobertura, não como risco.
- `classify_company` ainda devolve "inadequada — histórico/dados insuficientes
  para análise confiável" quando `years < 3`. Isso **condena por dado
  ausente**, que é o oposto do princípio adotado aqui. Fica registrado: está
  fora do escopo aprovado deste trabalho.
- A guarda de viabilidade cede a soma de sinais acessórios, e essa soma é real.
  A carteira resultante é a melhor disponível naquela indústria, não uma
  carteira aprovada.
