# Lacuna: histórico anual dos FIIs — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o snapshot de FII persistir a série histórica que já existe no armazém, para que os papéis `renda` e `crescimento` deixem de ser indetermináveis para 30,8% do patrimônio.

**Architecture:** O adaptador de FII passa a preencher o bloco `history`, hoje vazio, a partir de `market.fii_metrics_monthly` e `market.dividends`. O classificador de papel passa a ler essa série para FIIs, com a janela real declarada. Nenhum dado novo é ingerido — é dado que já está no banco e não estava sendo carregado.

**Tech Stack:** Python 3.12, pandas, SQLAlchemy, pytest.

## Global Constraints

- **Aditividade:** as únicas alterações em arquivo pré-existente são o preenchimento do bloco `history` em `core/portfolio/adapters/fii.py` e as regras de FII em `core/global_portfolio/roles.py`. Nenhuma lógica existente é removida.
- **Camada:** `core/global_portfolio/*` não executa SQL, não importa Streamlit e não faz I/O. O adaptador lê por meio do dicionário `loaders` injetável, como já faz.
- **A janela real é declarada.** `market.fii_metrics_monthly` cobre 2024-01 a 2026-05 — 29 meses. Não fingir cinco anos: onde a regra pedir cinco anos e houver 29 meses, ou a janela encolhe e é dita, ou o papel continua indeterminado. As duas saídas são honestas; inventar CAGR de cinco anos sobre dois e meio não é.
- **Determinismo:** nenhuma saída depende de ordem de iteração de `dict`/`set`.
- **Teto de payload:** `MAX_PAYLOAD_BYTES = 120_000` por ativo, já aplicado por `build_payload`. A série de 29 meses × 9 campos mais proventos anuais fica em torno de 4 KB — folgado, mas o teto continua valendo e trunca com aviso se algum FII tiver série muito maior.
- **Idioma:** comentários e docstrings em português.
- **Interpretador:** `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest ...`
- **Baseline da suíte:** `1773 passed, 3 skipped, 0 failed`.

---

## O que existe e será consumido

Verificado contra o banco de produção:

- `core.market_read.load_fii_metrics_mensal(ticker: str) -> pd.DataFrame` — decorada com `@st.cache_data`. Colunas: `Data`, `VPA`, `P/VP`, `Patrimonio`, `Cotistas`, `DY_Patrimonial`, `Pct_Imoveis`, `Pct_Papel`, `Pct_Caixa`, `Pct_Fundos`. Cobre **11 de 11** FIIs da carteira, 29 meses cada.
- `market.dividends` cobre **11 de 11**. O padrão de leitura anual por lote já existe em `load_demonstracoes_batch`: `SELECT ticker, EXTRACT(YEAR FROM event_date)::int AS y, SUM(amount) AS d ... GROUP BY 1, 2`.
- `core/portfolio/adapters/fii.py` já tem o dicionário `loaders` injetável e hoje escreve `"history": {}`.
- `core/portfolio/adapters/_frames.py::registros(frame) -> list[dict]` converte DataFrame em registros com `NaN → None`.
- `core/global_portfolio/roles.py` — `LIMIARES`, `classificar`, e as regras de `renda` e `crescimento` que hoje ficam indeterminadas para FII por ausência de histórico.

---

### Task 1: O adaptador de FII passa a persistir histórico

**Files:**
- Modify: `core/portfolio/adapters/fii.py`
- Test: `tests/test_portfolio_adapter_fii.py`

**Interfaces:**
- Consumes: `core.market_read.load_fii_metrics_mensal`; `_frames.registros`.
- Produces: no payload de FII, `history` deixa de ser `{}` e passa a conter:
  - `metricas_mensais`: lista de registros da série mensal, em ordem cronológica.
  - `proventos_anuais`: lista de `{"ano": int, "total": float}`, em ordem crescente de ano.

O dicionário `loaders` ganha duas chaves novas: `"metricas_mensais"` (função `tuple[str, ...] -> dict[str, pd.DataFrame]`) e `"proventos"` (função `tuple[str, ...] -> dict[int, float]` por ticker). Ambas com implementação padrão em `_default_loaders`, injetáveis nos testes — o padrão que o adaptador já usa e que mantém os testes sem banco.

**Nota de latência:** `load_fii_metrics_mensal` é por ticker e não tem versão em lote. Para 11 FIIs são 11 consultas, cada uma cacheada por uma hora. O gancho de captura já roda fora da transação da carteira e já custa segundos; isso soma pouco. Não criar uma versão em lote em `market_read.py` — seria alterar arquivo existente sem necessidade.

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_history_do_fii_traz_a_serie_mensal_e_os_proventos():
    import datetime as dt
    import pandas as pd
    from core.portfolio.adapters.fii import build_snapshots

    serie = pd.DataFrame({
        "Data": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
        "VPA": [100.0, 101.0, 102.0],
        "P/VP": [0.95, 0.97, 0.99],
        "Patrimonio": [1.0e9, 1.1e9, 1.2e9],
        "Cotistas": [1000, 1100, 1200],
        "DY_Patrimonial": [0.008, 0.0082, 0.0079],
        "Pct_Imoveis": [0.96, 0.96, 0.95],
        "Pct_Papel": [0.0, 0.0, 0.0],
        "Pct_Caixa": [0.04, 0.04, 0.05],
        "Pct_Fundos": [0.0, 0.0, 0.0],
    })
    loaders = {
        "fiis": lambda: pd.DataFrame({"Ticker": ["HGLG11"], "Nome": ["CSHG"],
                                      "Segmento": ["Logistica"], "Tipo": ["Tijolo"],
                                      "P/VP": [0.95], "DY_12m": [8.4]}),
        "metricas_mensais": lambda tks: {"HGLG11": serie},
        "proventos": lambda tks: {"HGLG11": {2024: 11.5, 2025: 12.1}},
    }
    snaps = build_snapshots([{"ticker": "HGLG11", "peso": 1.0}], model_id="m1",
                            params={}, as_of=dt.date(2026, 8, 10), loaders=loaders)
    hist = snaps[0].payload["history"]

    assert len(hist["metricas_mensais"]) == 3
    assert hist["metricas_mensais"][-1]["VPA"] == 102.0
    assert hist["proventos_anuais"] == [{"ano": 2024, "total": 11.5},
                                        {"ano": 2025, "total": 12.1}]


def test_fii_sem_serie_mensal_mantem_history_com_listas_vazias():
    import datetime as dt
    import pandas as pd
    from core.portfolio.adapters.fii import build_snapshots

    loaders = {
        "fiis": lambda: pd.DataFrame({"Ticker": ["XXXX11"], "Nome": ["Sem serie"]}),
        "metricas_mensais": lambda tks: {},
        "proventos": lambda tks: {},
    }
    snaps = build_snapshots([{"ticker": "XXXX11", "peso": 1.0}], model_id="m1",
                            params={}, as_of=dt.date(2026, 8, 10), loaders=loaders)
    hist = snaps[0].payload["history"]
    assert hist["metricas_mensais"] == []
    assert hist["proventos_anuais"] == []


def test_proventos_saem_ordenados_por_ano():
    import datetime as dt
    import pandas as pd
    from core.portfolio.adapters.fii import build_snapshots

    loaders = {
        "fiis": lambda: pd.DataFrame({"Ticker": ["A11"], "Nome": ["A"]}),
        "metricas_mensais": lambda tks: {},
        "proventos": lambda tks: {"A11": {2026: 3.0, 2024: 1.0, 2025: 2.0}},
    }
    snaps = build_snapshots([{"ticker": "A11", "peso": 1.0}], model_id="m1",
                            params={}, as_of=dt.date(2026, 8, 10), loaders=loaders)
    anos = [r["ano"] for r in snaps[0].payload["history"]["proventos_anuais"]]
    assert anos == sorted(anos), "ordem crescente e determinismo"


def test_loaders_padrao_expõem_as_duas_chaves_novas():
    from core.portfolio.adapters.fii import _default_loaders
    d = _default_loaders()
    assert "metricas_mensais" in d
    assert "proventos" in d
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_adapter_fii.py -v`
Expected: FAIL — `history` vem `{}` e as chaves novas não existem.

- [ ] **Step 3: Implementar**

Em `core/portfolio/adapters/fii.py`: acrescentar as duas chaves a `_default_loaders`, e preencher `history` com `metricas_mensais` (via `registros`) e `proventos_anuais`. O loader padrão de proventos usa o mesmo SQL agregado por ano que `load_demonstracoes_batch` já emprega; o de métricas mensais chama `load_fii_metrics_mensal` por ticker e devolve o dicionário.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_portfolio_adapter_fii.py -v`

- [ ] **Step 5: Provar contra o banco**

```
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -c "
import sys, io, datetime as dt; sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from core.portfolio.adapters.fii import build_snapshots
itens = [{'ticker': t, 'peso': 1.0/11} for t in
         ['KNHY11','RZTR11','MANA11','GGRC11','KORE11','XPLG11','VGIR11','SNEL11','KNHF11','VGHF11','LIFE11']]
for s in build_snapshots(itens, model_id='m', params={}, as_of=dt.date.today()):
    h = s.payload['history']
    print('%-8s meses=%-3d proventos=%-2d' % (s.symbol, len(h['metricas_mensais']), len(h['proventos_anuais'])))
"
```

Colar a saída. Esperado: 29 meses para os 11.

- [ ] **Step 6: Commit**

```bash
git add core/portfolio/adapters/fii.py tests/test_portfolio_adapter_fii.py
git commit -m "feat(portfolio): snapshot de FII passa a persistir serie mensal e proventos"
```

---

### Task 2: O classificador usa o histórico de FII

**Files:**
- Modify: `core/global_portfolio/roles.py`
- Test: `tests/test_global_roles.py`

**Interfaces:**
- Consumes: o bloco `history` do payload de FII produzido na Task 1.
- Produces: nenhuma assinatura nova. `renda` e `crescimento` passam a ser determináveis para FII.

**As regras, e a janela que cada uma declara:**

- **`renda` para FII** — usa a série `DY_Patrimonial` de `metricas_mensais`. O critério é o mesmo em espírito ao das ações: rendimento acima da mediana da classe **e** estável. A estabilidade passa a ser medida sobre o DY mensal, não sobre payout: desvio-padrão relativo abaixo de `LIMIARES["payout_instavel"]`. Menos de 12 meses de série torna o papel indeterminado, não negado.
- **`crescimento` para FII** — CAGR do `VPA` sobre a janela disponível, anualizado pelo número real de meses. Com 29 meses, o CAGR é de ~2,4 anos e **isso é declarado na evidência**, não apresentado como cinco anos. Menos de 12 meses torna o papel indeterminado.

Adicionar `LIMIARES["meses_minimos_fii"] = 12`, junto dos demais e com o mesmo comentário de que são escolhas heurísticas.

A `Evidencia` de ambos os papéis deve trazer no `texto` a janela real em meses. Um CAGR sem a janela é um número sem unidade.

- [ ] **Step 1: Escrever o teste que falha**

```python
def _fii(symbol, dy_mensal, vpa, peso=0.1):
    """Linha de FII com serie mensal sintetica."""
    meses = [{"Data": f"2024-{m:02d}-01", "DY_Patrimonial": d, "VPA": v}
             for m, (d, v) in enumerate(zip(dy_mensal, vpa), start=1)]
    return {
        "asset_class": "fii", "symbol": symbol, "name": symbol,
        "sector": "real_estate", "currency": "BRL", "weight_global": peso,
        "payload": {
            "fundamentals": {"dy_12m": sum(dy_mensal) / len(dy_mensal) * 12 * 100},
            "history": {"metricas_mensais": meses, "proventos_anuais": []},
            "classification": {"composition": {"pct_imoveis": 0.96, "pct_papel": 0.0}},
        },
    }


def test_renda_de_fii_usa_o_dy_mensal_e_exige_estabilidade():
    import pandas as pd
    from core.global_portfolio.roles import classificar

    estavel = [0.008] * 24
    erratico = [0.001, 0.020] * 12
    vpa = [100.0] * 24
    df = pd.DataFrame([_fii("ESTAVEL", estavel, vpa), _fii("ERRATICO", erratico, vpa)])
    saida = {p.symbol: p for p in classificar(df)}

    assert "renda" in saida["ESTAVEL"].papeis
    assert "renda" not in saida["ERRATICO"].papeis
    assert "renda" not in saida["ERRATICO"].indeterminados, "avaliado e reprovado, nao indeterminado"


def test_fii_com_serie_curta_deixa_renda_indeterminada():
    import pandas as pd
    from core.global_portfolio.roles import classificar

    df = pd.DataFrame([_fii("CURTO", [0.008] * 6, [100.0] * 6)])
    p = classificar(df)[0]
    assert "renda" in p.indeterminados
    assert "renda" not in p.papeis


def test_crescimento_de_fii_usa_cagr_do_vpa_com_a_janela_declarada():
    import pandas as pd
    from core.global_portfolio.roles import classificar

    subindo = [100.0 * (1.02 ** i) for i in range(24)]
    parado = [100.0] * 24
    df = pd.DataFrame([_fii("SOBE", [0.008] * 24, subindo),
                       _fii("PARADO", [0.008] * 24, parado)])
    saida = {p.symbol: p for p in classificar(df)}

    assert "crescimento" in saida["SOBE"].papeis
    assert "crescimento" not in saida["PARADO"].papeis

    ev = [e for e in saida["SOBE"].evidencias if e.papel == "crescimento"][0]
    assert "mes" in ev.texto.lower(), "a janela real precisa aparecer na evidencia"


def test_limiar_de_meses_minimos_existe():
    from core.global_portfolio.roles import LIMIARES
    assert "meses_minimos_fii" in LIMIARES
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_roles.py -k fii -v`

- [ ] **Step 3: Implementar as regras de FII em `roles.py`**

Sem alterar as regras de ações — só acrescentar o caminho de FII onde hoje o papel cai em indeterminado por ausência de histórico.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_global_roles.py -v`

- [ ] **Step 5: Suíte completa**

Run: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/ -q --tb=short`
Expected: baseline `1773` mais os novos, zero falhas.

- [ ] **Step 6: Commit**

```bash
git add core/global_portfolio/roles.py tests/test_global_roles.py
git commit -m "feat(global): papel de renda e crescimento para FII a partir da serie mensal"
```

---

## Passo operacional após o merge

Os 11 snapshots de FII já gravados têm `history` vazio — foram capturados antes desta mudança. Regravá-los:

```bash
python -m scripts.backfill_portfolio_snapshots --apply
```

O backfill sobrescreve por `ON CONFLICT`, então é seguro rodar de novo. Sem esse passo, o código novo lê um histórico que não está lá e os papéis continuam indeterminados — a mudança só aparece depois de regravar.

## Auto-revisão deste plano

**Cobertura:** a lacuna era "o adaptador de FII persiste `history` vazio, então `renda` e `crescimento` são indetermináveis para 30,8% do patrimônio". A Task 1 preenche o bloco, a Task 2 o consome, e o passo operacional regrava o que já estava salvo. As três coisas são necessárias — só a primeira não muda nada visível.

**Consistência de nomes:** `metricas_mensais` e `proventos_anuais` são escritas na Task 1 e lidas na Task 2 com esses mesmos nomes. As chaves de `loaders` (`metricas_mensais`, `proventos`) aparecem nos testes das duas tasks. `LIMIARES["meses_minimos_fii"]` é criado na Task 2 e testado lá.

**Limite que permanece, e é do dado:** a série cobre 2024-01 a 2026-05. Nenhuma regra deve afirmar cinco anos; a janela real entra na evidência. Quando a série crescer, as regras não mudam — só a janela declarada.
