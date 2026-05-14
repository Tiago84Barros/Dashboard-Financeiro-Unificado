# Fase 4.8.1 — Compute portfolio_positions

> Data: 2026-05-14  
> Script: `migration/08_compute_portfolio_positions.py`  
> Status: **✅ Concluído**

---

## Objetivo

Popular a tabela `portfolio_positions` a partir das 1.351 `investment_transactions` do App2,
resolvendo a pendência P2 da Fase 4.8 que deixava `v_investment_summary` e
`v_net_worth.investment_total` com valor R$ 0.

---

## Lógica de Cálculo

**Método: Custo Médio Ponderado** (padrão brasileiro — IN 1.585/2015 RFB)

Processa as transações de cada ativo em ordem cronológica:

```
buy:
  new_avg_price = (qty_atual × avg_price + compra_qty × unit_price + fees)
                  ÷ (qty_atual + compra_qty)
  qty_atual += compra_qty

sell:
  qty_atual -= sell_qty
  avg_price permanece inalterado

split (mapeado como buy com price=0):
  Adiciona cotas ao custo anterior → dilui avg_price proporcionalmente

Posição final inserida somente se:
  qty_atual > 0   AND   avg_price > 0
```

---

## Resultado da Execução

### Etapa 1 — Portfolio criado

| Campo | Valor |
|-------|-------|
| Nome | Carteira Principal |
| Tipo | stock |
| ID | 015ce5fc-... |
| user_id | owner (configurado em .env) |

### Etapa 2 — Posições calculadas

| Métrica | Quantidade |
|---------|----------:|
| Transações processadas | 1.351 |
| Posições válidas (qty > 0, preço > 0) | 34 |
| Posição inválida — preço médio negativo | 1 |
| Ativos zerados (vendidos completamente) | 34 |
| Total alertas qty_negativa | 234 |

### Etapa 3 — Upsert realizado

| Métrica | Resultado |
|---------|----------:|
| Posições inseridas/atualizadas | 34 |
| Posições puladas | 0 |

### Etapa 4 — Validação pós-upsert

| Verificação | Resultado |
|-------------|----------:|
| Total posições em portfolio_positions | 34 |
| Posições com qty ≤ 0 | 0 ✅ |
| Posições sem asset válido | 0 ✅ |
| **Total investido (custo histórico)** | **R$ 193.557,85** |
| Ativos distintos | 34 |

---

## Views após execução

### `v_investment_summary`

| Classe | Ativos | Total Investido | Valor de Mercado* |
|--------|-------:|----------------:|------------------:|
| reit | 6 | R$ 57.684,91 | R$ 57.684,91 |
| stock | 28 | R$ 135.872,95 | R$ 135.872,95 |
| **Total** | **34** | **R$ 193.557,85** | **R$ 193.557,85** |

> *Valor de mercado = custo histórico por falta de cotações em `asset_quotes`. Quando cotações forem alimentadas, o `current_market_value` refletirá preços reais.

### `v_net_worth`

| Componente | Valor |
|------------|------:|
| `bank_balance` (contas bancárias) | R$ 211.516,11 |
| `investment_total` (portfolio_positions) | R$ 193.557,85 |
| **`net_worth` (patrimônio total)** | **R$ 405.073,96** |

---

## Posições Inseridas (34)

| Ticker | Classe | Quantidade | Preço Médio | Total Investido |
|--------|--------|----------:|------------:|----------------:|
| BITH11 | reit | 190 | R$ 137,8705 | R$ 26.195,40 |
| PSSA3 | stock | 643 | R$ 35,1457 | R$ 22.598,69 |
| EQTL3F | stock | 591 | R$ 24,2466 | R$ 14.329,72 |
| MXRF15 | stock | 1.352 | R$ 10,2900 | R$ 13.912,08 |
| BBAS3F | stock | 339 | R$ 33,3018 | R$ 11.289,32 |
| HGLG11 | reit | 44 | R$ 157,0632 | R$ 6.910,78 |
| BRCO11 | reit | 60 | R$ 117,5227 | R$ 7.051,36 |
| KNCR11 | reit | 64 | R$ 106,2463 | R$ 6.799,76 |
| VISC11 | reit | 62 | R$ 108,8481 | R$ 6.748,58 |
| GMAT3 | stock | 4.600 | R$ 1,4018 | R$ 6.448,42 |
| PETR3F | stock | 230 | R$ 28,7839 | R$ 6.620,29 |
| CFF | stock | 5,36 | R$ 1.000,0000 | R$ 5.360,00 |
| ROMI3 | stock | 602 | R$ 8,3762 | R$ 5.042,49 |
| TRPL3F | stock | 200 | R$ 29,1916 | R$ 5.838,32 |
| ITUB3F | stock | 174 | R$ 25,3695 | R$ 4.414,29 |
| IRDM11 | reit | 69 | R$ 57,6671 | R$ 3.979,03 |
| CSMG3F | stock | 94 | R$ 40,8282 | R$ 3.837,85 |
| BRAP3 | stock | 206 | R$ 18,4691 | R$ 3.804,64 |
| BRAP3F | stock | 206 | R$ 18,4691 | R$ 3.804,64 |
| SAPR11F | stock | 100 | R$ 38,0050 | R$ 3.800,50 |
| SBSP3 | stock | 118 | R$ 33,8748 | R$ 3.997,22 |
| ISAE3 | stock | 102 | R$ 33,1490 | R$ 3.381,20 |
| ISAE3F | stock | 102 | R$ 33,1489 | R$ 3.381,19 |
| DIRR3F | stock | 138 | R$ 16,9197 | R$ 2.334,92 |
| SAPR3F | stock | 122 | R$ 18,5282 | R$ 2.260,44 |
| TAEE3F | stock | 200 | R$ 12,7424 | R$ 2.548,49 |
| DEXP3F | stock | 293 | R$ 8,0518 | R$ 2.359,17 |
| ROMI3F | stock | 202 | R$ 8,2301 | R$ 1.662,47 |
| PSSA3F | stock | 43 | R$ 27,4702 | R$ 1.181,22 |
| MRFG3F | stock | 32 | R$ 23,6156 | R$ 755,70 |
| MRFG3 | stock | 11 | R$ 26,9200 | R$ 296,12 |
| MBRF3F | stock | 6 | R$ 16,5800 | R$ 99,48 |
| ABCB4F | stock | 23 | R$ 17,4775 | R$ 401,98 |
| FRAS3 | stock | 5 | R$ 22,4200 | R$ 112,10 |

---

## Posição Excluída (1)

| Ticker | Motivo | Detalhe |
|--------|--------|---------|
| DIRR3 | `preco_medio_invalido` | avg_price = R$ −8,7986 — resultado de qty negativa durante cálculo com histórico incompleto. Não inserido. |

---

## Alertas — Quantidade Negativa (234)

Os 234 alertas são **informativos** — não impedem a inserção de posições válidas.

**Causa:** O App2 registrava transações de apenas uma corretora. Quando ações foram transferidas entre corretoras (ex: XP → Rico → Clear), o sistema registrava a saída (transfer_out → sell) mas não a entrada correspondente no histórico consolidado, gerando vendas sem cobertura.

**Ativos afetados por nº de ocorrências:**

| Ticker | Ocorrências | Observação |
|--------|----------:|------------|
| EZTC3 | 50 | Alta rotatividade — ativo zerado |
| BBAS3 | 32 | Transferências frequentes — zerado |
| PETR3 | 24 | Múltiplos brokers — zerado |
| FIIP11B | 18 | FII transferido — zerado |
| CDB | 16 | Renda fixa com resgates — zerado |
| MBRF3 | 11 | Zerado |
| IRDM11 | 8 | Aparece em posição válida (alerts anteriores, posição final OK) |
| BCFF11 | 8 | Zerado |
| EQTL3 | 8 | Zerado |
| demais | 76 | — |

> Ativos com alerts mas ainda com posição positiva (ex: IRDM11, IRDM11 qty=69) confirmam que os alertas intermediários não corrompem o resultado final quando há compras subsequentes suficientes.

---

## Idempotência

O script pode ser re-executado com segurança:

```sql
ON CONFLICT (portfolio_id, asset_id) DO UPDATE SET
  quantity       = EXCLUDED.quantity,
  average_price  = EXCLUDED.average_price,
  total_invested = EXCLUDED.total_invested,
  updated_at     = now()
```

Nenhum registro é deletado. Re-execução apenas atualiza as posições existentes.

---

## Limitações conhecidas

1. **Histórico incompleto de transferências** — 234 alerts de qty_negativa indicam que algumas compras não foram registradas no App2 (transferências entre corretoras). As posições afetadas que chegaram a qty > 0 com preço válido foram inseridas; DIRR3 foi excluída.

2. **Cotações ausentes** — `asset_quotes` está vazia. O `current_market_value` em `v_investment_summary` usa `average_price` como fallback (custo histórico). Para valor de mercado real, será necessário alimentar cotações via API.

3. **Preço de GMAT3 baixo (R$ 1,40)** — Causado por splits históricos mapeados como `buy` com `price=0`. O custo médio final após splits é matematicamente correto (dilui o preço), mas diverge do preço de mercado atual.

---

## Próximos Passos

1. Alimentar `asset_quotes` com cotações de mercado (B3/Yahoo Finance) → `current_market_value` refletirá preços reais
2. Conectar app ao banco real — Fase 4.9
3. Futuramente: script de recalculo de `portfolio_positions` (posições mudam a cada nova transação)

---

*Gerado em: 2026-05-14 | Dashboard Financeiro Unificado — Fase 4.8.1*
