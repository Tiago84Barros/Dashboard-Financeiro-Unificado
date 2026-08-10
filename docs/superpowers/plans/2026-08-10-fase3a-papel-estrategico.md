# Fase 3a — Papel estratégico de cada ativo: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dizer, para cada ativo do patrimônio, que papel ele cumpre — renda, crescimento, diversificação, proteção, redução de volatilidade, hedge cambial — sempre acompanhado do número que justificou a classificação.

**Architecture:** Um classificador determinístico por regras em `core/global_portfolio/roles.py`, puro, que recebe o quadro de posições e os insumos estatísticos já calculados pela Fase 2b. A view apenas formata. Nenhum papel é atribuído sem o número que o sustenta, e ativo sem dado suficiente é declarado indeterminado em vez de receber um rótulo por omissão.

**Tech Stack:** Python 3.12, pandas, Streamlit, pytest.

## Global Constraints

- **Aditividade:** as únicas alterações em arquivo pré-existente são acréscimos ao final de `core/global_portfolio/correlation.py` (Task 1) e os painéis em `views/portfolio_global.py` (Task 3).
- **Camada pura:** `core/global_portfolio/*` não executa SQL, não importa Streamlit e não faz I/O. A view não calcula.
- **Todo papel carrega seu número.** Um papel sem a evidência numérica que o justificou é um rótulo, não uma classificação. A estrutura de saída obriga isso.
- **Heurística é declarada como heurística.** Os limiares são escolhas, não fatos sobre o ativo; ficam em constantes nomeadas no topo do módulo e a interface diz que são ajustáveis.
- **Cobertura explícita.** Volatilidade e correlação existem para ~62% do patrimônio (só `b3` e `fii` têm série mensal). Papéis que dependem delas são marcados como indeterminados para os demais, nunca negados por omissão — "não sabemos" e "não cumpre" são coisas diferentes.
- **Determinismo:** nenhuma saída depende de ordem de iteração de `dict`/`set`.
- **Idioma:** comentários, docstrings e textos de interface em português.
- **Interpretador:** o `python` do PATH não tem pytest. Usar sempre
  `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest ...`
- **Baseline da suíte:** `1749 passed, 3 skipped, 0 failed`.
- **Cards via `design.componentes.card_metrica`** — não escrever HTML de card, não criar `_kpi_html` (um teste existente afirma que esse nome não aparece na view).

---

## O que existe e será consumido

Verificado contra o banco de produção, não presumido:

- `payload["fundamentals"]` — b3: `DY`, `P/L`, `P/VP`, `ROE`, `Margem_Liquida`, `Margem_Operacional`, `Endividamento_Total`, `Liquidez_Corrente`, `EV_EBIT`, `Payout`. fii: `dy_12m`, `pvp`, `preco`, `vpa`, `patrimonio_liquido`, `liquidez_diaria`, `num_cotistas`, `tipo_gestao`. us: `pe`, `roe`, `_market_cap` e os scores.
- `payload["history"]["multiplos_anuais"]` — 16 anos, cada um com as chaves de `fundamentals` mais `Data`.
- `payload["history"]["demonstracoes_anuais"]` — 16 anos com `EBIT`, `EBITDA`, `FCF`, `FCO`, `LPA`, `Dividendos`, `Ativo_Total`, `Divida_Liquida`, `Caixa`, `Divida_Total`, `FCI`.
- `payload["classification"]["composition"]` — só fii: `pct_imoveis`, `pct_papel`, `pct_caixa`, `pct_fundos`.
- `core.global_portfolio.returns.retornos_mensais(df) -> tuple[pd.DataFrame, Cobertura]`.
- `core.global_portfolio.correlation.matriz(retornos) -> pd.DataFrame`.
- `montar_posicoes` entrega as colunas `asset_class`, `symbol`, `name`, `sector`, `currency`, `country`, `weight_global`, `payload`.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `core/global_portfolio/correlation.py` (modificar) | +`correlacao_media_por_ativo` |
| `core/global_portfolio/roles.py` | Classificador de papel + evidência |
| `views/portfolio_global.py` (modificar) | Painel "Papel estratégico" |

---

### Task 1: Correlação média por ativo

**Files:**
- Modify: `core/global_portfolio/correlation.py` (acrescentar ao final)
- Test: `tests/test_global_correlation.py` (acrescentar casos)

**Interfaces:**
- Consumes: `matriz(retornos) -> pd.DataFrame`, já existente no módulo.
- Produces: `correlacao_media_por_ativo(retornos: pd.DataFrame) -> dict[str, float]` — símbolo → correlação média com os demais, excluindo a diagonal. Símbolo cuja linha seja inteiramente `NaN` fica de fora do dicionário em vez de virar `0.0`.

Um ativo cuja correlação média com o resto é baixa está diversificando de fato; é essa a evidência do papel "diversificação" na Task 2. Sem esta função, cada consumidor reduziria a matriz por conta própria.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_correlacao_media_por_ativo_exclui_a_diagonal():
    import numpy as np
    import pandas as pd
    from core.global_portfolio.correlation import correlacao_media_por_ativo

    # A e B andam juntos; C anda sozinho.
    n = 60
    base = np.sin(np.arange(n) / 3.0)
    ret = pd.DataFrame({
        "A": base,
        "B": base * 1.01,
        "C": np.cos(np.arange(n) / 7.0),
    }, index=pd.date_range("2020-01-31", periods=n, freq="ME"))

    saida = correlacao_media_por_ativo(ret)
    assert set(saida) == {"A", "B", "C"}
    # A diagonal (1.0) nao pode entrar: se entrasse, todo valor subiria.
    assert saida["A"] < 1.0
    assert saida["A"] > saida["C"], "A anda com B; C nao anda com ninguem"


def test_correlacao_media_por_ativo_ignora_ativo_sem_par():
    import numpy as np
    import pandas as pd
    from core.global_portfolio.correlation import correlacao_media_por_ativo

    n = 60
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    ret = pd.DataFrame({
        "A": np.sin(np.arange(n) / 3.0),
        "B": np.cos(np.arange(n) / 5.0),
        "SEMPAR": [np.nan] * n,
    }, index=idx)

    saida = correlacao_media_por_ativo(ret)
    assert "SEMPAR" not in saida, "linha toda NaN nao pode virar 0.0"
    assert set(saida) == {"A", "B"}


def test_correlacao_media_por_ativo_com_um_ativo_devolve_vazio():
    import numpy as np
    import pandas as pd
    from core.global_portfolio.correlation import correlacao_media_por_ativo

    ret = pd.DataFrame({"A": np.arange(60.0)},
                       index=pd.date_range("2020-01-31", periods=60, freq="ME"))
    assert correlacao_media_por_ativo(ret) == {}


def test_correlacao_media_por_ativo_com_frame_vazio_devolve_vazio():
    import pandas as pd
    from core.global_portfolio.correlation import correlacao_media_por_ativo
    assert correlacao_media_por_ativo(pd.DataFrame()) == {}
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_correlation.py -k correlacao_media_por_ativo -v`
Expected: FAIL com `ImportError: cannot import name 'correlacao_media_por_ativo'`

- [ ] **Step 3: Acrescentar ao final de `core/global_portfolio/correlation.py`**

```python
def correlacao_media_por_ativo(retornos: pd.DataFrame) -> dict[str, float]:
    """Correlacao media de cada ativo com os DEMAIS, sem a diagonal.

    Correlacao media baixa e a evidencia de que o ativo diversifica de fato,
    e nao apenas de que tem nome diferente. A diagonal fica de fora porque
    1.0 contra si mesmo inflaria todo mundo igualmente.

    Ativo cuja linha nao tem nenhum par valido fica ausente do resultado —
    devolver 0.0 diria "nao se correlaciona com nada", que e o oposto de
    "nao sabemos".
    """
    m = matriz(retornos)
    if m.empty or len(m.columns) < 2:
        return {}

    saida: dict[str, float] = {}
    for simbolo in sorted(m.columns):
        outros = m.loc[simbolo, [c for c in m.columns if c != simbolo]]
        media = pd.to_numeric(outros, errors="coerce").mean()
        if pd.notna(media):
            saida[str(simbolo)] = float(media)
    return saida
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_correlation.py -v`
Expected: todos passam (12 antigos + 4 novos).

- [ ] **Step 5: Commit**

```bash
git add core/global_portfolio/correlation.py tests/test_global_correlation.py
git commit -m "feat(global): correlacao media por ativo para medir diversificacao real"
```

---

### Task 2: O classificador de papel

**Files:**
- Create: `core/global_portfolio/roles.py`
- Test: `tests/test_global_roles.py`

**Interfaces:**
- Consumes: `core.global_portfolio.fields.valor` (aliasado como `campo_valor`); `correlacao_media_por_ativo` (Task 1).
- Produces:
  - `PAPEIS: tuple[str, ...]` — chaves ordenadas.
  - `ROTULOS_PAPEL: dict[str, str]` — chave → rótulo em português.
  - `LIMIARES: dict[str, float]` — os limiares heurísticos, nomeados e num só lugar.
  - `Evidencia` — dataclass congelada: `papel: str`, `valor: float`, `referencia: float`, `texto: str`.
  - `PapelDoAtivo` — dataclass congelada: `symbol: str`, `papeis: tuple[str, ...]`, `evidencias: tuple[Evidencia, ...]`, `indeterminados: tuple[str, ...]`, `justificativa: str`.
  - `classificar(df_posicoes, *, retornos=None, correlacoes=None) -> list[PapelDoAtivo]` — uma entrada por ativo, na ordem do quadro.

**As regras, com o dado que cada uma usa.** Cada papel só é atribuído se o dado existir; faltando o dado, o papel entra em `indeterminados`, nunca é simplesmente negado.

| Papel | Regra | Origem |
|---|---|---|
| `renda` | DY ≥ mediana da classe **e** payout com desvio-padrão relativo < `LIMIARES["payout_instavel"]` nos últimos 5 anos | `campo_valor(.., "dy")`, `history.multiplos_anuais[*]["Payout"]` |
| `crescimento` | CAGR de LPA nos últimos 5 anos ≥ `LIMIARES["cagr_minimo"]` | `history.demonstracoes_anuais[*]["LPA"]` |
| `baixa_volatilidade` | volatilidade anualizada do ativo < `LIMIARES["vol_baixa"]` | `retornos[symbol].std() * sqrt(12)` |
| `diversificacao` | correlação média com os demais < `LIMIARES["correlacao_baixa"]` | `correlacoes[symbol]` |
| `hedge_cambial` | `currency != "BRL"` | coluna `currency` |
| `protecao_inflacao` | FII com `pct_papel` ≥ `LIMIARES["papel_dominante"]` **ou** setor canônico em `{"utilities", "real_estate"}` | `classification.composition`, coluna `sector` |
| `reserva_valor` | FII com `pct_imoveis` ≥ `LIMIARES["tijolo_dominante"]` | `classification.composition` |

`justificativa` é uma frase montada a partir das evidências — não texto livre. Ativo sem papel algum recebe a justificativa que diz isso explicitamente, porque é o sinal mais acionável do painel.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Classificador de papel estrategico por ativo."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.roles import (
    LIMIARES,
    PAPEIS,
    ROTULOS_PAPEL,
    classificar,
)


def _linha(symbol, classe="b3", peso=0.1, currency="BRL", sector="financials",
           fundamentals=None, history=None, classification=None):
    return {
        "asset_class": classe, "symbol": symbol, "name": symbol,
        "sector": sector, "currency": currency, "weight_global": peso,
        "payload": {
            "fundamentals": fundamentals or {},
            "history": history or {},
            "classification": classification or {},
        },
    }


def test_todo_papel_tem_rotulo():
    assert set(ROTULOS_PAPEL) == set(PAPEIS)


def test_papeis_sao_deterministicos():
    assert PAPEIS == tuple(sorted(PAPEIS))


def test_renda_exige_dy_alto_e_payout_estavel():
    payout_estavel = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("ALTO", fundamentals={"DY": 9.0}, history=payout_estavel),
        _linha("BAIXO", fundamentals={"DY": 1.0}, history=payout_estavel),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "renda" in saida["ALTO"].papeis
    assert "renda" not in saida["BAIXO"].papeis


def test_renda_recusa_payout_erratico():
    erratico = {"multiplos_anuais": [{"Payout": v} for v in (5.0, 90.0, 10.0, 80.0, 15.0)]}
    estavel = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("ERRATICO", fundamentals={"DY": 9.0}, history=erratico),
        _linha("ESTAVEL", fundamentals={"DY": 9.0}, history=estavel),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "renda" not in saida["ERRATICO"].papeis
    assert "renda" in saida["ESTAVEL"].papeis


def test_crescimento_usa_cagr_de_lpa():
    crescendo = {"demonstracoes_anuais": [{"LPA": v} for v in (1.0, 1.3, 1.7, 2.2, 2.9)]}
    parado = {"demonstracoes_anuais": [{"LPA": 1.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("CRESCE", history=crescendo),
        _linha("PARADO", history=parado),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "crescimento" in saida["CRESCE"].papeis
    assert "crescimento" not in saida["PARADO"].papeis


def test_hedge_cambial_vem_da_moeda():
    df = pd.DataFrame([
        _linha("AAPL", classe="us", currency="USD"),
        _linha("PETR4", currency="BRL"),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "hedge_cambial" in saida["AAPL"].papeis
    assert "hedge_cambial" not in saida["PETR4"].papeis


def test_protecao_inflacao_por_fii_de_papel_ou_por_setor():
    df = pd.DataFrame([
        _linha("KNCR11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_papel": 94.0, "pct_imoveis": 0.0}}),
        _linha("SBSP3", sector="utilities"),
        _linha("LEVE3", sector="consumer"),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "protecao_inflacao" in saida["KNCR11"].papeis
    assert "protecao_inflacao" in saida["SBSP3"].papeis
    assert "protecao_inflacao" not in saida["LEVE3"].papeis


def test_reserva_valor_e_fii_de_tijolo():
    df = pd.DataFrame([
        _linha("HGLG11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_imoveis": 96.0, "pct_papel": 0.0}}),
        _linha("KNCR11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_imoveis": 0.0, "pct_papel": 94.0}}),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "reserva_valor" in saida["HGLG11"].papeis
    assert "reserva_valor" not in saida["KNCR11"].papeis


def test_baixa_volatilidade_e_diversificacao_vem_da_serie():
    n = 60
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    quieto = np.full(n, 0.001)
    agitado = np.tile([0.20, -0.18], n // 2)
    ret = pd.DataFrame({"QUIETO": quieto, "AGITADO": agitado}, index=idx)
    df = pd.DataFrame([_linha("QUIETO"), _linha("AGITADO")])

    saida = {p.symbol: p for p in classificar(
        df, retornos=ret, correlacoes={"QUIETO": 0.05, "AGITADO": 0.85})}
    assert "baixa_volatilidade" in saida["QUIETO"].papeis
    assert "baixa_volatilidade" not in saida["AGITADO"].papeis
    assert "diversificacao" in saida["QUIETO"].papeis
    assert "diversificacao" not in saida["AGITADO"].papeis


def test_sem_serie_o_papel_fica_indeterminado_e_nao_negado():
    df = pd.DataFrame([_linha("SEMSERIE")])
    p = classificar(df, retornos=None, correlacoes=None)[0]
    assert "baixa_volatilidade" in p.indeterminados
    assert "diversificacao" in p.indeterminados
    assert "baixa_volatilidade" not in p.papeis


def test_toda_evidencia_acompanha_o_papel_que_a_gerou():
    payout = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([_linha("X", fundamentals={"DY": 9.0}, history=payout)])
    p = classificar(df)[0]
    papeis_com_evidencia = {e.papel for e in p.evidencias}
    assert set(p.papeis) <= papeis_com_evidencia, "papel sem numero e rotulo, nao classificacao"
    for e in p.evidencias:
        assert e.texto, "evidencia precisa de texto legivel"


def test_ativo_sem_papel_algum_e_declarado_explicitamente():
    df = pd.DataFrame([_linha("NADA", fundamentals={"DY": 0.5})])
    p = classificar(df)[0]
    assert p.papeis == ()
    assert "nenhum papel" in p.justificativa.lower()


def test_ordem_da_saida_segue_o_quadro():
    df = pd.DataFrame([_linha("B"), _linha("A"), _linha("C")])
    assert [p.symbol for p in classificar(df)] == ["B", "A", "C"]


def test_quadro_vazio_devolve_lista_vazia():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "name", "sector",
                                  "currency", "weight_global", "payload"])
    assert classificar(vazio) == []


def test_limiares_estao_num_so_lugar():
    for chave in ("payout_instavel", "cagr_minimo", "vol_baixa",
                  "correlacao_baixa", "papel_dominante", "tijolo_dominante"):
        assert chave in LIMIARES
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_roles.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'core.global_portfolio.roles'`

- [ ] **Step 3: Escrever `core/global_portfolio/roles.py`**

O implementador escreve o módulo satisfazendo os testes acima e as regras da tabela. Pontos obrigatórios do desenho, que os testes cobrem mas que valem estar explícitos no código:

- `LIMIARES` no topo, com um comentário dizendo que são escolhas heurísticas e não fatos sobre os ativos.
- A mediana do DY é calculada **dentro da classe** do ativo, não sobre o patrimônio inteiro: comparar o DY de um FII com o de uma ação de crescimento não mede nada.
- Estabilidade de payout medida por desvio-padrão relativo (desvio ÷ média) sobre os últimos 5 anos disponíveis; menos de 3 anos de payout torna o papel `renda` indeterminado, não negado.
- CAGR de LPA calculado sobre os últimos 5 anos disponíveis, exigindo primeiro e último valores positivos — CAGR com base negativa não tem significado e deve tornar o papel indeterminado.
- Volatilidade anualizada por `√12` a partir dos retornos mensais.
- Cada papel atribuído gera uma `Evidencia` com `valor` (o do ativo), `referencia` (o limiar ou a mediana usada) e `texto` legível em português.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_roles.py -v`
Expected: 14 passed

- [ ] **Step 5: Provar contra o banco de produção**

```
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -c "
import sys, io; sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from core.portfolio.repository import load_active_snapshots, load_allocation_targets
from core.portfolio.registry import asset_classes
from core.global_portfolio.aggregate import montar_posicoes
from core.global_portfolio import returns as R, correlation as C, roles as P
snaps = {c: load_active_snapshots(c) for c in asset_classes()}
a = load_allocation_targets()
df = montar_posicoes(snaps, a['targets'], total_brl=a['total_brl'])
ret, cob = R.retornos_mensais(df)
cors = C.correlacao_media_por_ativo(ret)
for p in P.classificar(df, retornos=ret, correlacoes=cors)[:12]:
    print('%-8s %-40s %s' % (p.symbol, ','.join(p.papeis) or '(nenhum)', ','.join(p.indeterminados) or ''))
"
```

Colar a saída real no relatório. Um classificador cujos rótulos nunca foram vistos contra a carteira real não está pronto.

- [ ] **Step 6: Commit**

```bash
git add core/global_portfolio/roles.py tests/test_global_roles.py
git commit -m "feat(global): papel estrategico por ativo com evidencia numerica"
```

---

### Task 3: Painel "Papel estratégico"

**Files:**
- Modify: `views/portfolio_global.py`
- Test: `tests/test_portfolio_global_view.py`

**Interfaces:**
- Consumes: `roles.classificar`, `roles.ROTULOS_PAPEL`, `correlation.correlacao_media_por_ativo`, e o `ret` que `render()` já obtém de `retornos_mensais`.
- Produces: `_painel_papeis(df, ret)` e os helpers puros que a decisão exigir.

O painel mostra, por ativo e em ordem de peso: os papéis com seus rótulos, a evidência de cada um, e a justificativa. Dois agregados no topo, em `card_metrica`: quantos ativos cumprem cada papel, e **quantos não cumprem nenhum** — este último é o número acionável da tela.

Regras de honestidade, todas já estabelecidas nas fases anteriores e verificadas por teste:
- Papel indeterminado por falta de série aparece como indeterminado, visualmente distinto de "não cumpre".
- O painel diz que os limiares são heurísticos e mostra quais são.
- Ativo sem papel algum é destacado, não escondido no fim de uma tabela.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_o_painel_de_papeis_e_chamado_no_render():
    """Regressao: motor que ninguem consulta e decoracao."""
    import ast, inspect, textwrap
    import views.portfolio_global as v

    fonte = textwrap.dedent(inspect.getsource(v.render))
    arvore = ast.parse(fonte)
    chamadas = {n.func.id for n in ast.walk(arvore)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_painel_papeis" in chamadas


def test_resumo_de_papeis_conta_os_sem_papel_algum():
    from views.portfolio_global import _resumo_de_papeis
    from core.global_portfolio.roles import PapelDoAtivo

    entradas = [
        PapelDoAtivo("A", ("renda",), (), (), "x"),
        PapelDoAtivo("B", (), (), (), "y"),
        PapelDoAtivo("C", (), (), (), "z"),
    ]
    resumo = _resumo_de_papeis(entradas)
    assert resumo["sem_papel"] == 2
    assert resumo["por_papel"]["renda"] == 1


def test_resumo_de_papeis_com_lista_vazia_nao_quebra():
    from views.portfolio_global import _resumo_de_papeis
    resumo = _resumo_de_papeis([])
    assert resumo["sem_papel"] == 0
    assert resumo["por_papel"] == {}
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_global_view.py -k papel -v`
Expected: FAIL — `_painel_papeis` e `_resumo_de_papeis` não existem.

- [ ] **Step 3: Implementar o painel e o resumo**

`_resumo_de_papeis(entradas) -> dict` é puro e devolve `{"por_papel": {chave: contagem}, "sem_papel": int}`, com `por_papel` ordenado. `_painel_papeis(df, ret)` renderiza; `render()` o chama depois do painel de risco.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_global_view.py -v`

- [ ] **Step 5: Suíte completa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/ -q --tb=short`
Expected: `1749 + os novos`, zero falhas.

- [ ] **Step 6: Commit**

```bash
git add views/portfolio_global.py tests/test_portfolio_global_view.py
git commit -m "feat(global): painel de papel estrategico por ativo"
```

---

## Auto-revisão deste plano

**Cobertura da spec §7:** os sete papéis que a spec lista estão na tabela da Task 2 — geração de renda, crescimento, diversificação, proteção, reserva de valor, redução de volatilidade, hedge cambial. "Proteção" da spec vira `protecao_inflacao`, que é a proteção que o dado sustenta; proteção contra recessão depende de cenário macro e é Fase 4. A exigência de que "cada ativo possua justificativa clara para permanecer" é o campo `justificativa`, e o caso sem papel algum é testado explicitamente.

**Consistência de nomes:** `PapelDoAtivo` tem `symbol`, `papeis`, `evidencias`, `indeterminados`, `justificativa` na Task 2 e é construído com essa mesma ordem posicional no teste da Task 3. `classificar(df, *, retornos, correlacoes)` é chamada com esses nomes na Task 3 e na prova de produção. `correlacao_media_por_ativo` (Task 1) é consumida na Task 2 e na Task 3.

**Limite conhecido, registrado:** volatilidade e correlação cobrem ~62% do patrimônio, então `baixa_volatilidade` e `diversificacao` são indetermináveis para os ativos americanos enquanto `returns._CLASSES_COM_PRECO` não incluir `us`. O plano trata isso como indeterminado, não como ausência de papel — mas a limitação é de dado e só some quando a série americana entrar.
