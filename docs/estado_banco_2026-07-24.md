# Estado do banco de dados — 24/07/2026

Relatório gerado por `scripts/report_db_state.py` (read-only) nos dois bancos,
após as correções da auditoria percentual (PR #104, aplicadas no armazém local
em 24/07/2026).

## Resumo executivo

| | Armazém local | Supabase (vitrine) |
|---|---|---|
| Correções da auditoria | **aplicadas** | **pendentes** (1 comando, ver abaixo) |
| Constraints preventivas | 5/5 presentes | 0/5 (criadas pelo mesmo comando) |
| Closes nulos em preços | 0 | 1.509 |
| Dividendos inválidos / duplicados | 0 / 0 | 28 / 62 |
| `fiscal_year` inválido (EUA) | 0 | n/a (tabelas de demonstrações não existem na vitrine) |
| FIIs com preço sem segmento | 0 | 113 |
| FIIs com preço sem vacância | 200 (138 são papel/FoF — não se aplica; lacuna real 62) | 292 (157 papel/FoF; lacuna real 135) |
| Snapshot de score FII | corte 23/07 — 756 validated / 390 diligence | **corte 14/07 — 0 validated / 1.535 diligence (defasado)** |

## Armazém local (dfu_warehouse, Postgres 17)

| Schema | Tabelas |
|---|---:|
| market | 57 |
| market_us | 24 |
| public | 60 |

| Verificação | Resultado |
|---|---|
| Preços BR: linhas / tickers / última data | 136.714 / 1.086 / 2026-07-22 |
| Preços BR: closes nulos/≤0 | **0** ✅ |
| Dividendos: linhas / tickers / última ex-date | 39.110 / 798 / 2026-11-03 |
| Dividendos: amount ≤ 0 | **0** ✅ |
| Dividendos: duplicatas exatas | **0** ✅ |
| Métricas calculadas BR: linhas / tickers / atualização | 65.213 / 818 / 2026-07-23 |
| Métricas calculadas BR: valores nulos | **0** ✅ |
| Demonstrações BR (DRE): linhas / tickers / último ano | 28.401 / 423 / 2026 |
| Empresas: total / sem setor | 390 / 27 (¹) |
| FIIs (com preço): total / sem segmento / sem vacância | 430 / **0** ✅ / 200 (²) |
| Score FII: último corte / linhas / validated / diligence | 2026-07-23 / 1.146 / 756 / 390 |
| EUA snapshots: ativos / com score+confiança / geração | 2.830 / 100% / 2026-07-23 |
| EUA preços mensais: linhas / símbolos / última data | 609.347 / 2.817 / 2026-07-17 |
| EUA DRE / balanços / fluxo: `fiscal_year` fora de faixa | **0 / 0 / 0** ✅ |
| Constraints preventivas (5) | **todas presentes** ✅ |

(¹) No Supabase `market.companies` está 390/0 — a coluna `sector` local está
defasada em relação à vitrine; sem impacto: o scoring lê a taxonomia de
`public.setores` via `load_setores`, que também recebeu +3 raízes via CVM.
(²) Dos 200: 138 são FIIs de papel/FoF, onde vacância física não se aplica.
Lacuna real: 62 fundos (14 tijolo, 41 híbrido, 7 sem tipo) sem observação de
vacância em nenhuma fonte coletada.

## Supabase (vitrine, plano Free)

| Schema | Tabelas |
|---|---:|
| market | 40 |
| market_us | 1 (`company_snapshots`) |
| public | 60 |

| Verificação | Resultado |
|---|---|
| Preços BR: linhas / tickers / última data | 142.579 / 1.091 / **2026-07-24** (workflow diário ativo) |
| Preços BR: closes nulos/≤0 | ⚠ **1.509** |
| Dividendos: linhas / tickers / última ex-date | 56.943 / 791 / 2026-11-03 (³) |
| Dividendos: amount ≤ 0 | ⚠ **28** |
| Dividendos: duplicatas exatas | ⚠ **62** |
| Métricas calculadas BR: linhas / tickers / atualização | 65.424 / 864 / 2026-07-23 |
| Métricas calculadas BR: valores nulos | 0 ✅ |
| Demonstrações BR (DRE): linhas / tickers / último ano | 28.399 / 423 / 2026 |
| Empresas: total / sem setor | 390 / 0 ✅ |
| FIIs (com preço): total / sem segmento / sem vacância | 428 / ⚠ 113 / ⚠ 292 |
| Score FII: último corte / validated / diligence | ⚠ **2026-07-14 / 0 / 1.535 — defasado** |
| EUA snapshots: ativos / com score+confiança / geração | 2.830 / 100% / 2026-07-23 ✅ |
| Constraints preventivas (5) | ⚠ **nenhuma** |

(³) O Supabase tem ~17,8 mil linhas de dividendos a mais que o armazém local:
a tabela remota nunca passou pelos scrubs locais (ecos de classe, PR #55–#58) e
continua recebendo o workflow diário. As 62 duplicatas exatas serão removidas
pelo comando abaixo; uma divergência estrutural entre as duas bases permanece e
será eliminada quando a publicação da vitrine (truncate+load a partir do local)
cobrir `market.dividends`.

## Ações pendentes (executadas por você — conexão de escrita ao Supabase)

1. **Replicar as correções no Supabase** (limpeza com backup CSV + constraints
   + cadastro FII sincronizado do armazém local):

   ```
   set DATABASE_URL=<sua SUPABASE_DB_URL>
   python scripts/fix_warehouse_quality_2026_07.py --apply --vacancia-from-warehouse
   ```

   (dry-run sem `--apply` mostra antes o que será feito; no meu dry-run:
   1.509 preços + 28 + 62 dividendos.)

2. **Publicar o snapshot FII validado** (corte 23/07 com 756 `validated`):

   ```
   python scripts/publish_fii_selection_from_local.py
   ```

3. Opcional, recomendado: incluir `market.dividends` no fluxo de publicação
   local→Supabase para eliminar a divergência estrutural (³).
