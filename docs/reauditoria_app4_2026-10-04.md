# Reauditoria do App 4 — 04/10/2026 (noite)

Repetição da auditoria de `docs/auditoria_app4_2026-10-04.pdf` depois dos PRs #479–#519.
Mesmas seis frentes, mesma escala, mesma regra: só leitura (SELECT no Supabase e no
armazém), verificação por execução, e "NÃO VERIFICADO" quando não houve execução.
Base auditada: main em a086e99 (frentes) e 3071535/e5fd29d (CI e GLB-N1).

Não houve validação em navegador real nesta rodada (G8): as notas de interface vêm de
leitura de código e testes.

## 1. Notas

| Módulo | Antes | Agora | Nível | O que segura a nota |
|---|---|---|---|---|
| Empresas B3 + Portfólio B3 | 6,5 | **7,5** | Pleno | fundamentos defasados (decisão 2.1), pesos de setor duplicados (B3-04), proventos futuros (B3-10) |
| Seleção de FIIs | 6,5 | **6,5** | Pleno | publicação travada pela validação 94 (`blocked`); a tela segue com o selo antigo (FII-N1) |
| Empresas Americanas | 6,0 | **6,8** | Pleno | correção do CIK não publicada no Supabase (EUA-N1); duplicatas em `prices_monthly` pioraram |
| Investimentos | 5,0 | **7,0** | Pleno | USD sem câmbio vira R$ 1:1 sem aviso (INV-M1); cobertura do realizado errada (INV-N1) |
| Portfólio Global | 4,5 | **6,0** | Pleno | alocação BL sem validação fora da amostra; VaR com cauda curta |
| Infraestrutura, dados e testes | 6,0 | **7,0** | Pleno | CI vermelha por teste flaky (INF-N1); cron noturno ainda não confirmou o fix |
| Controle Financeiro / Dashboard / Config. | 5,5 | **6,0** | Pleno-júnior | saldo inflado até rodar os scripts de aportes e resgates |
| Camada LLM | 6,0 | **7,5** | Pleno | rótulos macro sem país (LLM-N1); curadoria de notícias não reverificada |

**Nota geral: 5,6 → 6,6 / 10 (Júnior+ → Pleno).** Mesma ponderação da auditoria anterior
(média simples 5,75 → 6,79, escalada pela razão 5,6/5,75).

Os três críticos da auditoria anterior estão fechados: **INF-C1** (pipeline de 10 noites;
preço B3 no Supabase de 24/09 → 02/10, ~98% de cobertura), **INV-C1** (lote × fracionário;
PETR3 PM 28,78 → 36,10 no dado real) e **EUA-A** (vínculo por CIK — no armazém; falta publicar).

## 2. Situação dos achados anteriores

Legenda: ✅ corrigido · 🟡 parcial · 🔴 aberto · 👤 depende do usuário · ❔ não verificado.

**B3** — B3-01 ✅ (EW 2024 +130,2% → −9,1%) · B3-02 🟡👤 (decisão 2.1) · B3-03 ✅ medição,
conclusão negativa (Rank-IC 0,066, IC [−0,016; 0,142]; Equilibrado −2,5 pp líquido) ·
B3-04 🔴 · B3-05 ✅ · B3-06 ✅ (`public.macro` 2010–2026 sem nulos) · B3-07 🟡 (6 buracos reais:
ENAT3, ENGI3/4, NATU3, PETZ3, TOKY3) · B3-08 ✅ · B3-09 ✅ · B3-10 🔴 (38 dividendos com
`ex_date` futura) · B3-11 ❔.

**FII** — FII-01 ✅ código / 🟡 prática (ver FII-N1) · FII-02 🟡👤 (2.2/2.3) · FII-03 ✅ exibição
(tetos seguem não aplicados) · FII-05 🔴 · FII-06 ❔ · FII-07 🟡 (resta `strategy_id` v6.8) ·
FII-08 ❔ · FII-09 ✅ · FII-10 ✅ código, sem efeito em produção · FII-11 ❔.

**Global** — GLB-01 ✅ com ressalvas (BL + benchmark, benchmark com pesos de hoje no passado) ·
GLB-02 🟡.

**EUA** — EUA-A ✅ armazém / 👤 publicação; dívida e preferenciais 🟡 (CHSCL, PRE-J, GLSPT
ainda elegíveis) · EUA-B ✅ (02/10: 67 → 2.652 de 2.652 closes) · EUA-C 🔴 (limite do dado) ·
EUA-D 🟡 · EUA-E 🔴 piorou (duplicatas 5.059 → 7.741 no armazém) · EUA-F 🟡 (só documentado) ·
EUA-G 🔴 · EUA-H/I/J ✅ (código) · EUA-K IC na tela ✅; `except Exception` em `us_read` 🔴 (34 → 37) ·
buscas por nome ✅.

**Investimentos** — INV-C1 ✅ (BBAS3 residual 👤, pendência 2.4) · INV-A1 ✅ · INV-A2 ✅ ·
INV-A3 ✅ · INV-A4 ✅ (`max_drawdown_tolerance_pct` lido, mas vazio na política) · INV-M1 🔴 ·
INV-M2 👤 · INV-M3 ❔ · INV-M4 🟡 · INV-M5 ❔ · TIR × CDI ❔ · card macro ✅.

**Infra** — INF-C1 ✅ (confirmação pelo cron em 05/10) · INF-A1 ✅ lint / CI ver INF-N1 ·
INF-A2 🟡👤 (453,7 MiB = 475,7 MB; egress só no painel) · INF-A3 🟡 · INF-A4 🔴👤 ·
INF-M1 ❔ · INF-M2 🔴 (6 arquivos) · INF-M3 🔴 piorou (BLE001 481 → 531) · INF-M4 🟡 ·
INF-M5 🔴 (11 arquivos > 2.000 linhas) · INF-M6 🔴 (README v0.5.10) · INF-M7 🟡 ·
INF-B1–B10 ❔ · CRI 🔴 (01/06) · chunks sem embedding 🔴 (462.818).

**Controle / Config.** — CF-B1 ✅ · CF-B2 ✅ · CF-B3 🟡👤 (21 aportes positivos, R$ 107.464) ·
CF-B4 👤 (84 resgates, R$ 199.388 como receita) · CF-M1 ✅ cálculo · CF-M2 ✅ código ·
CF-M3 🔴 (`/30`) · CF-M4 🟡 · CF-M5 🔴 (IMPOSTO → Transporte; CLAUDETE → Assinaturas) ·
CF-M6 ❔ · CF-M7 🔴 · CF-M8 🔴 (parcelamento projetado em dobro; dezembro vira mês 0) ·
CFG-C1–C4 🔴 (SHA-256 sem salt) · CF-Baixas 🟡.

**LLM** — LLM-A1 ✅ (FEDFUNDS 2026-09, CPI 2026-08) · LLM-A2/A7 ✅ (Selic meta 13,75, Fisher,
Focus) · LLM-A3 ✅ · LLM-A4–A6 🟡 · LLM-A8 ❔ · LLM-A9 ✅ · LLM-A10 ❔ · LLM-A11 ✅ ·
LLM-A12/A13 ❔.

## 3. Achados novos

| ID | Sev. | Achado | Correção |
|---|---|---|---|
| INF-N1 | ALTA | `tests/test_leitura_relatorios.py::test_numero_inventado_fica_com_ressalva` falha conforme `PYTHONHASHSEED` (reproduzido com 12); main vermelha no push do #518 | ordenar o pool de derivações em `core/llm_grounding.py`; fixar `PYTHONHASHSEED` na CI |
| FII-N1 | ALTA | Validação 94 (99 períodos, excesso líquido −0,116 p.p./mês) está `blocked`; o publicador aborta e o Supabase segue com o run 84 "aprovado". `pendencias_usuario` §2.2(a) diz o contrário | publicar o estado `blocked` em vez de abortar, ou decisão 2.2; corrigir o texto da pendência |
| EUA-N1 | ALTA | Correção de CIK/dívida só no armazém; a vitrine de 03/10 ainda tem DUKB/MCHPP/XELLL decision_grade e não tem MU, UBER, GOOG, DUK | `python scripts/publish_us_snapshot_from_local.py` (dry-run ok) ou aguardar a cadência de 2 dias |
| INV-N1 | MÉDIA | Cobertura do ganho realizado = vendido/(vendido+sem_custo), mas sem_custo ⊂ vendido: tela 70,2%, correto 57,5% | `(vendido − sem_custo) / vendido` |
| INV-N2 | MÉDIA | Card "SELIC" usa SGS 4189 (13,65) em vez da meta, SGS 432 (13,75) | trocar para 432 |
| INF-N2 | MÉDIA | `openpyxl` ausente em `requirements-pipeline.txt`; `update_tesouro_curva` falha no headless | adicionar `openpyxl==3.1.5` |
| INF-N3 | MÉDIA | workflow termina "success" com job interno falho | código ≠ 0 em job crítico |
| CF-N1 | MÉDIA | linha sem sinal nem D/C: "Pagamento de salário", "Estorno", "Reembolso", "Venda" saem como saída | termos de entrada vencem os de saída |
| LLM-N1 | MÉDIA | bloco macro com séries de outros países sem país no rótulo (dívida/GDP 81,86 e 115,77) e códigos BIS/ECB/OECD crus | `country_code` e descrição no rótulo |
| EUA-N2 | MÉDIA | `link-cik` fora da rotina: ticker novo volta a ficar sem vínculo | encadear no `us_snapshot` |
| EUA-N3 | MÉDIA | preferencial/warrant eleito representante (CHSCL, PRE-J, GLSPT) | excluir por tipo antes do giro |
| EUA-N5 | MÉDIA | `prices_monthly` cria linha nova a cada derivação em dia novo | `month_end` de calendário + deduplicar |
| N-B3-01 | MÉDIA | perfil "Equilibrado (recomendado)" não bate pesos iguais (7 de 9 safras negativas) | "padrão", ou condicionar a `ic_low > 0` |
| N-B3-02 | MÉDIA | safras com 2 ativos; 2017 domina a banda do portão (−61,7 pp num veto) | mínimo de ativos por safra |
| GLB-N1 | MÉDIA | texto fixo dizia B3 c = 37,5% quando o cálculo dá 1% | **corrigido no PR #519** |
| INV-N3 | BAIXA | timeouts REST+SOAP em série no render: > 60 s se o BCB cair | paralelizar ou cortar timeout |
| INV-N4 | BAIXA | risco cobre 71,6% do patrimônio (exclui os de menor vol.) — declarado na tela | — |
| FII-N2 | BAIXA | `strategy_id` v6.8 dentro de run 6.10.0 | derivar de `METHODOLOGY_VERSION` |
| EUA-N4 | BAIXA | ETFs classificados como `cik_sem_empresa` | tipo próprio |
| N-B3-03/04 | BAIXA | look-ahead residual de valor de mercado (declarado); `nao_avaliado` preso na sessão | — |
| INF-N4 | BAIXA | premissa do PDF sobre `income_statements` 2T26 era falsa (391 de 400, não 1) | — |

## 4. Atualidade dos dados (resumo do Apêndice A)

Em dia: preço diário B3 (02/10, ~98%), dividendos, vintages, curva do Tesouro, `public.macro`,
vitrine de FIIs (04/10), preço diário EUA (02/10, 100% com close), posições e transações.
Velhos ou parados: `fii_score_snapshots` e `fii_metrics_monthly` no Supabase (artefato local
em dia), `docs_corporativos` no Supabase (26/06), `noticias_vitrine` (03/10, vira VELHA em
~14 h), `cvm_filing_publications` (18/08), CRI (01/06), `memoria_mercado` (30/07),
`info_economica_mensal` (31/01), ingestão de demonstrações B3 parada desde 10/07 (decisão 2.1).
Supabase: 453,7 MiB (475,7 MB) de 500 MB.

## 5. Para o usuário

Além de `docs/pendencias_usuario_auditoria_2026-10-04.md`:

1. Publicar a vitrine dos EUA: `python scripts/publish_us_snapshot_from_local.py` e, depois,
   `python scripts/publish_us_prices_monthly.py --apply`.
2. Rodar `corrige_sinal_aportes_importados.py --apply` e o de resgates (CF-B3/B4): o saldo
   cai de R$ 465.718 para ~R$ 250.790 e a reserva de 19,8 para ~11,5 meses.
3. `publish_noticias_vitrine --apply` antes de a vitrine passar de 48 h.
4. Decidir 2.2 (FII): o texto atual da pendência §2.2(a) está errado — a tela **não** mostra o
   selo bloqueado, mostra o "aprovado" antigo.
5. Reboot do Streamlit Cloud depois dos merges em `views/`/`core/` (inclui #519).
6. Preencher `max_drawdown_tolerance_pct` na política de investimentos.
