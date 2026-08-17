# Fase 3b — Motor de movimentação

Implementa a §8 da spec `2026-08-05-portfolio-global-design.md`: um motor
determinístico que transforma as saídas dos analisadores em ações concretas
(`aumentar`, `reduzir`, `vender`, `manter`), com peso atual, peso sugerido e
decomposição numérica do motivo.

## Estado verificado antes de planejar

Conferido no código, não assumido da spec:

| peça | estado |
|---|---|
| `core/portfolio_constraints.py` | existe; projeções em simplex com teto individual, por setor, por classe e dual |
| `core/rebalancing.py` | existe; `RebalancePolicy` (Protocol) + `CalendarRebalance`/`ThresholdRebalance`/`HybridRebalance` |
| `core/transaction_costs.py` | existe, **mas é calibrado para PF no Brasil / B3** — ver ressalva abaixo |
| `core/global_portfolio/roles.py` | `classificar(df_posicoes, ...)` → `PapelDoAtivo(symbol, papeis, evidencias, indeterminados, justificativa)` |
| `concentration.py` | `resumo`, `por_dimensao`, `hhi`, `gini`, `top_n`, `numero_efetivo` |
| `correlation.py` | `pares_redundantes`, `correlacao_media_por_ativo`, `matriz` |
| `factors.py` | `ResultadoExposicao`, `Exposicao` |
| `risk.py` | `Risco(vol_mensal, vol_anual, var_95, cvar_95, drawdown_max, n_obs)` |
| **contribuição marginal ao risco** | **NÃO EXISTE** — a §8 depende dela; é a Task 1 |

## Ressalva registrada: o modelo de custos não cobre a carteira

A spec diz "descontado por `transaction_costs.py` — os três já existem no
projeto". Ele existe, mas seu docstring é explícito: *"modelo de custos de
transação brasileiro"*, e os parâmetros confirmam:

- `_LARGE_CAP_PREFIXES` é uma lista fixa de tickers do IBOV (`PETR`, `VALE`,
  `ITUB`…). Nenhum símbolo americano casa, então `ADBE`, `TJX` e `PGR` cairiam
  como small cap, com spread de 30 bps — ordem de grandeza acima do real para
  mega caps líquidas.
- `ISENCAO_MES_VENDAS_DEF = 20_000` é a isenção mensal de vendas na B3.
  Aplicá-la a ativo no exterior e a FII assume um regime que não é o deles.

Hoje a carteira tem 46 ativos B3, 11 FII e 12 EUA. O modelo está correto para
uma classe e errado para as outras duas.

**Decisão adotada, aberta a correção:** o custo passa a ser resolvido **por
classe**, via um mapa `classe → CostConfig`. O `CostConfig` de `b3` continua
sendo o `brasil_pf_default()` já calibrado — nada que funciona muda. Para `us` e
`fii`, os parâmetros ficam **explícitos e visíveis na interface**, e o motor
declara qual configuração usou em cada recomendação. Não inventamos alíquota nem
isenção: os campos existem, aparecem na tela e o usuário os calibra. Enquanto não
forem calibrados, a recomendação exibe o custo como **não calibrado** em vez de
exibir um número que parece apurado e não é.

## Tasks

### Task 1: Contribuição marginal ao risco

**Files:** Modify: `core/global_portfolio/risk.py` · Test: `tests/test_global_risk.py`

`contribuicao_marginal(retornos, pesos) -> dict[str, float]` — a contribuição de
cada ativo para a volatilidade do portfólio: `MCR_i = w_i · (Σw)_i / σ_p`, onde
`Σ` é a covariância. A soma das contribuições **tem que fechar em `σ_p`** — é a
propriedade que torna a decomposição legítima, e é o teste central.

Reaproveitar `_covariancia_confiavel` de `correlation.py`, que já aplica o piso
de sobreposição. Ativo sem série suficiente sai como ausente, nunca como zero:
zero significa "não contribui para o risco", que é uma afirmação forte e falsa.

Devolver também a razão contribuição/peso — é ela que a §8 pede ("contribuição
marginal ao risco comparada ao peso"), e ela é que distingue o ativo que carrega
mais risco do que o peso sugere.

### Task 2: Sinais normalizados, com procedência

**Files:** Create: `core/global_portfolio/signals.py` · Test: `tests/test_global_signals.py`

Um `Sinal(nome, symbol, valor, direcao, analisador, texto)` por evidência, onde
`analisador` é o módulo que o produziu (`concentration`, `correlation`,
`factors`, `risk`, `roles`, `metrics`, `targets`). Essa procedência não é enfeite:
é o que a §8 exige para o motor não virar decoração, e o que a Task 5 verifica.

Os sete sinais da §8:

1. **valuation** — percentil na classe e contra o próprio histórico do ativo
2. **qualidade e `data_confidence`**
3. **contribuição marginal ao risco versus peso** (Task 1)
4. **redundância** — correlação alta com par melhor classificado
5. **estouro de limite** — por ativo, setor ou país
6. **ausência de papel estratégico** (`roles.classificar`)
7. **desvio do alvo da classe** (`portfolio_allocation_targets`)

Cada sinal é normalizado para `[-1, +1]`, onde negativo empurra para reduzir.
Normalizar por percentil dentro da classe, **não** contra o mercado inteiro:
escalas de FII, B3 e EUA não são comparáveis, e a Fase 2a já registrou isso ao
reportar qualidade por classe.

Função pura: recebe os quadros e as saídas dos analisadores, devolve os sinais.
Sem I/O, sem Streamlit.

### Task 3: Custo por classe

**Files:** Modify: `core/transaction_costs.py` · Test: `tests/test_transaction_costs.py`

`CostConfig` ganha `calibrado: bool` (default `True`, para não mudar o
comportamento de quem já usa) e um construtor `nao_calibrado(classe)`.
`custo_por_classe(classe, mapa) -> CostConfig` resolve a configuração.

`is_large_cap` passa a receber a classe: a heurística de prefixo do IBOV só se
aplica a `b3`. Para as demais, sem lista calibrada, o spread é o parâmetro
explícito da configuração — nunca o default de small cap por acidente de não
casar prefixo.

**Estritamente aditivo:** as assinaturas atuais continuam válidas e o
comportamento para `b3` é idêntico ao de hoje. Teste que fixa isso.

### Task 4: O motor

**Files:** Create: `core/global_portfolio/advisor.py` · Test: `tests/test_global_advisor.py`

`recomendar(df_posicoes, sinais, *, alvos, politica, custos) -> list[Acao]`.

`Acao(symbol, acao, peso_atual, peso_sugerido, score, componentes, analisadores,
custo_estimado, custo_calibrado)` — onde `componentes` é a decomposição numérica
que produziu o score e `analisadores` o conjunto de módulos que dispararam.

Fluxo: combinar sinais → peso alvo → projetar por `portfolio_constraints` →
resolver movimento por `rebalancing` → descontar por `transaction_costs`.

Duas regras que impedem recomendação irresponsável:

- **Movimento menor que o custo vira `manter`.** Recomendar uma realocação de
  0,3% que custa mais do que corrige é destruir valor com aparência de rigor.
- **Sinal sobre dado ausente não vira ação.** Se o ativo não tem série, não tem
  contribuição de risco; o motor registra `indeterminado` e não inventa um score
  a partir do que falta. Mesmo princípio de `roles.indeterminados`.

### Task 5: O motor consulta mesmo os analisadores

**Files:** Test: `tests/test_global_advisor_procedencia.py`

O teste que a §8 exige nominalmente. Com uma carteira sintética construída para
disparar cada um, verificar que `concentration`, `correlation` e `factors`
aparecem no campo `analisadores` de pelo menos uma recomendação.

Este teste falha se alguém "simplificar" o motor removendo um analisador do
caminho de decisão — que é exatamente como um painel vira decoração sem ninguém
perceber. Cada um dos três ganha sua própria asserção, para o teste dizer **qual**
caiu.

### Task 6: Painel na seção

**Files:** Modify: `views/portfolio_global.py` · Test: `tests/test_view_portfolio_global.py`

Cartões CSS (`_kpi_html`), nunca informação solta. Por recomendação: ação, peso
atual → sugerido, decomposição do score, **quais analisadores dispararam**, e o
custo — marcado como não calibrado quando for o caso.

Ressalva no painel, como em `roles`: são limiares escolhidos, não fatos sobre o
ativo. Limiares visíveis.

## Como verificar

Suíte completa a cada task. Base atual: **1799 passed, 3 skipped, 0 failed**.

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/ -q
```

## Fora de escopo

Chat LLM e crítico do motor (§9) e cenário macro (§10) são a Fase 4. O motor
precisa existir antes de haver o que criticar.
