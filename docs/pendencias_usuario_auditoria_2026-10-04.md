# Pendências do usuário — auditoria do app4 (04/10/2026)

As correções de código da auditoria estão todas na main (PRs #480 a #513).
O que sobra aqui não pode ser feito pelo Claude Code: grava no Supabase, que
o classificador de segurança bloqueia mesmo com autorização, ou depende de uma
decisão sua. Cada item diz de onde veio.

## 1. Comandos no Supabase, na ordem sugerida

Todos os scripts têm modo simulação. Rode sem `--apply`, confira o número e só
então grave.

### 1.1 Controle financeiro (dado pessoal)

```bash
# aportes migrados do App 3 com sinal de entrada (21 linhas, R$ 107.464,03) — #485
python scripts/corrige_sinal_aportes_importados.py
python scripts/corrige_sinal_aportes_importados.py --apply

# resgates importados como despesa — #490
python scripts/reclassifica_resgates_importados.py
python scripts/reclassifica_resgates_importados.py --apply
```

```sql
-- Tesouro em dobro na foto de 28/08: devolve a fonte certa às 6 linhas td-snap — #509
UPDATE portfolio_position_snapshots
   SET source_table = 'tesouro_direto'
 WHERE source_id LIKE 'td-snap-%' AND source_table = 'xp_consolidado';
```

A migration `supabase_unificado/schema/080_posicao_anterior_extrato.sql`
(posição anterior ao primeiro extrato da B3) é aplicada pelo próprio app na
primeira gravação. Rodar à mão no SQL Editor é opcional.

### 1.2 Dados de mercado

```sql
-- AXIA5 e AXIA6 sem company_id, fora do universo B3 — #497
-- confira antes o company_id da AXIA3 no Supabase (no armazém local é 3)
UPDATE market.assets
   SET company_id = (SELECT company_id FROM market.assets WHERE ticker = 'AXIA3')
 WHERE ticker IN ('AXIA5', 'AXIA6') AND company_id IS NULL;
```

```bash
# TOKY3 e CCTY3 sem setor — #497
python scripts/enrich_setores_cvm.py
python scripts/enrich_setores_cvm.py --apply

# ecos de outra classe nos proventos da brapi (dividend yield dobrado)
python scripts/fix_dividends_class_mix.py
python scripts/fix_dividends_class_mix.py --apply

# juro real ex-ante por Fisher em public.macro — #495 (o job update_bcb do CI também faz)
python scripts/seed_macro_bcb.py --apply

# cotações com o IFIX oficial e o XFIX11, sem esperar o job diário — #509
python run_data_updates.py --source update_b3_quotes --force

# vitrine de notícias com a curadoria nova — #506
python scripts/publish_noticias_vitrine.py
python scripts/publish_noticias_vitrine.py --apply
```

### 1.3 Vitrine de métricas B3 (depende da decisão 2.1)

O portão do #496 recusa publicar TTM mais velho que o da vitrine. Até o
armazém ser ressincronizado, o alvo `b3_metrics` da rotina vai falhar toda
semana com "publicação recusada". É intencional.

```bash
python run_market_ingest.py annual --warehouse --json   # grava só no armazém local
python run_market_ingest.py reprocess --warehouse
python scripts/publish_b3_metrics_to_supabase.py --limit 5   # bloqueio_atualidade deve vir null
python scripts/publish_b3_metrics_to_supabase.py --apply
```

```sql
-- opcional, se não quiser esperar o agendamento de 10/10 — #496
DELETE FROM public.data_freshness_status WHERE job_name = 'update_b3_fundamentals';
```

A última rodada de `annual --warehouse` (04/10) terminou com 750 empresas
certas e 431 erros. A fonte não está pronta para ser a única.

### 1.4 Espaço e egress do Supabase

O passo a passo completo, com o SQL, está em
[`docs/plano_saida_supabase_free.md`](plano_saida_supabase_free.md) §5.

1. Streamlit Cloud → **Reboot** (o deploy só recarrega o `app.py`).
2. Supabase → Usage → Egress. Anote hoje e compare amanhã. A meta é menos de
   130 MB/dia.
3. §5.2: leitura do `pg_stat_statements` (o agente não teve acesso).
4. §5.3: remover os 8 índices com `idx_scan = 0` (−16,8 MB), um
   `DROP INDEX CONCURRENTLY` por vez.
5. §5.4: `python -m scripts.compact_remote_brapi_raw` e depois com `--apply`,
   seguido de `VACUUM (FULL, ANALYZE)` por conexão direta.
6. §5.5: podar as safras antigas de `fii_score_snapshots`, só se o banco
   passar de 480 MB.
7. Antes de um `DROP TABLE market.brapi_raw_payloads`, confirme que nenhuma
   ingestão ainda grava ali (`data_pipeline/market/repository.py`) — #505.

### 1.5 Na máquina local

- Reinicie `scripts/servir_armazem_leitura.py`. O túnel só passa a aceitar
  `ordem=nota` (busca do RAG pela relevância) depois disso — #512.

## 2. Decisões suas

### 2.1 Fonte das demonstrações B3 (trava a vitrine de métricas)
Não existe alvo de rotina que leve as demonstrações ao armazém local. As
opções são: Actions grava no Supabase, como antes, ou brapi alimenta o
armazém e o armazém publica. É uma decisão de arquitetura, e o 1.3 espera por
ela — #496.

### 2.2 Validação PIT dos FIIs segue bloqueada
A rodada de 04/10 (validação 94, backtest 63, metodologia 6.10.0) terminou
`blocked`. A causa é a mesma registrada em 19/09 (commit 873c925): em 16
meses entre 2017-04 e 2024-01, o otimizador montou carteira, mas o portão de
transparência recusou publicar por falta de dimensão obrigatória no
look-through. A fração viável é de 86,1%, contra um piso de 95%.

- Excesso médio contra o IFIX oficial: −0,12 p.p./mês em 99 meses. O IC dos
  dois padrões cruza zero.
- Padrão da casa: −0,05 p.p./mês, IC [−0,29; +0,20], 69 meses.
- Exceção: −0,27 p.p./mês, IC [−0,77; +0,19], 30 meses.

As opções:

- (a) aceitar: a tela passa a mostrar a validação bloqueada. Até 04/10 isso
  não acontecia: o publicador abortava, e o Supabase seguia com o run 84
  "aprovado" (70 meses, +0,136 p.p./mês). Depois do PR do FII-N1, a
  próxima publicação leva o run 94 `blocked` para a tela;
- (b) dispensar a dimensão obrigatória nos meses anteriores à existência do
  dado;
- (c) começar a validação em 2024.

Mudar o portão para passar é cessão de rigor, por isso a decisão é sua.

### 2.3 Piso do IC do excesso do FII como bloqueio
Hoje é só aviso ("Aprovado · excesso não significativo", em âmbar). Como
bloqueio, reprovaria as validações 88 a 90 — #494.

### 2.4 Demais decisões
- **Âncora do preço médio de BBAS3:** o app calcula 25,46, contra 23,92 da
  outra fonte. A posição anterior ao extrato (Bens e Direitos do IR,
  migration 080) resolve na origem.
- **Primeiro elo da cadeia de LLM:** hoje é `nvidia/nemotron-3-super-120b-a12b:free`.
  Desde o #482, dado pessoal não vai para modelo `:free`.
- **Uma linha por empresa no ranking B3:** 91 empresas têm mais de uma classe.
  `public.setores` guarda uma classe por empresa de propósito — #497.
- **Teto por gestora:** a exposição vem do administrador (o BTG DTVM administra
  218 fundos de 82 gestoras). Um teto por gestora de verdade precisa do
  histórico do cadastro de gestoras — #494.
- **`max_drawdown_tolerance_pct`:** a política vigente não preenche o campo, e
  o alerta de drawdown diz isso em vez de comparar — #498.
- **`investment_policies`:** não há política ativa nesta conta. O
  Black-Litterman usa os tetos padrão (10% por ativo, 30% por setor) e relaxa o
  teto de setor — #499.
- **Retenção de `calculated_metric_vintages`** — #505.
- **Supabase Pro** (US$ 25/mês), Neon ou VPS, se o egress não cair —
  `docs/plano_saida_supabase_free.md` §6.
- **Funções sem chamador** marcadas `SEM CHAMADOR (LLM-A13)`: apagar ou
  manter — #511.

## 3. Achados novos, fora do escopo e não corrigidos

**Metodologia e rigor**
- **Look-ahead na aba B3 — corrigido na 2.31.0:** os pisos de valor de
  mercado e de volume de hoje só decidem a carteira atual; cada safra usa o
  volume e o tamanho da época, e o Pesos Iguais passa a ter quem encolheu.
  A Selic de ano sem dado repete o último valor, sem a média com o futuro.
  O spread do ROIC só entra na decisão de hoje, então não é look-ahead.
  **Sobra:** o piso de qualidade, o portão da LLM e a guarda de entrada
  leem hoje; e a regra de decaimento da carteira histórica difere da atual.
- **Rank-IC do score B3 deixou de ser significativo na 2.31.0:** com o
  universo honesto, a média caiu de 0,0765 para 0,0657 e o IC 95% foi de
  [0,020; 0,130] para [−0,016; 0,142] (n de pares 1190 → 1449). O Grau de
  Confiança passa a REPROVADO e o Black-Litterman dá confiança mínima à B3.
  Não é defeito do código: é o que a evidência sustenta.
- **Vantagem líquida da B3 (medida antes da 2.31.0):** some contra a carteira
  de peso igual (+2,00 para +0,09 p.p.). Contra a Selic, cai de +12,64 para
  +10,73 p.p.
- **OOS por perfil (2.31.0, universo da época):** Equilibrado caiu de +4,2
  para −2,5 p.p. por safra, IC [−9,7; +4,3], 7 de 9 safras negativas;
  Conservador de +1,8 para +0,2 p.p., IC [−8,0; +7,8]. O Pesos Iguais agora
  inclui quem encolheu, e a vantagem anterior era em parte esse viés. Amplo
  segue +10,1 p.p., IC [+1,4; +23,2], carregado por 2017; ele não tem piso,
  então a correção não o altera.
- **Golden set do portão da LLM:** tem só 5 casos.

**Dados**
- **Série diária da carteira:** só começa em 14/04.
- **`historical_prices`:** o local está velho.
- **Lacunas:** IVVB11 e IFIX têm buracos na série.
- **Parser da SEC:** não lê IFRS (emissores estrangeiros).
- **Linhagem da DRE:** 5.290 de 5.379 linhas têm payload coletado depois de
  `first_seen_at`.
- **COTAHIST:** o código BDI 02 (lote) e o caso CCRO3/MOTV3.
- **Dividendos:** faltam os de ETF americano.
- **Importador do Tesouro:** grava líquido, não bruto, e a `report_date` é
  ambígua.
- **BOVA11:** classificado como `reit`.

**Código**
- **Deduplicação:** são 4 guardas que divergem entre si.
- **Freshness:** a ordenação de `get_outdated_sources` e de `_metricas_snapshot`.
- **Dependência entre jobs:** o job de FII depende do job B3.
- **Contexto da LLM:** `contexto_mercado.itens_gerais` lê os 150 mais recentes
  por data, sem curadoria.
- **Rótulo de falha:** exceção que não é da LLM aparece como "falha do
  provedor".
- **Notícias:** marcação de `tipo_evento`.
- **Session state:** aviso de `pb3_thr_selic_hist`.
- **Piso de negociabilidade:** na nuvem, o aviso "Piso de negociabilidade não
  aplicado" aparece sempre, e é verdade.
- **Egress:** o `b3_db` lê a nuvem durante medições locais.
- **Validação:** `persist_validation_run` grava a cada clique (deliberado).
