# Proteção ao investidor na criação de portfólio de Empresas B3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a criação de portfólio de Empresas B3 pesar a sustentabilidade histórica da distribuição na nota e vetar apenas quando duas evidências históricas independentes convergem, sem nunca esvaziar um segmento.

**Architecture:** Um módulo novo e puro (`core/b3_renda_sustentavel.py`) deriva, do histórico anual, três colunas de sustentabilidade da distribuição e a fração de pares "PL em queda com lucro positivo". A trilha `shareholder` do score passa a ler essas colunas. O portão entra como uma quarta confirmação dentro de `check_holdings` — nunca como motor paralelo — e `b3_quality_floor` ganha uma guarda de viabilidade que, apenas para esse critério novo, admite o líder marcado quando nenhum candidato do segmento sobrevive.

**Tech Stack:** Python 3.12, pandas/numpy, SQLAlchemy (engine singleton via `core.database.get_engine`), Streamlit, pytest.

## Global Constraints

Valores copiados do spec `docs/superpowers/specs/2026-09-09-b3-protecao-investidor-design.md`. Todas as tarefas herdam esta seção.

- `JANELA_ANOS = 8` — janela de exercícios anuais.
- `MIN_ANOS = 3` — abaixo disso, `payout_sustentabilidade` é `NaN`.
- `PISO, OTIMO_LO, OTIMO_HI, TETO = 0.05, 0.25, 0.80, 1.30` — faixa de dois lados.
- Regra de incoerência da fonte: exercício com `DY > 0,5%` **e** `Payout ≤ 1%` é tratado como **ausência** (sai do cálculo), nunca como nota zero.
- Trilha `shareholder` passa a `[("dy_sustentavel", True), ("payout_sustentabilidade", True), ("DY", True)]`; `Payout` sai; peso da trilha permanece **0.12**.
- Portão = **A e B** (nunca "A ou B"): **A** = `payout_mediano_hist > 1,0` com ≥ 5 anos observados; **B** = PL em queda com lucro positivo em ≥ 50% dos pares consecutivos, com ≥ 5 pares.
- `PAYOUT_CRITICO = 1.50` sobre o payout do TTM permanece inalterado — a confirmação histórica é o que decide entre ATENÇÃO e CRÍTICO.
- `SCORE_VERSION` sobe de `"2.25.0"` para `"2.26.0"` em `core/b3_methodology.py`; `MODEL_SCHEMA_VERSION` permanece `3`.
- Ausência nunca pune: cai no `_NEUTRAL = 0.5` da trilha, nunca em zero.
- A guarda de viabilidade afrouxa **somente** o critério novo; os critérios pré-existentes de `check_holdings` mantêm o comportamento de vaga vazia.
- Nenhuma chamada nova de banco para a sustentabilidade: o `hist_batch` de `load_multiplos_historico_batch` já é carregado. O critério B é a única exceção, e sua consulta mora em `core/dossie_b3.py`, que já é dono dessa query (regra do CLAUDE.md: não duplicar conexão de banco).
- Interpretador de teste: `"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest` com `PYTHONPATH="$PWD"`. O `python` do PATH cai na venv do Hermes e relata falsamente "sem pytest".

---

## File Structure

**Criar:**
- `core/b3_renda_sustentavel.py` — módulo puro. Faixa de sustentabilidade anual, leitura da série de payout, fração histórica de PL em queda com lucro positivo, e os dois enriquecedores de cross-section. Sem Streamlit, sem banco.
- `tests/test_b3_renda_sustentavel.py` — testes 1 a 6 e 11 do spec.

**Modificar:**
- `core/dossie_b3.py` — novo `load_pl_lucro_anual_batch` (a única consulta nova, no módulo que já é dono dela) e a red flag de patrimônio passando a citar a fração histórica.
- `core/b3_company_score.py:38` — composição da trilha `shareholder`.
- `core/b3_methodology.py:3` — `SCORE_VERSION`.
- `core/b3_holdings_health.py` — quarta confirmação `persistencia_historica`.
- `core/b3_quality_floor.py` — repasse da flag e guarda de viabilidade em `apply_with_substitution`.
- `views/portfolio_b3.py` — enriquecimento sobre `df_mult_todos` (o quadro que a decisão lê), etiqueta do líder marcado e transparência do afrouxamento.
- `views/empresas_b3.py` — mesmo enriquecimento na tela de Empresas B3.
- `core/portfolio_db_analysis.py` — mesmo enriquecimento na Análise do Portfólio.
- `core/llm_context_b3.py` — sustentabilidade no contexto, com ausência declarada.
- `core/dossie_b3.py` (prompt) — regra contra tratar exercício isolado como padrão.
- `tests/test_b3_company_score.py`, `tests/test_b3_holdings_health.py`, `tests/test_b3_quality_floor.py` — extensões.

---

### Task 1: Faixa de sustentabilidade e leitura da série de payout

**Files:**
- Create: `core/b3_renda_sustentavel.py`
- Test: `tests/test_b3_renda_sustentavel.py`

**Interfaces:**
- Consumes: nada (primeira tarefa).
- Produces:
  - `JANELA_ANOS: int = 8`, `MIN_ANOS: int = 3`, `PISO/OTIMO_LO/OTIMO_HI/TETO: float`
  - `sustentabilidade_do_ano(payout: float | None) -> float`
  - `leitura_da_serie(df_hist: pd.DataFrame) -> dict` com chaves
    `payout_sustentabilidade: float | None`, `payout_mediano_hist: float | None`,
    `n_anos_payout: int`
  - `enrich_com_renda_sustentavel(df_mult: pd.DataFrame, hist_batch: dict[str, pd.DataFrame]) -> pd.DataFrame`
    acrescentando as colunas `payout_sustentabilidade`, `dy_sustentavel`,
    `payout_mediano_hist`, `n_anos_payout`.

- [ ] **Step 1: Escrever os testes que falham**

Crie `tests/test_b3_renda_sustentavel.py`:

```python
"""Sustentabilidade histórica da distribuição — spec 2026-09-09."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.b3_renda_sustentavel import (
    MIN_ANOS,
    enrich_com_renda_sustentavel,
    leitura_da_serie,
    sustentabilidade_do_ano,
)


def _serie(payouts, dys=None):
    """Histórico anual no formato de load_multiplos_historico_batch."""
    n = len(payouts)
    dys = dys if dys is not None else [0.05] * n
    return pd.DataFrame({
        "Ticker": ["XPTO3"] * n,
        "Data": pd.to_datetime([f"{2018 + i}-12-31" for i in range(n)]),
        "DY": dys,
        "Payout": payouts,
    })


def test_fronteiras_da_faixa():
    # Teste 1 do spec: os quatro pontos exatos da faixa de dois lados.
    assert sustentabilidade_do_ano(0.05) == 0.0
    assert sustentabilidade_do_ano(1.30) == 0.0
    assert sustentabilidade_do_ano(0.25) == 1.0
    assert sustentabilidade_do_ano(0.80) == 1.0


def test_episodio_isolado_nao_condena():
    # Teste 2: um ano de 300% em oito, os outros sete dentro da faixa.
    leitura = leitura_da_serie(_serie([0.50] * 7 + [3.00]))
    assert leitura["payout_sustentabilidade"] >= 0.85


def test_padrao_persistente_zera():
    # Teste 3: acima do TETO em todos os anos.
    leitura = leitura_da_serie(_serie([1.40, 1.55, 2.10, 1.80, 1.60]))
    assert leitura["payout_sustentabilidade"] == 0.0


def test_ano_incoerente_sai_como_ausencia():
    # Teste 4: DY > 0,5% com Payout <= 1% é contradição da fonte.
    leitura = leitura_da_serie(
        _serie([0.40, 0.005, 0.50, 0.60], dys=[0.05, 0.06, 0.05, 0.05])
    )
    assert leitura["n_anos_payout"] == 3
    assert leitura["payout_sustentabilidade"] == 1.0


def test_historico_curto_produz_nan():
    # Teste 5: menos de MIN_ANOS observados não vira nota.
    leitura = leitura_da_serie(_serie([0.40] * (MIN_ANOS - 1)))
    assert leitura["payout_sustentabilidade"] is None
    assert leitura["n_anos_payout"] == MIN_ANOS - 1


def test_dy_sustentavel_nunca_cai_no_dy_bruto():
    # Teste 6: sem sustentabilidade, dy_sustentavel é NaN — nunca o DY cru.
    df_mult = pd.DataFrame({"Ticker": ["XPTO3", "CURTA3"], "DY": [0.08, 0.09]})
    hist = {
        "XPTO3": _serie([0.40, 0.50, 0.60, 0.55]),
        "CURTA3": _serie([0.40, 0.50]),
    }
    out = enrich_com_renda_sustentavel(df_mult, hist)
    linha = out[out["Ticker"] == "CURTA3"].iloc[0]
    assert np.isnan(linha["payout_sustentabilidade"])
    assert np.isnan(linha["dy_sustentavel"])
    boa = out[out["Ticker"] == "XPTO3"].iloc[0]
    assert boa["dy_sustentavel"] == pytest.approx(0.08 * boa["payout_sustentabilidade"])


def test_enrich_preserva_quadro_sem_historico():
    df_mult = pd.DataFrame({"Ticker": ["XPTO3"], "DY": [0.08]})
    out = enrich_com_renda_sustentavel(df_mult, {})
    assert list(out["Ticker"]) == ["XPTO3"]
```

- [ ] **Step 2: Rodar os testes e confirmar a falha**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py -v
```

Esperado: FAIL com `ModuleNotFoundError: No module named 'core.b3_renda_sustentavel'`.

- [ ] **Step 3: Implementar o módulo**

Crie `core/b3_renda_sustentavel.py`:

```python
"""Sustentabilidade histórica da distribuição — a qualidade da política de
dividendos da empresa, medida na janela de exercícios anuais.

Definição única, no molde de `core/b3_slopes.py`: a tela de Empresas B3, a
Criação de Portfólio e a Análise do Portfólio precisam da MESMA leitura de
sustentabilidade, senão a mesma empresa recebe duas notas e nenhuma das duas é
auditável. O módulo é puro (pandas/numpy), sem Streamlit e sem banco.

O princípio que ele implementa: vale a qualidade histórica, não o período
isolado. Um payout de 318% no TTM com mediana de 63,5% em oito anos é um
exercício fora da curva, não uma política insustentável — 79% das empresas que
o diagnóstico antigo condenava pelo TTM têm payout mediano abaixo de 100%.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

JANELA_ANOS = 8
MIN_ANOS = 3

# Faixa de dois lados. Distribuir quase nada e distribuir muito acima do lucro
# são as duas formas de o dividendo não ser um dividendo sustentável — por isso
# a nota cai nas DUAS pontas, e não apenas acima do teto.
PISO, OTIMO_LO, OTIMO_HI, TETO = 0.05, 0.25, 0.80, 1.30

# Contradição interna da fonte: a empresa não pode distribuir (DY acima de
# 0,5%) e não distribuir (payout de até 1%) no mesmo exercício. O ano sai do
# cálculo como AUSÊNCIA — punir por dado incoerente é medir a fonte errada.
DY_MIN_COERENCIA = 0.005
PAYOUT_MAX_COERENCIA = 0.01

__all__ = [
    "JANELA_ANOS", "MIN_ANOS", "PISO", "OTIMO_LO", "OTIMO_HI", "TETO",
    "sustentabilidade_do_ano", "leitura_da_serie", "enrich_com_renda_sustentavel",
]


def sustentabilidade_do_ano(payout) -> float:
    """Nota [0,1] de sustentabilidade da distribuição de UM exercício."""
    try:
        p = float(payout)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(p):
        return 0.0
    if p <= PISO or p >= TETO:
        return 0.0
    if OTIMO_LO <= p <= OTIMO_HI:
        return 1.0
    if p < OTIMO_LO:
        return (p - PISO) / (OTIMO_LO - PISO)
    return (TETO - p) / (TETO - OTIMO_HI)


def _anos_observados(df_hist: pd.DataFrame) -> list[float]:
    """Payouts anuais coerentes da janela, do mais antigo para o mais recente."""
    if df_hist is None or df_hist.empty or "Payout" not in df_hist.columns:
        return []
    df = df_hist.copy()
    if "Data" in df.columns:
        df = df.sort_values("Data")
    payout = pd.to_numeric(df["Payout"], errors="coerce")
    dy = (pd.to_numeric(df["DY"], errors="coerce") if "DY" in df.columns
          else pd.Series(np.nan, index=df.index))
    incoerente = (dy > DY_MIN_COERENCIA) & (payout <= PAYOUT_MAX_COERENCIA)
    payout = payout[~incoerente].dropna()
    return [float(v) for v in payout.tolist()[-JANELA_ANOS:]]


def leitura_da_serie(df_hist: pd.DataFrame) -> dict:
    """Sustentabilidade média, payout mediano e nº de anos observados."""
    anos = _anos_observados(df_hist)
    n = len(anos)
    if n < MIN_ANOS:
        return {"payout_sustentabilidade": None, "payout_mediano_hist": None,
                "n_anos_payout": n}
    notas = [sustentabilidade_do_ano(p) for p in anos]
    return {
        "payout_sustentabilidade": float(np.mean(notas)),
        "payout_mediano_hist": float(np.median(anos)),
        "n_anos_payout": n,
    }


def enrich_com_renda_sustentavel(
    df_mult: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Acrescenta a sustentabilidade histórica ao cross-section de múltiplos."""
    if df_mult is None or df_mult.empty or not hist_batch:
        return df_mult
    dados: dict[str, dict] = {}
    for tk, df_h in hist_batch.items():
        leitura = leitura_da_serie(df_h)
        dados[str(tk)] = {
            "payout_sustentabilidade": leitura["payout_sustentabilidade"],
            "payout_mediano_hist": leitura["payout_mediano_hist"],
            "n_anos_payout": leitura["n_anos_payout"],
        }
    if not dados:
        return df_mult
    df_rs = pd.DataFrame.from_dict(dados, orient="index")
    df_rs.index.name = "Ticker"
    out = df_mult.merge(df_rs.reset_index(), on="Ticker", how="left")
    # dy_sustentavel é NaN quando a sustentabilidade é NaN: nunca cai no DY
    # bruto. Um fallback que só preenche lacuna nunca contradiz — e contradizer
    # o DY divulgado é justamente o objetivo desta métrica.
    dy = (pd.to_numeric(out["DY"], errors="coerce") if "DY" in out.columns
          else pd.Series(np.nan, index=out.index))
    out["dy_sustentavel"] = dy * pd.to_numeric(
        out["payout_sustentabilidade"], errors="coerce")
    return out
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py -v
```

Esperado: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add core/b3_renda_sustentavel.py tests/test_b3_renda_sustentavel.py
git commit -m "feat(b3): sustentabilidade histórica da distribuição como módulo puro"
```

---

### Task 2: Critério B — fração histórica de PL em queda com lucro positivo

**Files:**
- Modify: `core/b3_renda_sustentavel.py`
- Modify: `core/dossie_b3.py` (novo `load_pl_lucro_anual_batch`, após `_series_anuais`)
- Test: `tests/test_b3_renda_sustentavel.py` (acrescentar)

**Interfaces:**
- Consumes: `JANELA_ANOS` da Task 1.
- Produces:
  - `fracao_pl_em_queda_com_lucro(serie: list[dict]) -> tuple[float | None, int]`
    — recebe a série anual no formato de `_series_anuais` (chaves `ano`,
    `pl_mi`, `lucro_mi`) e devolve `(fração, n_pares)`; fração é `None` quando
    não há par avaliável.
  - `enrich_com_historico_patrimonial(df_mult: pd.DataFrame, series_batch: dict[str, list[dict]]) -> pd.DataFrame`
    acrescentando `pl_queda_com_lucro_frac` e `n_pares_pl`.
  - `core.dossie_b3.load_pl_lucro_anual_batch(tickers: tuple[str, ...], max_anos: int = 12) -> dict[str, list[dict]]`

**Nota de projeto (por que a consulta nova é inevitável):** `_MULT_COLS` de
`core/market_read.py` não carrega `equity` nem `net_income`, então o
`hist_batch` de `load_multiplos_historico_batch` não sustenta o critério B. A
query mora em `core/dossie_b3.py` porque esse módulo já é dono exatamente deste
join (`_series_anuais`) e já usa o engine singleton — criar uma segunda fonte
violaria "não duplicar conexão de banco" do CLAUDE.md.

- [ ] **Step 1: Escrever os testes que falham**

Acrescente ao final de `tests/test_b3_renda_sustentavel.py`:

```python
from core.b3_renda_sustentavel import (
    enrich_com_historico_patrimonial,
    fracao_pl_em_queda_com_lucro,
)


def _anual(pares):
    """pares: lista de (ano, pl_mi, lucro_mi)."""
    return [{"ano": a, "pl_mi": pl, "lucro_mi": lu} for a, pl, lu in pares]


def test_fracao_conta_apenas_pares_com_queda_e_lucro():
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, 90.0, 8.0),    # queda com lucro
        (2021, 95.0, 9.0),    # sobe
        (2022, 80.0, 5.0),    # queda com lucro
        (2023, 70.0, -2.0),   # queda com prejuízo — não conta
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 4
    assert frac == pytest.approx(0.5)


def test_ausencia_de_dado_nao_e_zero():
    # `or 0` do _checks antigo tratava faltante como zero e fabricava queda.
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, None, 8.0),
        (2021, 95.0, None),
        (2022, 90.0, 7.0),
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 1
    assert frac == pytest.approx(1.0)


def test_serie_sem_par_avaliavel():
    frac, n_pares = fracao_pl_em_queda_com_lucro(_anual([(2020, 100.0, 5.0)]))
    assert frac is None
    assert n_pares == 0


def test_enrich_patrimonial_acrescenta_colunas():
    df_mult = pd.DataFrame({"Ticker": ["XPTO3", "SEMDADO3"], "DY": [0.08, 0.02]})
    lote = {"XPTO3": _anual([(2019, 100.0, 10.0), (2020, 90.0, 8.0)])}
    out = enrich_com_historico_patrimonial(df_mult, lote)
    linha = out[out["Ticker"] == "XPTO3"].iloc[0]
    assert linha["pl_queda_com_lucro_frac"] == pytest.approx(1.0)
    assert linha["n_pares_pl"] == 1
    vazia = out[out["Ticker"] == "SEMDADO3"].iloc[0]
    assert np.isnan(vazia["pl_queda_com_lucro_frac"])
```

- [ ] **Step 2: Rodar e confirmar a falha**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py -v -k "fracao or ausencia or par_avaliavel or patrimonial"
```

Esperado: FAIL com `ImportError: cannot import name 'fracao_pl_em_queda_com_lucro'`.

- [ ] **Step 3: Implementar as duas funções puras**

Em `core/b3_renda_sustentavel.py`, acrescente ao `__all__` os nomes
`"fracao_pl_em_queda_com_lucro"` e `"enrich_com_historico_patrimonial"`, e ao
final do arquivo:

```python
def _num_ou_none(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def fracao_pl_em_queda_com_lucro(serie: list[dict]) -> tuple[float | None, int]:
    """Fração dos pares consecutivos com PL caindo e lucro positivo.

    Ausência de PL ou de lucro em qualquer ponta do par é AUSÊNCIA: o par sai
    da conta. `core/dossie_b3.py` usava `(x or 0)`, que transforma faltante em
    zero e fabrica queda onde não há observação.
    """
    linhas = sorted(serie or [], key=lambda r: r.get("ano") or 0)
    pares = 0
    positivos = 0
    for a, b in zip(linhas, linhas[1:]):
        pl_a, pl_b = _num_ou_none(a.get("pl_mi")), _num_ou_none(b.get("pl_mi"))
        lucro_b = _num_ou_none(b.get("lucro_mi"))
        if pl_a is None or pl_b is None or lucro_b is None:
            continue
        pares += 1
        if pl_b < pl_a and lucro_b > 0:
            positivos += 1
    if pares == 0:
        return None, 0
    return positivos / pares, pares


def enrich_com_historico_patrimonial(
    df_mult: pd.DataFrame,
    series_batch: dict[str, list[dict]],
) -> pd.DataFrame:
    """Acrescenta `pl_queda_com_lucro_frac` e `n_pares_pl` ao cross-section."""
    if df_mult is None or df_mult.empty or not series_batch:
        return df_mult
    dados: dict[str, dict] = {}
    for tk, serie in series_batch.items():
        frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
        dados[str(tk)] = {"pl_queda_com_lucro_frac": frac, "n_pares_pl": n_pares}
    if not dados:
        return df_mult
    df_pl = pd.DataFrame.from_dict(dados, orient="index")
    df_pl.index.name = "Ticker"
    return df_mult.merge(df_pl.reset_index(), on="Ticker", how="left")
```

- [ ] **Step 4: Rodar e confirmar que passam**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py -v
```

Esperado: PASS (11 testes).

- [ ] **Step 5: Implementar o carregador em lote**

Em `core/dossie_b3.py`, logo depois da função `_series_anuais`, acrescente:

```python
@st.cache_data(ttl=3600, show_spinner=False)
def load_pl_lucro_anual_batch(tickers: tuple[str, ...],
                              max_anos: int = 12) -> dict[str, list[dict]]:
    """PL e lucro anuais de vários tickers, para o critério histórico do piso.

    Vive aqui, e não em `core/market_read.py`, porque este módulo já é dono
    deste join (`_series_anuais`) e do engine singleton. `_MULT_COLS` da
    vitrine de múltiplos não carrega `equity` nem `net_income`, então o
    histórico de múltiplos não sustenta a leitura de patrimônio.
    """
    alvos = sorted({str(t).upper().replace(".SA", "") for t in (tickers or []) if t})
    if not alvos:
        return {}
    rows = _rows(
        """
        SELECT i.ticker, i.year, i.net_income, b.equity
        FROM market.income_statements i
        LEFT JOIN market.balance_sheets b
          ON b.ticker = i.ticker AND b.period = i.period AND b.year = i.year
        WHERE i.ticker = ANY(:tks) AND i.period = 'annual'
        ORDER BY i.ticker, i.year
        """,
        tks=alvos,
    )
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[str(r["ticker"])].append({
            "ano": int(r["year"]),
            "pl_mi": _mi(r["equity"]),
            "lucro_mi": _mi(r["net_income"]),
        })
    return {tk: serie[-max_anos:] for tk, serie in out.items()}
```

`defaultdict` e `st` já estão importados no topo do módulo — confirme antes de
rodar.

- [ ] **Step 6: Rodar a suíte dos módulos tocados**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py tests/test_avaliacao_portfolio_b3_ui.py -v
```

Esperado: PASS.

- [ ] **Step 7: Commit**

```bash
git add core/b3_renda_sustentavel.py core/dossie_b3.py tests/test_b3_renda_sustentavel.py
git commit -m "feat(b3): fração histórica de PL em queda com lucro e carregador em lote"
```

---

### Task 3: Trilha `shareholder` e versão do score

**Files:**
- Modify: `core/b3_company_score.py:38`
- Modify: `core/b3_methodology.py:3`
- Test: `tests/test_b3_company_score.py`

**Interfaces:**
- Consumes: as colunas `dy_sustentavel` e `payout_sustentabilidade` produzidas
  pela Task 1.
- Produces: `FACTOR_TRACKS["shareholder"]` com três métricas; `SCORE_VERSION ==
  "2.26.0"`.

**Contexto para quem implementa:** `score_cross_section` monta `all_metrics` a
partir de `FACTOR_TRACKS` e `_numeric_metric` devolve uma coluna toda-NaN
quando o nome não existe no quadro — logo, um quadro sem enriquecimento não
quebra: a trilha cai no `_NEUTRAL` e é encolhida por cobertura. Isso é
exatamente o comportamento exigido pelo spec para ausência.

- [ ] **Step 1: Escrever os testes que falham**

Acrescente a `tests/test_b3_company_score.py`:

```python
def test_trilha_shareholder_le_sustentabilidade():
    from core.b3_company_score import FACTOR_TRACKS
    metricas = [m for m, _ in FACTOR_TRACKS["shareholder"]]
    assert metricas == ["dy_sustentavel", "payout_sustentabilidade", "DY"]
    assert "Payout" not in metricas


def test_peso_da_trilha_shareholder_inalterado():
    from core.b3_company_score import DEFAULT_TRACK_WEIGHTS
    assert DEFAULT_TRACK_WEIGHTS["shareholder"] == 0.12


def test_ausencia_de_sustentabilidade_vai_para_o_neutro():
    # Teste 5 do spec: sem histórico, a trilha fica no neutro — nunca em zero.
    import pandas as pd
    from core.b3_company_score import score_cross_section

    df = pd.DataFrame({
        "Ticker": ["COMHIST3", "SEMHIST3"],
        "DY": [0.08, 0.08],
        "dy_sustentavel": [0.07, float("nan")],
        "payout_sustentabilidade": [0.9, float("nan")],
        "ROIC": [0.15, 0.15],
    })
    scored = score_cross_section(df)
    sem = scored[scored["Ticker"] == "SEMHIST3"].iloc[0]
    assert sem["score_shareholder"] > 0.0


def test_score_version_2_26_0():
    # Teste 12 do spec: a composição da trilha mudou, a versão acompanha.
    from core.b3_methodology import MODEL_SCHEMA_VERSION, SCORE_VERSION
    assert SCORE_VERSION == "2.26.0"
    assert MODEL_SCHEMA_VERSION == 3
```

- [ ] **Step 2: Rodar e confirmar a falha**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_company_score.py -v -k "shareholder or score_version or neutro"
```

Esperado: FAIL — a trilha ainda é `[("DY", True), ("Payout", True)]` e a versão
ainda é `"2.25.0"`.

- [ ] **Step 3: Trocar a composição da trilha**

Em `core/b3_company_score.py`, substitua a linha 38:

```python
    "shareholder": [("DY", True), ("Payout", True)],
```

por:

```python
    # `Payout` saiu: monotônico e crescente, ele PREMIAVA distribuir acima do
    # lucro — um payout de 318% recebia rank 1,0. A métrica que deveria medir
    # prudência media generosidade. Entram as leituras históricas de
    # core/b3_renda_sustentavel.py: `dy_sustentavel` mede o NÍVEL da renda
    # sustentável e `payout_sustentabilidade` mede a QUALIDADE da política.
    # `DY` permanece porque três formulações sem ele foram medidas e todas
    # premiavam quem quase não distribui (payout de 1,7% tirava rank alto por
    # não ter de onde cair). A dupla contagem da sustentabilidade é assumida.
    "shareholder": [("dy_sustentavel", True),
                    ("payout_sustentabilidade", True),
                    ("DY", True)],
```

- [ ] **Step 4: Subir a versão**

Em `core/b3_methodology.py`, linha 3:

```python
SCORE_VERSION = "2.26.0"
```

`MODEL_SCHEMA_VERSION` fica em `3` — o schema de saída não mudou.

- [ ] **Step 5: Rodar os testes e confirmar que passam**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_company_score.py -v
```

Esperado: PASS. Se algum teste pré-existente afirmar `"2.25.0"` ou a
composição antiga da trilha, atualize-o — a mudança de versão é deliberada e
consta das Global Constraints.

- [ ] **Step 6: Commit**

```bash
git add core/b3_company_score.py core/b3_methodology.py tests/test_b3_company_score.py
git commit -m "feat(b3): trilha shareholder passa a ler sustentabilidade histórica (score 2.26.0)"
```

---

### Task 4: A quarta confirmação em `check_holdings`

**Files:**
- Modify: `core/b3_holdings_health.py` (constantes no topo; `check_holdings`)
- Test: `tests/test_b3_holdings_health.py`

**Interfaces:**
- Consumes: colunas `payout_mediano_hist`, `n_anos_payout` (Task 1) e
  `pl_queda_com_lucro_frac`, `n_pares_pl` (Task 2) no `df_mult`.
- Produces:
  - `PAYOUT_MEDIANO_CRITICO = 1.00`, `MIN_ANOS_PAYOUT_HIST = 5`,
    `FRACAO_PL_QUEDA_CRITICA = 0.50`, `MIN_PARES_PL_HIST = 5`
  - `check_holdings(..., persistencia_historica: bool = True)`

- [ ] **Step 1: Escrever os testes que falham**

Acrescente a `tests/test_b3_holdings_health.py`:

```python
def _linha_payout_alto(**extras):
    """Empresa com payout TTM acima do crítico e caixa apertado."""
    base = {
        "Ticker": "XPTO3", "Payout": 1.80, "DY": 0.12,
        "Endividamento_Total": 0.5, "Margem_Operacional": 0.20,
        "FCO_Negativo": 0.0,
    }
    base.update(extras)
    return pd.DataFrame([base])


def test_veto_exige_a_e_b():
    # Teste 8 do spec: só A (payout mediano alto, patrimônio estável) não veta.
    from core.b3_holdings_health import CRITICO, check_holdings
    df = _linha_payout_alto(payout_mediano_hist=1.95, n_anos_payout=7,
                            pl_queda_com_lucro_frac=0.10, n_pares_pl=6)
    (h,) = check_holdings(df, ["XPTO3"])
    assert h.nivel != CRITICO


def test_veto_quando_a_e_b_convergem():
    from core.b3_holdings_health import CRITICO, check_holdings
    df = _linha_payout_alto(payout_mediano_hist=1.61, n_anos_payout=6,
                            pl_queda_com_lucro_frac=0.67, n_pares_pl=6)
    (h,) = check_holdings(df, ["XPTO3"])
    assert h.nivel == CRITICO
    assert any("persistente" in a for a in h.alertas)


def test_historico_curto_nao_confirma():
    from core.b3_holdings_health import CRITICO, check_holdings
    df = _linha_payout_alto(payout_mediano_hist=1.61, n_anos_payout=4,
                            pl_queda_com_lucro_frac=0.90, n_pares_pl=4)
    (h,) = check_holdings(df, ["XPTO3"])
    assert h.nivel != CRITICO


def test_flag_desligada_ignora_a_confirmacao_historica():
    from core.b3_holdings_health import CRITICO, check_holdings
    df = _linha_payout_alto(payout_mediano_hist=1.61, n_anos_payout=6,
                            pl_queda_com_lucro_frac=0.67, n_pares_pl=6)
    (h,) = check_holdings(df, ["XPTO3"], persistencia_historica=False)
    assert h.nivel != CRITICO
```

Se `pd` ainda não estiver importado no arquivo de teste, acrescente
`import pandas as pd` no topo.

- [ ] **Step 2: Rodar e confirmar a falha**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_holdings_health.py -v -k "a_e_b or historico_curto or flag_desligada"
```

Esperado: FAIL — `check_holdings` ainda não aceita `persistencia_historica`.

- [ ] **Step 3: Acrescentar as constantes**

Em `core/b3_holdings_health.py`, junto de `PAYOUT_ATENCAO`/`PAYOUT_CRITICO`
(linhas 47-48):

```python
# Confirmação HISTÓRICA da insustentabilidade, por convergência de duas
# evidências independentes. Isoladas, A atinge 18 empresas e B atinge 9;
# juntas, 3 tickers. A variante "A ou B" foi medida e rejeitada: 24 vetadas,
# com Utilidades Domésticas perdendo 40% do segmento sem ganho de convicção.
PAYOUT_MEDIANO_CRITICO = 1.00   # A: payout mediano da janela acima do lucro
MIN_ANOS_PAYOUT_HIST = 5        # A: anos observados mínimos
FRACAO_PL_QUEDA_CRITICA = 0.50  # B: metade dos pares com PL caindo e lucro
MIN_PARES_PL_HIST = 5           # B: pares consecutivos mínimos
```

- [ ] **Step 4: Aceitar a flag e computar a confirmação**

Troque a assinatura de `check_holdings` (linha 136) por:

```python
def check_holdings(df_mult: pd.DataFrame, tickers: list[str], *,
                   policy: ValuePolicy | None = None,
                   selic: float | None = None,
                   persistencia_historica: bool = True) -> list[HoldingHealth]:
```

e acrescente ao docstring, na lista de Args:

```
        persistencia_historica: quando False, a quarta confirmação (payout
            mediano alto E patrimônio em queda persistente) não é considerada.
            Usado apenas pela guarda de viabilidade do piso de qualidade.
```

Dentro do bloco de confirmações (logo depois de
`confirmacoes.append("margem operacional negativa")`), acrescente:

```python
            # Quarta confirmação: PERSISTÊNCIA histórica. As três acima leem a
            # foto (TTM); esta lê a janela de exercícios. É a peça que separa a
            # Unipar — 318% no TTM, mediana de 63,5% em oito anos — de uma
            # política de distribuição que de fato não se sustenta.
            payout_med = _num(linha_base.get("payout_mediano_hist"))
            n_anos_pay = _num(linha_base.get("n_anos_payout"))
            frac_pl = _num(linha_base.get("pl_queda_com_lucro_frac"))
            n_pares = _num(linha_base.get("n_pares_pl"))
            crit_a = (payout_med == payout_med and n_anos_pay == n_anos_pay
                      and payout_med > PAYOUT_MEDIANO_CRITICO
                      and n_anos_pay >= MIN_ANOS_PAYOUT_HIST)
            crit_b = (frac_pl == frac_pl and n_pares == n_pares
                      and frac_pl >= FRACAO_PL_QUEDA_CRITICA
                      and n_pares >= MIN_PARES_PL_HIST)
            if persistencia_historica and crit_a and crit_b:
                confirmacoes.append(
                    f"padrão persistente: payout mediano de {payout_med:.0%} em "
                    f"{int(n_anos_pay)} anos com patrimônio caindo em "
                    f"{frac_pl:.0%} dos {int(n_pares)} pares")
```

O `PAYOUT_CRITICO = 1.50` sobre o TTM não muda: o veto efetivo é TTM ≥ 150%
**e** A **e** B.

- [ ] **Step 5: Rodar os testes e confirmar que passam**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_holdings_health.py -v
```

Esperado: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/b3_holdings_health.py tests/test_b3_holdings_health.py
git commit -m "feat(b3): persistência histórica como quarta confirmação do payout crítico"
```

---

### Task 5: A guarda de viabilidade no piso de qualidade

**Files:**
- Modify: `core/b3_quality_floor.py` (`evaluate`, `apply_with_substitution`)
- Test: `tests/test_b3_quality_floor.py`

**Interfaces:**
- Consumes: `check_holdings(..., persistencia_historica=...)` da Task 4.
- Produces:
  - `evaluate(..., persistencia_historica: bool = True)`
  - chave nova de log `afrouxado_por_viabilidade`, com itens
    `{"tk": str, "segmento": str, "motivo": str}`

**Por que o afrouxamento se confina sozinho ao critério novo:** ao reavaliar com
`persistencia_historica=False`, um líder que também reprova por FCO negativo,
endividamento ou margem operacional continua REPROVADO — a vaga segue vazia,
sem precisar de nenhuma lista de exceções.

- [ ] **Step 1: Escrever os testes que falham**

Acrescente a `tests/test_b3_quality_floor.py`:

```python
def _df_segmento_todo_persistente():
    """Líder e substitutos, todos reprovados SÓ pela persistência histórica."""
    linhas = []
    for tk in ("LIDER3", "SEG2", "SEG3"):
        linhas.append({
            "Ticker": tk, "Payout": 1.80, "DY": 0.12,
            "Endividamento_Total": 0.5, "Margem_Operacional": 0.20,
            "FCO_Negativo": 0.0,
            "payout_mediano_hist": 1.61, "n_anos_payout": 6,
            "pl_queda_com_lucro_frac": 0.67, "n_pares_pl": 6,
        })
    return pd.DataFrame(linhas)


def test_lider_entra_marcado_quando_segmento_inteiro_reprova():
    # Teste 9 do spec: a vaga não fica vazia; o líder entra com o motivo no log.
    from core.b3_quality_floor import apply_with_substitution
    df = _df_segmento_todo_persistente()
    log = {}
    finais = apply_with_substitution(
        ["LIDER3"], [("SEG2", 1.0), ("SEG3", 0.9)], df,
        seg_label="Utilidades › Domésticas", log=log,
    )
    assert finais == ["LIDER3"]
    assert not log["sem_substituto"]
    assert log["afrouxado_por_viabilidade"][0]["tk"] == "LIDER3"


def test_guarda_nao_afrouxa_criterios_preexistentes():
    # Teste 10 do spec: FCO negativo continua deixando a vaga vazia.
    from core.b3_quality_floor import apply_with_substitution
    df = _df_segmento_todo_persistente()
    df["FCO_Negativo"] = 1.0
    log = {}
    finais = apply_with_substitution(
        ["LIDER3"], [("SEG2", 1.0), ("SEG3", 0.9)], df,
        seg_label="Utilidades › Domésticas", log=log,
    )
    assert finais == []
    assert log["sem_substituto"][0]["tk"] == "LIDER3"
    assert not log["afrouxado_por_viabilidade"]
```

Se `pd` ainda não estiver importado no arquivo de teste, acrescente
`import pandas as pd` no topo.

- [ ] **Step 2: Rodar e confirmar a falha**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_quality_floor.py -v -k "marcado or preexistentes"
```

Esperado: FAIL — `log["afrouxado_por_viabilidade"]` não existe.

- [ ] **Step 3: Repassar a flag em `evaluate`**

Em `core/b3_quality_floor.py`, troque a assinatura de `evaluate` por:

```python
def evaluate(df_mult: pd.DataFrame, tickers: list[str], *,
             policy: FloorPolicy | None = None,
             value_policy: ValuePolicy | None = None,
             selic: float | None = None,
             persistencia_historica: bool = True) -> dict[str, FloorVerdict]:
```

e a chamada interna por:

```python
    saude = check_holdings(df_mult, list(tickers or []),
                           policy=value_policy, selic=selic,
                           persistencia_historica=persistencia_historica)
```

- [ ] **Step 4: Implementar a guarda**

Em `apply_with_substitution`, acrescente a chave de log junto das outras:

```python
    log.setdefault("afrouxado_por_viabilidade", [])
```

e substitua o bloco final

```python
        else:
            log["sem_substituto"].append({"tk": tk, "segmento": seg_label})
```

por:

```python
        else:
            # GUARDA DE VIABILIDADE. Se ninguém do segmento sobrevive, reavalia
            # o líder SEM a confirmação de persistência histórica. Passando, ele
            # entra marcado — a carteira nunca encolhe por causa do critério
            # novo. Reprovando de novo, é porque algum critério PRÉ-EXISTENTE
            # (FCO negativo, endividamento, margem) o condena, e aí a vaga
            # continua vazia: o afrouxamento se confina sozinho, sem lista de
            # exceções. Mesma razão declarada em core/us_quality_floor.py —
            # reprovar todo mundo esvaziaria a carteira, não a tornaria mais
            # seletiva.
            sem_persistencia = evaluate(
                df_mult, [tk], policy=policy, value_policy=value_policy,
                selic=selic, persistencia_historica=False,
            ).get(tk)
            if sem_persistencia is not None and sem_persistencia.situacao != REPROVADO:
                finais.append(tk)
                motivo = ("nenhum candidato do segmento sobreviveu à confirmação "
                          "histórica de payout — critério rebaixado de CRÍTICO "
                          "para ATENÇÃO neste segmento")
                log["afrouxado_por_viabilidade"].append(
                    {"tk": tk, "segmento": seg_label, "motivo": motivo})
            else:
                log["sem_substituto"].append({"tk": tk, "segmento": seg_label})
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_quality_floor.py -v
```

Esperado: PASS, incluindo o teste pré-existente que afirma que o piso não
define critérios próprios além de `check_holdings`.

- [ ] **Step 6: Commit**

```bash
git add core/b3_quality_floor.py tests/test_b3_quality_floor.py
git commit -m "feat(b3): guarda de viabilidade admite o líder marcado quando o segmento inteiro reprova"
```

---

### Task 6: Ligar o enriquecimento aos três consumidores

**Files:**
- Modify: `views/portfolio_b3.py:2864` (onde `hist_batch` é montado)
- Modify: `views/empresas_b3.py` (perto das linhas 33, 819, 3353 e 4597)
- Modify: `core/portfolio_db_analysis.py` (perto das linhas 66 e 92)
- Test: `tests/test_b3_renda_sustentavel.py`

**Interfaces:**
- Consumes: `enrich_com_renda_sustentavel` (Task 1),
  `enrich_com_historico_patrimonial` e `load_pl_lucro_anual_batch` (Task 2).
- Produces: `df_mult_todos` na Criação de Portfólio carregando as seis colunas
  novas.

**A armadilha desta tarefa:** em `views/portfolio_b3.py`, o piso
(`_aplicar_piso_qualidade`, linha 3397), a Saúde da Carteira e a Rota de Valor
leem **`df_mult_todos`**, não `df_mult_recon`. Enriquecer o quadro errado
produz exatamente o defeito já catalogado neste projeto — o portão lendo uma
fonte e a tela lendo outra, com dez pontos de diferença e nenhum erro visível.
As colunas TÊM que chegar a `df_mult_todos`.

- [ ] **Step 1: Escrever o teste de derivação única**

Acrescente a `tests/test_b3_renda_sustentavel.py`:

```python
def test_mesma_derivacao_para_as_mesmas_linhas():
    # Teste 7 do spec: tela e Análise do Portfólio derivam o MESMO valor.
    # Regra certa num consumidor só já publicou número errado neste projeto.
    hist = {
        "XPTO3": _serie([0.30, 0.45, 0.60, 0.75, 0.90]),
        "OUTRO4": _serie([1.40, 1.50, 1.60, 1.70]),
    }
    df_tela = pd.DataFrame({"Ticker": ["XPTO3", "OUTRO4"], "DY": [0.08, 0.11]})
    df_analise = pd.DataFrame({"Ticker": ["OUTRO4", "XPTO3"], "DY": [0.11, 0.08]})
    a = enrich_com_renda_sustentavel(df_tela, hist).set_index("Ticker")
    b = enrich_com_renda_sustentavel(df_analise, hist).set_index("Ticker")
    for tk in ("XPTO3", "OUTRO4"):
        assert a.loc[tk, "payout_sustentabilidade"] == pytest.approx(
            b.loc[tk, "payout_sustentabilidade"])
        assert a.loc[tk, "dy_sustentavel"] == pytest.approx(
            b.loc[tk, "dy_sustentavel"])
```

- [ ] **Step 2: Rodar e confirmar que passa**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py::test_mesma_derivacao_para_as_mesmas_linhas -v
```

Esperado: PASS — a função é única por construção; o teste é o guarda contra
alguém reimplementar a regra num segundo consumidor.

- [ ] **Step 3: Ligar na Criação de Portfólio**

Em `views/portfolio_b3.py`, logo depois do bloco que monta `hist_batch`
(linha 2864, após `hist_batch = hist_clean`), acrescente:

```python
        # Sustentabilidade histórica da distribuição sobre df_mult_todos — que
        # é o quadro que o piso de qualidade, a Saúde da Carteira e a Rota de
        # Valor leem. Enriquecer df_mult_recon deixaria o portão cego às
        # colunas novas sem produzir erro visível.
        from core.b3_renda_sustentavel import (
            enrich_com_historico_patrimonial,
            enrich_com_renda_sustentavel,
        )
        from core.dossie_b3 import load_pl_lucro_anual_batch

        df_mult_todos = enrich_com_renda_sustentavel(df_mult_todos, hist_batch)
        df_mult_todos = enrich_com_historico_patrimonial(
            df_mult_todos, load_pl_lucro_anual_batch(tuple(all_tickers)))
```

- [ ] **Step 4: Ligar na tela de Empresas B3**

Em `views/empresas_b3.py`, acrescente ao import da linha 33:

```python
from core.b3_renda_sustentavel import enrich_com_renda_sustentavel
```

e, logo depois de cada chamada a `_enrich_com_slopes` (linhas ~3353 e ~4597),
acrescente a chamada irmã sobre o mesmo quadro e o mesmo `hist_batch`:

```python
        pares = enrich_com_renda_sustentavel(pares, historicos)
```

```python
        df_mult_enrich = enrich_com_renda_sustentavel(df_mult_enrich, hist_batch)
```

- [ ] **Step 5: Ligar na Análise do Portfólio**

Em `core/portfolio_db_analysis.py`, acrescente ao import da linha 66:

```python
from core.b3_renda_sustentavel import enrich_com_renda_sustentavel
```

e, logo depois da linha 92 (`universo = enrich_com_slopes(universo, historicos)`):

```python
    universo = enrich_com_renda_sustentavel(universo, historicos)
```

- [ ] **Step 6: Rodar as suítes que tocam os três consumidores**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py tests/test_b3_company_score.py tests/test_empresas_b3_abas_ui.py tests/test_b3_portfolio_model.py -v
```

Esperado: PASS.

- [ ] **Step 7: Commit**

```bash
git add views/portfolio_b3.py views/empresas_b3.py core/portfolio_db_analysis.py tests/test_b3_renda_sustentavel.py
git commit -m "feat(b3): enriquecimento de sustentabilidade nos três consumidores do score"
```

---

### Task 7: Tela — DY sustentável e o líder marcado

**Files:**
- Modify: `views/portfolio_b3.py` (`motivos` em 3421-3436; `piso_log` em 3347; transparência do piso em 3865-3896)
- Test: `tests/test_portfolio_b3_market_cap_gate.py` não cobre isto; a verificação é visual (Streamlit) mais o teste de log da Task 5.

**Interfaces:**
- Consumes: `piso_log["afrouxado_por_viabilidade"]` (Task 5) e as colunas
  `payout_sustentabilidade`, `n_anos_payout`, `dy_sustentavel` em
  `df_mult_todos` (Task 6).
- Produces: nada consumido por tarefas posteriores.

- [ ] **Step 1: Semear a chave nova no log**

Em `views/portfolio_b3.py`, no ponto onde `piso_log` é criado (linha 3347),
troque

```python
    piso_log: dict = {"reprovados": [], "substituicoes": [], "sem_substituto": []}
```

por

```python
    piso_log: dict = {"reprovados": [], "substituicoes": [], "sem_substituto": [],
                "afrouxado_por_viabilidade": []}
```

- [ ] **Step 2: Etiquetar o líder na própria linha**

No bloco que monta `motivos` (logo depois do trecho
`motivos.append(f"Entrou por piso de qualidade sobre {_sub_piso}")`),
acrescente:

```python
                _afrouxado = next(
                    (a for a in piso_log["afrouxado_por_viabilidade"]
                     if a["tk"] == tk), None)
                if _afrouxado:
                    motivos.append(
                        "⚠️ Entrou com ressalva: distribuição historicamente "
                        "acima do lucro; nenhum candidato do segmento passou")
```

E, ainda dentro do mesmo laço, acrescente a leitura da sustentabilidade — a
coluna que decide é a coluna que aparece:

```python
                _lin_rs = df_mult_todos[df_mult_todos["Ticker"] == tk]
                if not _lin_rs.empty and "payout_sustentabilidade" in _lin_rs.columns:
                    _sust = _lin_rs["payout_sustentabilidade"].iloc[0]
                    _dy_s = _lin_rs.get("dy_sustentavel", pd.Series([float("nan")])).iloc[0]
                    _n_anos = _lin_rs.get("n_anos_payout", pd.Series([0])).iloc[0]
                    if _sust == _sust:
                        motivos.append(
                            f"DY sustentável {float(_dy_s):.1%} "
                            f"(divulgado {float(_lin_rs['DY'].iloc[0]):.1%} × "
                            f"sustentabilidade {float(_sust):.0%} em "
                            f"{int(_n_anos)} anos)")
                    else:
                        motivos.append(
                            "DY sustentável indisponível — menos de 3 anos de "
                            "payout observados")
```

- [ ] **Step 3: Mostrar o afrouxamento na transparência do piso**

No bloco de transparência do piso, troque a condição de abertura

```python
    if _piso_ativo and (piso_log["reprovados"] or piso_log["sem_substituto"]):
```

por

```python
    if _piso_ativo and (piso_log["reprovados"] or piso_log["sem_substituto"]
                        or piso_log["afrouxado_por_viabilidade"]):
```

e, depois do laço `for vazio in piso_log["sem_substituto"]`, acrescente:

```python
        for _afr in piso_log["afrouxado_por_viabilidade"]:
            st.warning(
                f"Segmento **{_afr['segmento']}**: **{_afr['tk']}** entrou "
                f"MARCADO — {_afr['motivo']}. A carteira não perde o segmento, "
                "mas trate a distribuição desta empresa como não confirmada.",
                icon="⚠️")
```

- [ ] **Step 4: Verificar a tela**

Suba o preview da aplicação (`preview_start`), abra Criação de Portfólio B3,
rode a criação e confirme: (a) os cards de líder trazem a linha "DY
sustentável"; (b) empresa sem histórico traz a linha de indisponibilidade em
vez de omitir; (c) se houver afrouxamento, o aviso âmbar aparece na seção do
piso. Colete o screenshot como prova.

- [ ] **Step 5: Rodar as suítes de UI da tela**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_b3_diversificacao_correlacao.py tests/test_portfolio_b3_liquidez_adtv.py tests/test_b3_quality_floor.py -v
```

Esperado: PASS.

- [ ] **Step 6: Commit**

```bash
git add views/portfolio_b3.py
git commit -m "feat(b3): tela mostra DY sustentável e etiqueta o líder admitido por viabilidade"
```

---

### Task 8: Red flag histórica e contexto da LLM

**Files:**
- Modify: `core/dossie_b3.py` (`_checks`, ~linha 388; `_PROMPT_PARECER`, ~linha 506)
- Modify: `core/llm_context_b3.py` (`get_company_fundamentals_context`, ~linha 231)
- Test: `tests/test_b3_renda_sustentavel.py`

**Interfaces:**
- Consumes: `fracao_pl_em_queda_com_lucro` (Task 2) e as colunas
  `payout_sustentabilidade` / `payout_mediano_hist` / `n_anos_payout` (Task 1).
- Produces: nada consumido por tarefas posteriores.

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_b3_renda_sustentavel.py`:

```python
def test_red_flag_de_patrimonio_cita_a_fracao_historica():
    # Teste 11 do spec: 1 em 8 anos não recebe o mesmo texto que 5 em 8.
    from core.dossie_b3 import _checks

    def _serie_pl(pares):
        return [{"ano": a, "pl_mi": pl, "lucro_mi": lu,
                 "fco_mi": 1.0, "ebitda_mi": 1.0} for a, pl, lu in pares]

    raro = _serie_pl([(2018, 100.0, 9.0), (2019, 110.0, 9.0), (2020, 120.0, 9.0),
                      (2021, 130.0, 9.0), (2022, 140.0, 9.0), (2023, 150.0, 9.0),
                      (2024, 160.0, 9.0), (2025, 150.0, 9.0)])
    cronico = _serie_pl([(2018, 200.0, 9.0), (2019, 190.0, 9.0), (2020, 180.0, 9.0),
                         (2021, 170.0, 9.0), (2022, 175.0, 9.0), (2023, 165.0, 9.0),
                         (2024, 155.0, 9.0), (2025, 145.0, 9.0)])
    vazio = {"yoy": {}}
    f_raro = _checks(raro, vazio, {}, {}, {"n_docs": 1}, {})
    f_cronico = _checks(cronico, vazio, {}, {}, {"n_docs": 1}, {})
    assert not [f for f in f_raro if "PATRIMÔNIO EM QUEDA" in f]
    pat = [f for f in f_cronico if "PATRIMÔNIO EM QUEDA" in f]
    assert pat and "7" in pat[0]
```

- [ ] **Step 2: Rodar e confirmar a falha**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py::test_red_flag_de_patrimonio_cita_a_fracao_historica -v
```

Esperado: FAIL — a flag atual dispara pelo último par e não cita fração.

- [ ] **Step 3: Reescrever a red flag**

Em `core/dossie_b3.py`, substitua o bloco

```python
    if len(serie) >= 2:
        a, b = serie[-2], serie[-1]
        if (b.get("pl_mi") or 0) < (a.get("pl_mi") or 0) and (b.get("lucro_mi") or 0) > 0:
            flags.append(
                f"PATRIMÔNIO EM QUEDA COM LUCRO POSITIVO ({a['ano']}→{b['ano']}: "
                f"PL {a.get('pl_mi')}→{b.get('pl_mi')} R$ mi): distribuição acima do lucro "
                "(dividendo extraordinário/reversão de reservas) — dividendo atual pode não ser recorrente.")
```

por

```python
    # A leitura antiga comparava só o ÚLTIMO par e disparava em 88 de 426
    # empresas, das quais 80 (91%) têm o padrão em menos da metade dos anos.
    # Vale a qualidade histórica, não o período isolado.
    from core.b3_renda_sustentavel import fracao_pl_em_queda_com_lucro
    _frac, _pares = fracao_pl_em_queda_com_lucro(serie)
    if _frac is not None and _frac >= 0.50 and _pares >= 5:
        flags.append(
            f"PATRIMÔNIO EM QUEDA COM LUCRO POSITIVO em {round(_frac * _pares)} "
            f"de {_pares} pares de anos ({_frac:.0%}): padrão persistente de "
            "distribuição acima do lucro (dividendo extraordinário/reversão de "
            "reservas) — dividendo atual pode não ser recorrente.")
    elif _frac is not None and _frac > 0 and _pares >= 2:
        flags.append(
            f"Patrimônio em queda com lucro positivo em {round(_frac * _pares)} "
            f"de {_pares} pares de anos ({_frac:.0%}): episódio, não padrão — "
            "não trate como política de distribuição da empresa.")
```

- [ ] **Step 4: Acrescentar a regra ao prompt do parecer**

Em `_PROMPT_PARECER`, logo depois da regra 5.1, acrescente:

```
5.2. NÃO TRATE EXERCÍCIO ISOLADO COMO PADRÃO. Payout acima do lucro em um ano, \
ou patrimônio caindo num par de anos, é episódio — só é política de distribuição \
insustentável quando o dossiê disser que o padrão se repete na MAIORIA dos anos \
observados. As red flags já dizem em quantos dos N pares o padrão aparece: cite \
essa fração ao afirmar insustentabilidade, e não afirme sem ela.
```

- [ ] **Step 5: Entregar a sustentabilidade ao contexto do chat**

Em `core/llm_context_b3.py`, dentro de `get_company_fundamentals_context`, logo
depois da linha que monta `inds`, acrescente:

```python
        # Ausência declarada, não linha omitida: a LLM precisa distinguir "não
        # sustentável" de "não observado" para não terceirizar a evidência ao
        # usuário — ela já mandou ler relatório gerencial de dado que estava no
        # próprio prompt.
        _sust = row.get("payout_sustentabilidade")
        _n_anos = row.get("n_anos_payout")
        if _sust is not None and _sust == _sust:
            inds += (f" | Sustentabilidade da distribuição={float(_sust):.0%}"
                     f" (payout mediano={_fmt_val('Payout', row.get('payout_mediano_hist'))},"
                     f" {int(_n_anos or 0)} anos observados)")
        else:
            inds += (" | Sustentabilidade da distribuição=não observada "
                     "(menos de 3 anos de payout no banco)")
```

- [ ] **Step 6: Rodar as suítes de contexto e dossiê**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_renda_sustentavel.py tests/test_apb3_chat_context.py tests/test_apb3_broad_context.py tests/test_avaliacao_portfolio_b3_ui.py -v
```

Esperado: PASS.

- [ ] **Step 7: Commit**

```bash
git add core/dossie_b3.py core/llm_context_b3.py tests/test_b3_renda_sustentavel.py
git commit -m "feat(b3): red flag de patrimônio por fração histórica e sustentabilidade no contexto da LLM"
```

---

### Task 9: Suíte completa e fechamento

**Files:**
- Test: toda a suíte.

**Interfaces:**
- Consumes: tudo das Tasks 1-8.
- Produces: nada.

- [ ] **Step 1: Rodar a suíte inteira**

```bash
PYTHONPATH="$PWD" "$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/ -q > local_staging/suite_b3_protecao.txt 2>&1; tail -30 local_staging/suite_b3_protecao.txt
```

Esperado: nenhuma falha nova em relação à linha de base da branch. Se algum
teste pré-existente quebrar, conserte a causa — não o teste — salvo quando ele
afirmar `SCORE_VERSION == "2.25.0"` ou a composição antiga da trilha
`shareholder`, casos em que a atualização é a correção certa.

- [ ] **Step 2: Confirmar que nenhuma criação de portfólio ficou zerada**

Rode a Criação de Portfólio B3 no preview com os perfis padrão e confirme que
nenhum segmento perdeu representante por causa do critério novo. A guarda da
Task 5 deve manter `sem_substituto` vazio para reprovações que sejam apenas de
persistência histórica.

- [ ] **Step 3: Commit final**

```bash
git add local_staging/suite_b3_protecao.txt
git commit -m "test(b3): suíte completa após proteção ao investidor na criação de portfólio"
```

---

## Self-Review

**1. Cobertura do spec**

| Seção do spec | Tarefa |
|---|---|
| §1 `core/b3_renda_sustentavel.py`, faixa, incoerência, três colunas | Task 1 |
| §2 trilha `shareholder`, peso 0,12, ausência no neutro | Task 3 |
| §3 portão A e B, quarta confirmação, `PAYOUT_CRITICO` intacto | Tasks 2 e 4 |
| §3 red flag de `dossie_b3` por fração histórica | Task 8 |
| §4 guarda de viabilidade, líder marcado, critérios pré-existentes intactos | Task 5 |
| §5 tela (DY sustentável, líder marcado), contexto e prompt da LLM | Tasks 7 e 8 |
| §6 `SCORE_VERSION` 2.26.0, `MODEL_SCHEMA_VERSION` 3 | Task 3 |
| Teste 1 | Task 1, `test_fronteiras_da_faixa` |
| Teste 2 | Task 1, `test_episodio_isolado_nao_condena` |
| Teste 3 | Task 1, `test_padrao_persistente_zera` |
| Teste 4 | Task 1, `test_ano_incoerente_sai_como_ausencia` |
| Teste 5 | Task 1 + Task 3, `test_ausencia_de_sustentabilidade_vai_para_o_neutro` |
| Teste 6 | Task 1, `test_dy_sustentavel_nunca_cai_no_dy_bruto` |
| Teste 7 | Task 6, `test_mesma_derivacao_para_as_mesmas_linhas` |
| Teste 8 | Task 4, `test_veto_exige_a_e_b` |
| Teste 9 | Task 5, `test_lider_entra_marcado_quando_segmento_inteiro_reprova` |
| Teste 10 | Task 5, `test_guarda_nao_afrouxa_criterios_preexistentes` |
| Teste 11 | Task 8, `test_red_flag_de_patrimonio_cita_a_fracao_historica` |
| Teste 12 | Task 3, `test_score_version_2_26_0` |

Sem lacuna. As exclusões do spec (Empresas Americanas, `risk_logit`, cobertura
de `Payout`, demais red flags, descasamento universo/segmento) não geram tarefa,
por definição.

**2. Varredura de placeholders**

Nenhum "TBD", "similar à Task N" ou passo sem código. Cada passo de código traz
o bloco literal. O único passo sem código é a verificação visual da Task 7,
Step 4, que é uma inspeção de tela — e traz os três itens exatos a conferir.

**3. Consistência de tipos**

- `sustentabilidade_do_ano` devolve `float` sempre (Task 1) e é chamada só
  internamente por `leitura_da_serie`.
- `leitura_da_serie` devolve `None` (não `NaN`) em Python puro; a conversão para
  `NaN` acontece no `pd.DataFrame.from_dict` do enriquecimento — por isso os
  testes de dicionário usam `is None` e os de quadro usam `np.isnan`.
- `fracao_pl_em_queda_com_lucro` devolve `tuple[float | None, int]` na Task 2 e
  é consumida com essa forma na Task 8 (`_frac, _pares`).
- Os nomes de coluna são idênticos nas seis ocorrências:
  `payout_sustentabilidade`, `dy_sustentavel`, `payout_mediano_hist`,
  `n_anos_payout`, `pl_queda_com_lucro_frac`, `n_pares_pl`.
- `persistencia_historica` tem o mesmo nome e o mesmo default (`True`) em
  `check_holdings` (Task 4) e em `evaluate` (Task 5).
- A chave de log `afrouxado_por_viabilidade` tem a mesma forma nos três pontos
  (semeada na Task 5, semeada de novo na tela na Task 7, lida na Task 7).
