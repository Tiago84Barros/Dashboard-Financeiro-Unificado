# Proteção ao Investidor na Seleção de FIIs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a proteção ao investidor ser critério de entrada da seleção de FIIs — piso sobre a renda recorrente, vetos de concentração, e custo para a opacidade — em vez de observação no relatório.

**Architecture:** Um módulo puro novo (`core/fii_renda_recorrente.py`) vira a fonte única do DY recorrente e das leituras de concentração. A elegibilidade (`core/fii_integrated_model.py`) e o score (`core/fii_methodology.py`) importam dele, e um teste compara os dois caminhos. O otimizador (`core/fii_portfolio_v4.py`) troca o teto escalar por ativo por um vetor, para cobrar a opacidade em peso. A tela e o contexto da LLM passam a exibir o número que decide.

**Tech Stack:** Python 3.12, pandas, numpy, scipy (`milp`, `linprog`, `minimize`), Streamlit, pytest, Postgres (Supabase e warehouse local).

**Spec:** `docs/superpowers/specs/2026-09-09-fii-protecao-investidor-design.md`

## Global Constraints

- **Interpretador:** use `"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest`. O `python` do PATH cai na venv do Hermes e reporta "não tem pytest" — o erro engana.
- **Ausência nunca vira zero nem vira o valor bruto.** `dy_recorrente` devolve `None` quando falta insumo; concentração desconhecida não exclui e não é tratada como 0%.
- **Os dois tetos de concentração só se aplicam a `tijolo` e `hibrido`.** Papel e FoF passam apenas pelo piso de renda recorrente.
- **Valores exatos do spec:** piso `min_recurrent_dy_12m = .08`; `max_tenant_concentration = .40`; `max_lease_expiry_24m = .25`; teto de peso do não divulgado = metade de `PortfolioPolicy.max_asset`.
- **O teto de plausibilidade de 20% continua sobre o DY bruto** (`dy_12m`), não sobre o recorrente: ali ele testa a sanidade da fonte.
- **Versões:** ao fim, `METHODOLOGY_VERSION` 6.8.0 → 6.9.0, `FORMULA_VERSION` acompanha, `INTEGRATED_MODEL_VERSION` 6.7.0 → 6.8.0. Subir versão obriga a reconstruir a safra PIT (Task 8) — sem isso o backtest desliga em silêncio.
- **Branch:** `fii-protecao-investidor`, já criado. A rotina noturna que commita o snapshot de FII na main recusa enquanto um branch de trabalho está em uso; não deixar aberto por dias.
- **Efeito esperado ao final:** universo elegível 220 → 98 (50 papel, 23 tijolo, 22 FoF, 2 híbrido).
- **A proteção não pode inviabilizar a carteira.** `tactical_type_bands` impõe **piso** de tijolo (25% a 40% conforme o regime). Se os tetos reduzidos pela opacidade não comportarem o piso de uma banda, o custo da opacidade cede — e o afrouxamento é **reportado**, nunca silencioso. Medição de 09/09/2026: 10 dos 25 tijolo/híbrido elegíveis são opacos; os 14 tijolo transparentes sozinhos comportam 90% de peso contra um piso de 40%. A folga existe hoje e é circunstancial: a guarda é o que a torna permanente.

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `core/fii_renda_recorrente.py` (novo) | Fonte única: DY recorrente e estado das concentrações. Puro, sem I/O. |
| `core/fii_integrated_model.py` | Portão de entrada: piso e vetos, com razões de exclusão. |
| `core/fii_methodology.py` | Score: `dy_recorrente` no lugar de `dy_12m`; versões. |
| `core/fii_portfolio_v4.py` | Teto de peso por ativo; verificação pós-tilt; yield recorrente da carteira. |
| `views/fiis.py` | Rótulos, colunas da tabela, construção das políticas. |
| `core/llm_context_fii.py` | Entrega `dy_recorrente` e declara concentração não divulgada. |
| `core/llm_fii.py` | Regra 11 do system prompt. |
| `data_pipeline/market/fii_documents.py` | Extração ampliada do prazo médio de locação. |
| `tests/test_fii_renda_recorrente.py` (novo) | Módulo puro e paridade entre os dois consumidores. |
| `tests/test_fii_protecao_elegibilidade.py` (novo) | Portão de entrada. |

---

### Task 1: Ampliar a extração do prazo médio de locação e medir a cobertura

O piso de `wault_anos` não é decidido aqui. Esta tarefa só amplia a fonte e mede — o número do piso vem depois, com a distribuição em mãos, como fizemos com 40% e 25%.

**Files:**
- Modify: `data_pipeline/market/fii_documents.py:99`
- Test: `tests/test_fii_documents.py`

**Interfaces:**
- Consumes: nada.
- Produces: `_METRIC_PATTERNS["wault_anos"]` reconhecendo três formas de escrita. Nenhuma outra tarefa depende desta.

- [ ] **Step 1: Write the failing test**

Em `tests/test_fii_documents.py`, acrescente:

```python
def test_prazo_medio_de_locacao_e_reconhecido_sem_a_sigla():
    """A sigla literal cobria 3% do universo; gestoras escrevem por extenso."""
    from data_pipeline.market.fii_documents import _METRIC_PATTERNS

    padrao = _METRIC_PATTERNS["wault_anos"]
    casos = {
        "WAULT de 4,2 anos": "4,2",
        "prazo médio remanescente dos contratos: 5,1 anos": "5,1",
        "Prazo Médio Ponderado dos Contratos (anos) 3,80": "3,80",
        "WAULT (anos): 6,4": "6,4",
    }
    for texto, esperado in casos.items():
        encontrado = padrao.search(texto)
        assert encontrado, f"não reconheceu: {texto}"
        assert encontrado.group(1) == esperado


def test_prazo_medio_nao_captura_numero_sem_contexto_de_prazo():
    from data_pipeline.market.fii_documents import _METRIC_PATTERNS

    padrao = _METRIC_PATTERNS["wault_anos"]
    assert padrao.search("prazo médio de pagamento dos fornecedores: 45 dias") is None
    assert padrao.search("o fundo tem 12 anos de história") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_documents.py -k prazo_medio -v
```

Expected: FAIL — o padrão atual só casa `"WAULT de 4,2 anos"`.

- [ ] **Step 3: Write minimal implementation**

Substitua a linha 99 de `data_pipeline/market/fii_documents.py`:

```python
    # A sigla literal cobria 3,0% do universo e 5,0% de tijolo/híbrido, o que
    # inviabilizava qualquer regra sobre prazo de locação. Gestoras brasileiras
    # escrevem "prazo médio remanescente/ponderado dos contratos", e publicam a
    # sigla em tabela com o "(anos)" no cabeçalho, longe do número.
    "wault_anos": re.compile(
        r"(?:\bWAULT\b|prazo\s+m[eé]dio\s+(?:remanescente|ponderado)"
        r"(?:\s+ponderado)?(?:\s+dos?\s+contratos?)?)"
        r"[^\d]{0,40}(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:anos?|years?)?",
        re.I,
    ),
```

O sufixo `(?:anos?|years?)?` passa a ser opcional porque a unidade costuma estar no cabeçalho da tabela. O limite `\d{1,2}` já rejeita valores implausíveis, e `[^\d]{0,40}` impede que o número venha de outra frase.

- [ ] **Step 4: Run test to verify it passes**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_documents.py -v
```

Expected: PASS, incluindo os testes já existentes do arquivo.

- [ ] **Step 5: Medir a cobertura resultante**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -c "import pandas as pd; from core.market_read import load_fii_methodology_inputs as L; d=L(); w=pd.to_numeric(d['wault_anos'],errors='coerce'); t=d['tipo'].isin(['tijolo','hibrido']); print('universo', round(float(w.notna().mean()),3)); print('tijolo/hibrido', round(float(w[t].notna().mean()),3)); print(w[t].quantile([.1,.25,.5,.75]).round(2).to_string())"
```

A vitrine só reflete a extração nova depois da republicação (Task 8), então este número é a linha de base **antes**. Registre-o no commit. A medição que decide o piso é refeita na Task 8, e o piso entra em ciclo seguinte se a cobertura ainda ficar abaixo de 40% em tijolo/híbrido.

- [ ] **Step 6: Commit**

```bash
git add data_pipeline/market/fii_documents.py tests/test_fii_documents.py
git commit -m "fix(fii): reconhece prazo medio de locacao escrito por extenso"
```

---

### Task 2: Módulo único da renda recorrente

**Files:**
- Create: `core/fii_renda_recorrente.py`
- Test: `tests/test_fii_renda_recorrente.py`

**Interfaces:**
- Consumes: nada.
- Produces, usados pelas Tasks 3, 4, 5, 6 e 7:
  - `DY_RECORRENTE_FORMULA: str`
  - `dy_recorrente(row: Mapping[str, Any]) -> float | None`
  - `TETO_LOCATARIO: float`, `TETO_VENCIMENTO_24M: float`
  - `estado_concentracao(row, chave: str, teto: float) -> str`, devolvendo `"ok"`, `"acima_do_teto"` ou `"nao_divulgado"`
  - `PROTECAO_NAO_DIVULGADA: tuple[str, ...]` — as chaves cuja ausência limita peso
  - `protecao_nao_divulgada(row) -> tuple[str, ...]` — chaves ausentes para o tipo da linha

- [ ] **Step 1: Write the failing test**

Crie `tests/test_fii_renda_recorrente.py`:

```python
"""A renda que decide é a recorrente, e a ausência precisa continuar ausente.

O piso de DY incidia sobre a renda divulgada e por isso selecionava o yield
inflado: KORE11 entrava com 18,17% sustentado por 58,25% de recorrência.
"""
from __future__ import annotations

from core.fii_renda_recorrente import (
    TETO_LOCATARIO,
    TETO_VENCIMENTO_24M,
    dy_recorrente,
    estado_concentracao,
    protecao_nao_divulgada,
)


def test_dy_recorrente_e_o_produto_do_yield_pela_recorrencia():
    assert dy_recorrente({"dy_12m": .181666, "income_recurrence": .582464}) == 0.1058


def test_recorrencia_ausente_nao_vira_o_dy_bruto():
    """Fallback que só preenche lacuna nunca contradiz — e contradizer é o ponto."""
    assert dy_recorrente({"dy_12m": .18, "income_recurrence": None}) is None
    assert dy_recorrente({"dy_12m": .18}) is None


def test_dy_ausente_tambem_nao_produz_numero():
    assert dy_recorrente({"income_recurrence": .9}) is None


def test_valores_nao_finitos_nao_viram_observacao():
    assert dy_recorrente({"dy_12m": float("nan"), "income_recurrence": .9}) is None


def test_dy_gravado_em_percentual_e_normalizado_antes_do_produto():
    """A vitrine grava ora 0.18 ora 18.0; o produto precisa concordar."""
    assert dy_recorrente({"dy_12m": 18.1666, "income_recurrence": .582464}) == 0.1058


def test_estado_da_concentracao_separa_acima_do_teto_de_nao_divulgado():
    acima = {"tipo": "tijolo", "tenant_concentration": .45}
    abaixo = {"tipo": "tijolo", "tenant_concentration": .2225}
    ausente = {"tipo": "tijolo"}
    assert estado_concentracao(acima, "tenant_concentration", TETO_LOCATARIO) == "acima_do_teto"
    assert estado_concentracao(abaixo, "tenant_concentration", TETO_LOCATARIO) == "ok"
    assert estado_concentracao(ausente, "tenant_concentration", TETO_LOCATARIO) == "nao_divulgado"


def test_papel_e_fof_nao_tem_protecao_de_locatario_a_declarar():
    """Cobrar de papel um dado que só tijolo publica seria punir o tipo errado."""
    assert protecao_nao_divulgada({"tipo": "papel"}) == ()
    assert protecao_nao_divulgada({"tipo": "fof"}) == ()


def test_tijolo_sem_as_duas_metricas_declara_as_duas():
    assert protecao_nao_divulgada({"tipo": "tijolo"}) == (
        "tenant_concentration", "lease_expiry_concentration_24m")


def test_tijolo_com_uma_metrica_declara_so_a_outra():
    row = {"tipo": "tijolo", "tenant_concentration": .10}
    assert protecao_nao_divulgada(row) == ("lease_expiry_concentration_24m",)


def test_tetos_sao_os_valores_aprovados_no_spec():
    assert TETO_LOCATARIO == .40
    assert TETO_VENCIMENTO_24M == .25
```

- [ ] **Step 2: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_renda_recorrente.py -v
```

Expected: FAIL com `ModuleNotFoundError: No module named 'core.fii_renda_recorrente'`.

- [ ] **Step 3: Write minimal implementation**

Crie `core/fii_renda_recorrente.py`:

```python
"""Renda recorrente e evidência de proteção: fonte única para os dois consumidores.

O piso de elegibilidade incidia sobre o DY divulgado, e por isso selecionava
ativamente o yield inflado por receita não recorrente. A renda que decide passa
a ser ``dy_12m * income_recurrence``.

Elegibilidade e score importam deste módulo. Ter a regra em um consumidor só já
fez a vitrine publicar dividend yield que a tela não reconhecia.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

DY_RECORRENTE_FORMULA = "dy_12m * income_recurrence"

TETO_LOCATARIO = .40
TETO_VENCIMENTO_24M = .25

#: Métricas cuja ausência é omissão do gestor, não do nosso pipeline. Só fazem
#: sentido para carteira própria de imóveis.
PROTECAO_NAO_DIVULGADA = ("tenant_concentration", "lease_expiry_concentration_24m")

_TIPOS_COM_IMOVEL = frozenset({"tijolo", "hibrido"})


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fracao(value: Any) -> float | None:
    """A vitrine grava percentuais ora em fração (0.18) ora em ponto (18.0)."""
    number = _finite(value)
    if number is None:
        return None
    return number / 100.0 if abs(number) > 1 else number


def dy_recorrente(row: Mapping[str, Any]) -> float | None:
    """Parcela do yield sustentada por resultado recorrente.

    Devolve ``None`` — nunca o DY bruto — quando falta qualquer um dos dois
    insumos. Um fallback que só preenche lacuna nunca contradiz a entrada, e
    contradizer o yield divulgado é exatamente a função desta métrica.
    """
    dy = _fracao(row.get("dy_12m"))
    recorrencia = _fracao(row.get("income_recurrence"))
    if dy is None or recorrencia is None:
        return None
    # round() mantém o número estável entre o widget, o prompt e o teste.
    return round(dy * recorrencia, 4)


def _tem_imovel(row: Mapping[str, Any]) -> bool:
    return str(row.get("tipo") or "").strip().lower() in _TIPOS_COM_IMOVEL


def estado_concentracao(row: Mapping[str, Any], chave: str, teto: float) -> str:
    """``ok`` | ``acima_do_teto`` | ``nao_divulgado``.

    Os três estados são distintos de propósito: vetar apenas quem divulga
    premiaria quem cala, e tratar ausência como zero aprovaria o opaco.
    """
    valor = _fracao(row.get(chave))
    if valor is None:
        return "nao_divulgado"
    return "acima_do_teto" if valor > teto else "ok"


def protecao_nao_divulgada(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Métricas de proteção que o gestor não publicou, para tijolo e híbrido."""
    if not _tem_imovel(row):
        return ()
    return tuple(chave for chave in PROTECAO_NAO_DIVULGADA
                 if _fracao(row.get(chave)) is None)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_renda_recorrente.py -v
```

Expected: PASS, 10 testes.

- [ ] **Step 5: Commit**

```bash
git add core/fii_renda_recorrente.py tests/test_fii_renda_recorrente.py
git commit -m "feat(fii): fonte unica da renda recorrente e da evidencia de protecao"
```

---

### Task 3: Portão de entrada

**Files:**
- Modify: `core/fii_integrated_model.py:17-30` (política), `:45-92` (razões)
- Test: `tests/test_fii_protecao_elegibilidade.py` (novo), `tests/test_fii_integrated_model.py` (ajuste dos chamadores)

**Interfaces:**
- Consumes: `dy_recorrente`, `estado_concentracao`, `TETO_LOCATARIO`, `TETO_VENCIMENTO_24M` da Task 2.
- Produces: `IntegratedEligibilityPolicy` com os campos `min_recurrent_dy_12m: float = .08`, `max_tenant_concentration: float = .40`, `max_lease_expiry_24m: float = .25`. O campo `min_dy_12m` **deixa de existir**. Consumido pela Task 6.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_fii_protecao_elegibilidade.py`:

```python
"""Proteção é critério de entrada, não observação no relatório."""
from __future__ import annotations

import pytest

from core.fii_integrated_model import (
    IntegratedEligibilityPolicy,
    apply_integrated_eligibility,
)

_BASE = {
    "ticker": "TEST11", "tipo": "tijolo", "liquidez_diaria": 5e6,
    "pvp": .95, "history_months": 60, "max_drawdown": -.20,
    "dy_12m": .12, "income_recurrence": .90,
    "tenant_concentration": .10, "lease_expiry_concentration_24m": .10,
}


def _linha(**mudancas):
    return {**_BASE, **mudancas}


def _reasons(row, policy=None):
    from core.fii_integrated_model import _eligibility_reasons
    return tuple(_eligibility_reasons(row, policy or IntegratedEligibilityPolicy()))


def test_yield_alto_sustentado_por_renda_nao_recorrente_e_excluido():
    """KORE11: 18,17% de DY com 58,25% de recorrência = 10,58% recorrentes."""
    row = _linha(dy_12m=.1817, income_recurrence=.30)  # 5,45% recorrentes
    assert "renda recorrente abaixo do mínimo" in _reasons(row)


def test_yield_recorrente_acima_do_piso_passa():
    assert _reasons(_linha(dy_12m=.1817, income_recurrence=.5825)) == ()


def test_recorrencia_ausente_exclui_com_razao_propria():
    row = _linha(income_recurrence=None)
    assert "renda recorrente ausente" in _reasons(row)
    assert "renda recorrente abaixo do mínimo" not in _reasons(row)


def test_concentracao_de_locatario_acima_do_teto_exclui():
    assert "concentração de locatário acima do teto" in _reasons(
        _linha(tenant_concentration=.45))


def test_concentracao_de_locatario_ausente_nao_exclui():
    """Vetar só quem divulga premiaria quem cala."""
    assert _reasons(_linha(tenant_concentration=None)) == ()


def test_vencimentos_em_24m_acima_do_teto_excluem():
    assert "vencimentos em 24m acima do teto" in _reasons(
        _linha(lease_expiry_concentration_24m=.29))


def test_vencimentos_ausentes_nao_excluem():
    assert _reasons(_linha(lease_expiry_concentration_24m=None)) == ()


@pytest.mark.parametrize("tipo", ["papel", "fof"])
def test_papel_e_fof_nao_sao_cobrados_por_metrica_de_imovel(tipo):
    row = _linha(tipo=tipo, tenant_concentration=None,
                 lease_expiry_concentration_24m=None)
    assert _reasons(row) == ()


def test_teto_de_plausibilidade_de_20_por_cento_continua_sobre_o_dy_bruto():
    """Ali o teto testa a sanidade da fonte, não a qualidade da renda."""
    row = _linha(dy_12m=.35, income_recurrence=.50)  # 17,5% recorrentes
    assert "DY 12m acima do limite de plausibilidade" in _reasons(row)


def test_a_politica_nao_aceita_mais_o_nome_antigo_do_piso():
    """O rename impede que um chamador passe a semântica antiga em silêncio."""
    with pytest.raises(TypeError):
        IntegratedEligibilityPolicy(min_dy_12m=.08)


def test_relatorio_agrega_as_razoes_novas():
    linhas = [_linha(ticker="A11", income_recurrence=.20),
              _linha(ticker="B11", tenant_concentration=.90)]
    _, relatorio = apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())
    assert relatorio["eligible_count"] == 0
    assert relatorio["exclusion_counts"]["renda recorrente abaixo do mínimo"] == 1
    assert relatorio["exclusion_counts"]["concentração de locatário acima do teto"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_protecao_elegibilidade.py -v
```

Expected: FAIL — `IntegratedEligibilityPolicy` ainda aceita `min_dy_12m` e não conhece os tetos.

- [ ] **Step 3: Write minimal implementation**

Em `core/fii_integrated_model.py`, no topo, acrescente o import:

```python
from core.fii_renda_recorrente import (
    TETO_LOCATARIO,
    TETO_VENCIMENTO_24M,
    dy_recorrente,
    estado_concentracao,
)
```

Troque `min_dy_12m` por três campos na dataclass:

```python
@dataclass(frozen=True)
class IntegratedEligibilityPolicy:
    min_daily_liquidity: float = 1_000_000.0
    # O piso incide sobre a renda recorrente, não sobre a divulgada: sobre o DY
    # bruto ele selecionava ativamente o yield inflado por evento não
    # recorrente. O rename é deliberado — impede que um chamador passe a
    # semântica antiga em silêncio.
    min_recurrent_dy_12m: float = .08
    min_history_months: int = 24
    max_drawdown: float = .35
    pvp_min: float = .55
    pvp_max: float = 1.30
    max_tenant_concentration: float = TETO_LOCATARIO
    max_lease_expiry_24m: float = TETO_VENCIMENTO_24M
    require_pvp_below_one: bool = False
    require_multi_region: bool = False
    require_min_properties: bool = False
    min_properties: int = 8
    require_multicategory: bool = False
```

Em `_eligibility_reasons`, substitua o bloco do DY:

```python
    dy = _normalized_yield(row.get("dy_12m"))
    recorrente = dy_recorrente(row)
    if dy is None:
        reasons.append("DY 12m ausente")
    elif dy > .20:
        # Teto de sanidade da fonte, deliberadamente sobre o DY bruto.
        reasons.append("DY 12m acima do limite de plausibilidade")
    elif recorrente is None:
        reasons.append("renda recorrente ausente")
    elif recorrente < policy.min_recurrent_dy_12m:
        reasons.append("renda recorrente abaixo do mínimo")
```

E, dentro do bloco `if fii_type in {"tijolo", "hibrido"}:`, acrescente:

```python
        if estado_concentracao(row, "tenant_concentration",
                               policy.max_tenant_concentration) == "acima_do_teto":
            reasons.append("concentração de locatário acima do teto")
        if estado_concentracao(row, "lease_expiry_concentration_24m",
                               policy.max_lease_expiry_24m) == "acima_do_teto":
            reasons.append("vencimentos em 24m acima do teto")
```

Suba `INTEGRATED_MODEL_VERSION` para `"6.8.0"`.

- [ ] **Step 4: Run test to verify it passes**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_protecao_elegibilidade.py tests/test_fii_integrated_model.py -v
```

Expected: PASS. Se `tests/test_fii_integrated_model.py` falhar por passar `min_dy_12m=`, atualize as chamadas para `min_recurrent_dy_12m=` — é a guarda funcionando como projetada.

- [ ] **Step 5: Commit**

```bash
git add core/fii_integrated_model.py tests/test_fii_protecao_elegibilidade.py tests/test_fii_integrated_model.py
git commit -m "feat(fii): piso sobre renda recorrente e vetos de concentracao"
```

---

### Task 4: A nota passa a ordenar pela renda recorrente

**Files:**
- Modify: `core/fii_methodology.py:113` (métrica), `:66-69` (versões), `:374+` (`score_fiis_by_type`)
- Test: `tests/test_fii_renda_recorrente.py` (acréscimo), `tests/test_fii_methodology_v4.py`

**Interfaces:**
- Consumes: `dy_recorrente` da Task 2.
- Produces: `MetricDefinition("dy_recorrente", "income", .12, "higher", critical=True, max_age_days=15)` em `COMMON_METRICS`; cada linha pontuada ganha a chave `dy_recorrente`. `METHODOLOGY_VERSION == "6.9.0"`. Consumido pelas Tasks 6 e 7.

- [ ] **Step 1: Write the failing test**

Acrescente a `tests/test_fii_renda_recorrente.py`:

```python
def test_elegibilidade_e_score_derivam_o_mesmo_numero():
    """Regra certa em um consumidor só já publicou yield errado na vitrine."""
    from core.fii_integrated_model import (
        IntegratedEligibilityPolicy, apply_integrated_eligibility)
    from core.fii_methodology import score_fiis_by_type

    linhas = [
        {"ticker": "AAA11", "tipo": "papel", "liquidez_diaria": 5e6, "pvp": .95,
         "history_months": 60, "max_drawdown": -.2,
         "dy_12m": .1817, "income_recurrence": .5825},
        {"ticker": "BBB11", "tipo": "papel", "liquidez_diaria": 5e6, "pvp": .98,
         "history_months": 60, "max_drawdown": -.2,
         "dy_12m": .1261, "income_recurrence": .6483},
    ]
    eleg, _ = apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())
    pontuadas = score_fiis_by_type(eleg)
    por_ticker = {row["ticker"]: row for row in pontuadas}
    assert por_ticker["AAA11"]["dy_recorrente"] == dy_recorrente(linhas[0])
    assert por_ticker["BBB11"]["dy_recorrente"] == dy_recorrente(linhas[1])


def test_a_metodologia_pontua_a_renda_recorrente_e_nao_a_divulgada():
    from core.fii_methodology import COMMON_METRICS

    chaves = {definicao.key: definicao for definicao in COMMON_METRICS}
    assert "dy_12m" not in chaves
    renda = chaves["dy_recorrente"]
    assert (renda.weight, renda.critical, renda.max_age_days) == (.12, True, 15)


def test_versao_da_metodologia_subiu_com_a_formula():
    from core.fii_methodology import FORMULA_VERSION, METHODOLOGY_VERSION

    assert METHODOLOGY_VERSION == "6.9.0"
    assert "6.9.0" in FORMULA_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_renda_recorrente.py -k "mesmo_numero or pontua or versao" -v
```

Expected: FAIL — `KeyError: 'dy_recorrente'` e `METHODOLOGY_VERSION == "6.8.0"`.

- [ ] **Step 3: Write minimal implementation**

Em `core/fii_methodology.py`, no topo:

```python
from core.fii_renda_recorrente import dy_recorrente
```

Troque a primeira entrada de `COMMON_METRICS`:

```python
    # A renda que ordena é a recorrente. Sobre o DY divulgado, a média
    # ponderada deixava o yield inflado compensar a renda não recorrente.
    MetricDefinition("dy_recorrente", "income", .12, "higher", critical=True,
                     max_age_days=15),
```

`income_recurrence` permanece com peso .08 — a repetição é deliberada: o produto mede o nível da renda sustentável e a métrica isolada mede a estabilidade dela.

Suba as versões:

```python
# 6.9.0: o piso e a nota passaram a incidir sobre a renda recorrente
# (dy_12m * income_recurrence) e a concentração virou veto. A fórmula mudou,
# então a versão muda junto — senão as notas novas herdariam em silêncio o
# certificado PIT da 6.8.0.
METHODOLOGY_VERSION = "6.9.0"
FORMULA_VERSION = "br-fii-integrated-income-resilience-6.9.0"
```

No início de `score_fiis_by_type`, derive a coluna antes de qualquer leitura de métrica, para que nenhum chamador precise lembrar de fazê-lo:

```python
    rows = [{**row, "dy_recorrente": dy_recorrente(row)} for row in rows]
```

Ajuste a assinatura/uso conforme o nome da variável local existente na função — a derivação tem de vir antes da montagem de `_metric_values`.

`max_age_days=15` continua olhando o frescor de `dy_12m`. Verifique em `_freshness_for_metric` se a proveniência é buscada por `definition.key`; se for, acrescente `fallback_keys=("dy_12m",)` à definição para que o frescor continue vindo do vintage da cotação.

- [ ] **Step 4: Run test to verify it passes**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_renda_recorrente.py tests/test_fii_methodology_v4.py tests/test_fii_encolhimento_por_cobertura.py -v
```

Expected: PASS. Testes que fixavam `"dy_12m"` como métrica pontuada precisam ser repontados para `"dy_recorrente"`.

- [ ] **Step 5: Commit**

```bash
git add core/fii_methodology.py tests/
git commit -m "feat(fii): nota ordena pela renda recorrente e sobe para 6.9.0"
```

---

### Task 5: A opacidade custa peso

**Files:**
- Modify: `core/fii_portfolio_v4.py:227` (ligação MILP), `:261` (bounds MILP), `:679`, `:693`, `:700` (bounds do otimizador contínuo), `:462-500` (`portfolio_constraint_violations`), `:786-788` (yields da carteira)
- Test: `tests/test_fii_portfolio_v4.py`

**Interfaces:**
- Consumes: `protecao_nao_divulgada` da Task 2.
- Produces: função interna `_teto_por_ativo(rows, policy) -> np.ndarray`; cada item da carteira ganha `"protecao_nao_divulgada": tuple[str, ...]`; o resultado ganha `"recurrent_yield_12m": float`.

- [ ] **Step 1: Write the failing test**

Acrescente a `tests/test_fii_portfolio_v4.py`:

```python
def test_fundo_sem_protecao_divulgada_nao_passa_de_metade_do_teto():
    """A verificação incide sobre o peso FINAL: já houve teto de 15%
    respeitado em toda chamada e violado em 27,8% no acumulado."""
    import numpy as np
    from core.fii_portfolio_v4 import _teto_por_ativo, PortfolioPolicy

    policy = PortfolioPolicy(max_asset=.15)
    rows = [
        {"ticker": "OPACO11", "tipo": "tijolo"},
        {"ticker": "ABERTO11", "tipo": "tijolo",
         "tenant_concentration": .10, "lease_expiry_concentration_24m": .10},
        {"ticker": "PAPEL11", "tipo": "papel"},
    ]
    assert np.allclose(_teto_por_ativo(rows, policy), [.075, .15, .15])


def test_violacao_de_teto_por_ativo_e_reportada_para_o_fundo_opaco():
    from core.fii_portfolio_v4 import PortfolioPolicy, portfolio_constraint_violations

    policy = PortfolioPolicy(max_asset=.15)
    itens = [
        {"ticker": "OPACO11", "tipo": "tijolo", "weight": .12, "confidence": .8},
        {"ticker": "PAPEL11", "tipo": "papel", "weight": .88, "confidence": .8},
    ]
    violacoes = portfolio_constraint_violations(itens, policy)
    assert any("OPACO11" in v for v in violacoes)


def test_carteira_reporta_o_yield_recorrente_alem_do_divulgado():
    from core.fii_portfolio_v4 import _resumo_de_renda

    itens = [
        {"weight": .5, "dy_12m": .1817, "income_recurrence": .5825},
        {"weight": .5, "dy_12m": .1379, "income_recurrence": .8886},
    ]
    resumo = _resumo_de_renda(itens)
    assert resumo["trailing_yield_12m"] == round((.1817 + .1379) / 2, 6)
    assert resumo["recurrent_yield_12m"] == round((.1058 + .1225) / 2, 6)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_portfolio_v4.py -k "opaco or teto_por_ativo or yield_recorrente" -v
```

Expected: FAIL com `ImportError: cannot import name '_teto_por_ativo'`.

- [ ] **Step 3: Write minimal implementation**

Em `core/fii_portfolio_v4.py`, acrescente o import e a função:

```python
from core.fii_renda_recorrente import dy_recorrente, protecao_nao_divulgada


def _teto_por_ativo(rows: list[dict], policy: PortfolioPolicy) -> np.ndarray:
    """Teto de peso individual, reduzido à metade para quem não divulga proteção.

    Excluir o fundo opaco apagaria metade do universo de tijolo; deixá-lo
    passar sem custo premiaria quem cala. O meio-termo é o peso.
    """
    return np.array([
        policy.max_asset * (.5 if protecao_nao_divulgada(row) else 1.0)
        for row in rows
    ])


def _resumo_de_renda(items: list[dict]) -> dict[str, float]:
    """Yield divulgado e yield recorrente da carteira, lado a lado."""
    def _yield(row: dict) -> float:
        valor = _num(row.get("dy_12m"))
        return valor / 100 if valor > 1 else valor

    return {
        "trailing_yield_12m": round(
            sum(item["weight"] * _yield(item) for item in items), 6),
        "recurrent_yield_12m": round(
            sum(item["weight"] * (dy_recorrente(item) or 0.0) for item in items), 6),
    }
```

Substitua os usos escalares. Na ligação do MILP (linha 227), troque `-policy.max_asset` pelo teto do próprio ativo:

```python
    tetos = _teto_por_ativo(pool, policy)
    for index in range(n):
        row = np.zeros(2 * n)
        row[index] = 1.0
        row[n + index] = -float(tetos[index])
```

Nos `Bounds` do MILP (linha 261), troque `np.full(n, policy.max_asset)` por `tetos`.

No otimizador contínuo, os três `bounds=[(policy.min_asset_weight, policy.max_asset)] * n` (linprog e os dois SLSQP) viram:

```python
        tetos = _teto_por_ativo(rows, policy)
        limites = [(policy.min_asset_weight, float(teto)) for teto in tetos]
```

usando `bounds=limites` nos três.

**O ponto crítico:** `bound_macro_weights` roda **depois** do solver e pode elevar um peso acima do que o solver aprovou. Logo após a linha `weights = bound_macro_weights(...).to_numpy()`, prenda o resultado acumulado:

```python
    # O tilt macro roda depois do solver e já elevou peso acima de teto
    # aprovado neste projeto. O limite tem de prender o resultado final.
    weights = np.minimum(weights, tetos)
    weights = weights / weights.sum()
```

Em `portfolio_constraint_violations`, troque a checagem escalar da linha 474:

```python
    tetos = _teto_por_ativo(rows, policy)
    excedentes = [str(row.get("ticker")) for row, teto in zip(rows, tetos)
                  if _num(row.get("weight")) > teto + tolerance]
    if excedentes:
        violations.append(
            "peso acima do teto individual: " + ", ".join(sorted(excedentes)))
```

Ajuste o nome da lista de violações ao que já existe na função. Nos itens montados na linha ~730, acrescente `"protecao_nao_divulgada": protecao_nao_divulgada(rows[i])`. Nas linhas 786-788, substitua o cálculo inline pelos valores de `_resumo_de_renda(items)`, mantendo `trailing_yield_12m` e `expected_yield` como estão e acrescentando `recurrent_yield_12m`.

- [ ] **Step 4: Write the failing test for feasibility**

O teto reduzido é um custo, não um veto — e um custo que zera a carteira virou veto.
`tactical_type_bands` impõe piso de tijolo de 25% a 40%. Acrescente a
`tests/test_fii_portfolio_v4.py`:

```python
def test_opacidade_cede_quando_inviabilizaria_a_banda_do_tipo():
    """Custo que zera a carteira deixou de ser custo e virou veto.

    Precedente no próprio arquivo: max_weighted_uncertainty foi de .30 para .35
    porque tornava o LP inviável no universo real.
    """
    import numpy as np
    from core.fii_portfolio_v4 import (
        PortfolioPolicy, _afrouxa_teto_por_viabilidade, _teto_por_ativo)

    policy = PortfolioPolicy(max_asset=.15)
    # Cinco tijolos, todos opacos: 5 x .075 = .375 contra um piso de banda .40.
    rows = [{"ticker": f"T{i}11", "tipo": "tijolo"} for i in range(5)]
    rows += [{"ticker": "P11", "tipo": "papel"}]
    bands = {"tijolo": (.40, .60), "papel": (.15, .35)}

    caps, notas = _afrouxa_teto_por_viabilidade(
        _teto_por_ativo(rows, policy), rows, policy, bands)
    assert caps[:5].sum() >= .40
    assert caps.max() <= policy.max_asset
    assert any("tijolo" in nota for nota in notas)


def test_afrouxamento_nao_ocorre_quando_ha_folga():
    """Com folga, a opacidade continua custando: relaxar sempre apagaria a regra."""
    import numpy as np
    from core.fii_portfolio_v4 import (
        PortfolioPolicy, _afrouxa_teto_por_viabilidade, _teto_por_ativo)

    policy = PortfolioPolicy(max_asset=.15)
    rows = [{"ticker": "OPACO11", "tipo": "tijolo"}]
    rows += [{"ticker": f"OK{i}11", "tipo": "tijolo",
              "tenant_concentration": .10,
              "lease_expiry_concentration_24m": .10} for i in range(4)]
    bands = {"tijolo": (.40, .60)}

    caps, notas = _afrouxa_teto_por_viabilidade(
        _teto_por_ativo(rows, policy), rows, policy, bands)
    assert caps[0] == .075
    assert notas == []


def test_teto_por_ativo_sempre_comporta_uma_carteira_inteira():
    """Soma dos tetos abaixo de 1 devolve carteira vazia sem dizer por quê."""
    import numpy as np
    from core.fii_portfolio_v4 import (
        PortfolioPolicy, _afrouxa_teto_por_viabilidade, _teto_por_ativo)

    policy = PortfolioPolicy(max_asset=.15, max_assets=12)
    rows = [{"ticker": f"T{i}11", "tipo": "tijolo"} for i in range(12)]
    caps, notas = _afrouxa_teto_por_viabilidade(
        _teto_por_ativo(rows, policy), rows, policy, {})
    assert np.sort(caps)[::-1][:policy.max_assets].sum() >= 1.0
    assert notas
```

- [ ] **Step 5: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_portfolio_v4.py -k "opacidade_cede or afrouxamento or carteira_inteira" -v
```

Expected: FAIL com `ImportError: cannot import name '_afrouxa_teto_por_viabilidade'`.

- [ ] **Step 6: Implement the feasibility guard**

Em `core/fii_portfolio_v4.py`, logo abaixo de `_teto_por_ativo`:

```python
def _afrouxa_teto_por_viabilidade(
    caps: np.ndarray,
    rows: list[dict],
    policy: PortfolioPolicy,
    bands: dict[str, tuple[float, float]],
) -> tuple[np.ndarray, list[str]]:
    """Devolve o custo da opacidade ao patamar mínimo que mantém a carteira viável.

    O teto reduzido é um custo, não um veto. Quando a perna de um tipo não
    comporta o piso da própria banda — ou quando os tetos somados não chegam a
    100% — o desconto cede pelo mínimo necessário, até no máximo
    ``policy.max_asset``. O afrouxamento entra em ``notas`` porque proteção que
    cede em silêncio deixa de ser proteção verificável.
    """
    caps = np.asarray(caps, dtype=float).copy()
    tipos = np.array([str(row.get("tipo") or "").strip().lower() for row in rows])
    notas: list[str] = []

    def _eleva(mascara: np.ndarray, necessario: float, motivo: str) -> None:
        indices = np.flatnonzero(mascara)
        if not indices.size:
            return
        # Capacidade dos que efetivamente cabem na carteira.
        usaveis = indices[np.argsort(caps[indices])[::-1]][:policy.max_assets]
        if caps[usaveis].sum() >= necessario - 1e-9:
            return
        descontados = usaveis[caps[usaveis] < policy.max_asset - 1e-9]
        if not descontados.size:
            return
        folga = necessario - caps[usaveis].sum()
        # Distribui o mínimo necessário igualmente entre os descontados.
        caps[descontados] = np.minimum(
            caps[descontados] + folga / descontados.size, policy.max_asset)
        notas.append(motivo)

    for tipo, (piso, _teto) in (bands or {}).items():
        _eleva(tipos == tipo, float(piso),
               f"custo da opacidade reduzido em {tipo}: os tetos não "
               f"comportavam o piso de banda de {piso:.0%}")

    _eleva(np.ones(len(rows), dtype=bool), 1.0,
           "custo da opacidade reduzido: os tetos somados não comportavam "
           "uma carteira inteira")
    return caps, notas
```

Nos três pontos que consomem `_teto_por_ativo` (MILP, linprog e os dois SLSQP),
troque a chamada crua por:

```python
    tetos, notas_de_viabilidade = _afrouxa_teto_por_viabilidade(
        _teto_por_ativo(rows, policy), rows, policy, bands)
```

usando as `bands` já calculadas por `_adaptive_type_bands` no escopo. Anexe
`notas_de_viabilidade` à lista de avisos que o resultado já devolve à tela, do
mesmo modo que os bloqueios de cobertura — a Task 7 a exibe.

`portfolio_constraint_violations` passa a usar os tetos **afrouxados**: cobrar
do resultado um limite que o solver não recebeu produziria violação fantasma.

- [ ] **Step 7: Run test to verify it passes**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fii_portfolio_v4.py tests/test_fii_v4_portao.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add core/fii_portfolio_v4.py tests/test_fii_portfolio_v4.py
git commit -m "feat(fii): teto de peso por ativo cobra a opacidade sem inviabilizar a carteira"
```

---

### Task 6: Tela

**Files:**
- Modify: `views/fiis.py:1187-1188` (rótulo), `:1272-1280` (política), `:1460-1490` (colunas)
- Test: `tests/test_fiis_ui.py`

**Interfaces:**
- Consumes: `min_recurrent_dy_12m` da Task 3; `dy_recorrente` da Task 4.
- Produces: nenhum consumidor a jusante.

- [ ] **Step 1: Write the failing test**

Acrescente a `tests/test_fiis_ui.py`:

```python
def test_o_piso_da_tela_e_o_da_renda_recorrente():
    """A coluna que decide e a que aparece têm de ser a mesma."""
    corpo = inspect.getsource(fiis._integrated_preference_controls)
    assert '"DY recorrente 12m mín. (%)"' in corpo
    assert "min_recurrent_dy_12m=min_dy" in corpo
    assert "min_dy_12m=" not in corpo
```

- [ ] **Step 2: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fiis_ui.py -k renda_recorrente -v
```

Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Em `views/fiis.py`, linha 1187, troque o rótulo mantendo a chave de sessão intacta (trocar a chave apagaria a preferência salva do usuário):

```python
        min_dy = c5.slider("DY recorrente 12m mín. (%)", 0.0, 20.0, 8.0, .5,
                           key="fii_pref_integrated_dy",
                           help="Incide sobre dy_12m × income_recurrence: a "
                                "parcela do yield sustentada por resultado "
                                "recorrente.") / 100
```

Na construção da política (linha ~1273), troque `min_dy_12m=min_dy` por `min_recurrent_dy_12m=min_dy`.

Onde a tela já exibe os bloqueios de cobertura da carteira, exiba também as
notas de viabilidade devolvidas pela Task 5 — se o custo da opacidade cedeu para
manter a banda de um tipo, o investidor precisa ler isso junto com a carteira.

Na tabela de resultados, acrescente a coluna "DY recorrente" imediatamente antes da coluna de DY existente e renomeie o rótulo da existente para "DY divulgado", formatando ambas como percentual — siga o padrão de `st.column_config` já usado no bloco das linhas 1460-1490.

- [ ] **Step 4: Run test to verify it passes**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_fiis_ui.py -v
```

Expected: PASS.

- [ ] **Step 5: Verificar na aplicação**

Abra a página Seleção de FIIs pelo Browser pane, confirme que o expander "Carteira e elegibilidade" mostra o rótulo novo, que a tabela traz as duas colunas de DY, e que o relatório de exclusões lista as quatro razões novas com contagem. Capture a tela.

- [ ] **Step 6: Commit**

```bash
git add views/fiis.py tests/test_fiis_ui.py
git commit -m "feat(fii): tela exibe o DY recorrente que decide e o divulgado ao lado"
```

---

### Task 7: Contexto e prompt da LLM

**Files:**
- Modify: `core/llm_context_fii.py:10-21`, `core/llm_fii.py:30+`
- Test: `tests/test_llm_fii.py`, `tests/test_fii_cenario_macro.py`

**Interfaces:**
- Consumes: `dy_recorrente` (Task 4), `protecao_nao_divulgada` (Task 2).
- Produces: nenhum consumidor a jusante.

- [ ] **Step 1: Write the failing test**

Acrescente a `tests/test_llm_fii.py`:

```python
def test_contexto_entrega_o_yield_recorrente_e_declara_a_omissao():
    from core.llm_context_fii import _DETAIL_METRICS

    assert "dy_recorrente" in _DETAIL_METRICS
    assert "tenant_concentration" in _DETAIL_METRICS
    assert "lease_expiry_concentration_24m" in _DETAIL_METRICS


def test_prompt_exige_declarar_protecao_desconhecida(monkeypatch):
    import core.llm_fii as llm_fii

    capturado = {}

    def _captura(messages, **kwargs):
        capturado["mensagens"] = messages
        return "ok"

    monkeypatch.setattr(llm_fii, "_chat_complete", _captura)
    llm_fii.chat_com_fiis("CONTEXTO", [], "pergunta")
    system = capturado["mensagens"][0]["content"].lower()
    assert "não divulgado" in system
    assert "renda recorrente" in system
```

- [ ] **Step 2: Run test to verify it fails**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_llm_fii.py -k "recorrente or protecao" -v
```

Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

Em `core/llm_context_fii.py`, acrescente `"dy_recorrente"` como primeira entrada de `_DETAIL_METRICS`. Onde as métricas de detalhe são formatadas, para linhas de tijolo/híbrido cuja chave esteja em `protecao_nao_divulgada(row)`, emita `não divulgado pelo gestor` em vez de `ausente` — a distinção importa: `ausente` sugere falha nossa, `não divulgado` é fato sobre o fundo.

Em `core/llm_fii.py`, após a regra 10, acrescente:

```python
        "11. A renda que decide é a recorrente. Ao citar rendimento, use dy_recorrente "
        "(dy_12m × income_recurrence) e diga o DY divulgado ao lado, explicando a "
        "diferença quando ela for material. Nunca apresente o DY divulgado sozinho como "
        "se fosse renda sustentável.\n"
        "12. Evidência de proteção marcada como 'não divulgado pelo gestor' é fato sobre "
        "o fundo, não lacuna do sistema. Ao recomendar um fundo assim, diga qual proteção "
        "não foi divulgada e o que ela mudaria na conclusão. Nunca trate ausência de "
        "divulgação como ausência de risco.\n\n"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest tests/test_llm_fii.py tests/test_fii_cenario_macro.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/llm_context_fii.py core/llm_fii.py tests/
git commit -m "feat(fii): prompt decide pela renda recorrente e declara omissao do gestor"
```

---

### Task 8: Suíte completa, safra PIT e republicação

Esta tarefa não é pendência: sem ela a página se declara Lista de Diligência, porque a validação PIT da 6.8.0 deixou de valer com a mudança de `METHODOLOGY_VERSION`.

**Files:**
- Nenhum arquivo de código. Execução e verificação.

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: safra PIT da 6.9.0 e vitrine republicada.

- [ ] **Step 1: Suíte completa**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m pytest -q
```

Expected: nenhuma falha. A linha de base antes deste trabalho era 4713 passed, 4 skipped.

- [ ] **Step 2: Subir o armazém local**

```bash
docker start dfu_warehouse
```

Autorização permanente já registrada: armazém parado trava as três publicações de uma vez.

- [ ] **Step 3: Reprocessar os documentos com o extrator ampliado e medir o WAULT**

Rode a ingestão de documentos de FII e repita a medição do Step 5 da Task 1. Registre a cobertura de `wault_anos` em tijolo/híbrido. **Se ficar abaixo de 40%, o piso de prazo de locação não entra** — registre a medição no commit e trate como ciclo seguinte. Se passar de 40%, traga a distribuição para escolher a régua antes de implementar.

- [ ] **Step 4: Walk-forward PIT da 6.9.0**

Rode o validador point-in-time da metodologia de FIIs e confirme que a safra gravada traz `methodology_version = "6.9.0"`. Sem safra correspondente, `load_fii_validation_status(METHODOLOGY_VERSION)` devolve vazio e a página cai para Lista de Diligência.

- [ ] **Step 5: Republicar a vitrine**

```bash
"$LOCALAPPDATA/Programs/Python/Python312/python.exe" scripts/publish_fii_selection_from_local.py
```

Confirme o universo elegível resultante: esperado 98 fundos (50 papel, 23 tijolo, 22 FoF, 2 híbrido). Divergência maior que 5 fundos significa que alguma regra não está lendo o que se supõe — investigar antes de seguir.

- [ ] **Step 6: Confirmar que a carteira continua sendo construída**

Na página, gere a carteira de diligência nos regimes extremos — Selic alta e
`easing`, que é o de piso de tijolo mais alto (40%). Confirme que sai carteira
com 12 ativos nos dois, que a banda de tijolo é respeitada, e que
`portfolio_constraint_violations` volta vazia. Se aparecer nota de afrouxamento,
ela é resultado válido: registre qual tipo cedeu e por quanto.

Carteira vazia aqui é falha de aceitação da entrega, não um "universo apertado":
o reforço da proteção não pode inviabilizar a criação do portfólio.

- [ ] **Step 7: Verificar na aplicação publicada**

Faça à LLM a mesma pergunta que originou este trabalho (por que um fundo negocia com desconto patrimonial) e confirme que a resposta cita o DY recorrente, não o divulgado sozinho, e que nenhuma proteção desconhecida é apresentada como ausência de risco.

- [ ] **Step 8: Commit, PR e merge**

```bash
git add -A
git commit -m "chore(fii): safra PIT 6.9.0 e vitrine republicada"
gh pr create --fill
```

Merge na main assim que a suíte passar: a Streamlit Cloud publica da main, e a rotina noturna do snapshot recusa enquanto o branch de trabalho estiver em uso.

---

## Notas de revisão

**Cobertura do spec.** As sete seções do spec têm tarefa correspondente: seção 1 → Task 2; seção 2 → Task 3; seção 3 → Task 4; seção 4 → Task 5; seção 5 → Tasks 6 e 7; seção 6 → Tasks 3, 4 e 8; seção 7 → Tasks 1 e 8.

**Um acréscimo ao spec.** A Task 5 acrescenta `recurrent_yield_12m` ao resumo da carteira. O spec cobria tabela e prompt, mas `optimize_diligence_portfolio` reporta `trailing_yield_12m` e `expected_yield` a partir de `dy_12m` cru — deixá-los intactos publicaria, no resumo da carteira, exatamente o yield inflado que o resto do trabalho combate. O DY divulgado continua sendo reportado ao lado.

**Ordem das tarefas.** A Task 1 vem primeiro porque a decisão sobre o prazo de locação depende da medição, e a medição depende do extrator ampliado. Ela é independente das Tasks 2-7 e pode ser executada em paralelo, se preferir.
