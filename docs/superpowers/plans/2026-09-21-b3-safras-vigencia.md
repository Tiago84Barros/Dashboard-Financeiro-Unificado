# Safras da carteira B3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir a janela do gráfico de desempenho da Criação de Portfólio B3 (janeiro → abril) e entregar um relatório que mostre, safra a safra, a carteira que o motor teria montado e como ela se saiu.

**Architecture:** Uma regra de vigência única em `core/b3_vigencia.py` (puro), consumida por todos. A lógica das safras em `core/b3_safras.py` (puro: entra `resultados` + preços, sai DataFrame), a renderização em `views/portfolio_b3_safras.py`. Nenhuma lógica nova entra em `views/portfolio_b3.py`, que já tem 4.466 linhas.

**Tech Stack:** Python 3.12, pandas, numpy, Streamlit, plotly, pytest, SQLAlchemy.

## Global Constraints

- Rodar pytest **sempre** com o interpretador completo: `"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest`. O `python` do PATH cai na venv do Hermes e não tem pytest.
- `REBAL_MONTH = 4`. Nenhum arquivo além de `core/b3_vigencia.py` pode definir literal de mês de rebalance.
- A safra N é pontuada com dados até N−1 (`lag=1`) e vigora de 01/04/N a 31/03/N+1.
- Módulos `core/b3_vigencia.py` e `core/b3_safras.py` são **puros**: sem `streamlit`, sem `core.database`, sem `core.config`. Importar qualquer um desses no topo quebra a pureza (arrasta `load_dotenv`).
- Toda métrica agregada exclui a safra vigente (janela incompleta). Safra parcial aparece na tabela marcada, nunca nas médias.
- Ticker sem preço nas duas pontas da janela rende **zero** e o peso ausente é reportado — mesma convenção que `core/fii_validation.py` já aplica. Não redistribuir entre sobreviventes.
- UI em cards CSS via `design.componentes.card_metrica`. Cada card sai num único `st.markdown`; div aberta num bloco e fechada em outro vira moldura vazia.
- Linter: `ruff` com `select = ["E4", "E7", "E9", "F", "I"]` (imports ordenados).

---

### Task 1: Regra de vigência única

**Files:**
- Create: `core/b3_vigencia.py`
- Create: `tests/test_b3_vigencia.py`
- Modify: `views/empresas_b3.py:2077` (remove a definição de `_REBAL_MONTH`)
- Modify: `views/portfolio_b3.py:44` (import passa a vir de `core.b3_vigencia`)

**Interfaces:**
- Consumes: nada.
- Produces: `core.b3_vigencia.REBAL_MONTH: int`, `safra_vigente_em(data: pd.Timestamp | date) -> int`, `janela_de_vigencia(safra: int) -> tuple[pd.Timestamp, pd.Timestamp]`, `ano_base_do_score(safra: int) -> int`, `safra_completa(safra: int, hoje: pd.Timestamp | None = None) -> bool`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_b3_vigencia.py`:

```python
import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.b3_vigencia import (
    REBAL_MONTH,
    ano_base_do_score,
    janela_de_vigencia,
    safra_completa,
    safra_vigente_em,
)

RAIZ = Path(__file__).parents[1]


def test_rebal_month_e_abril():
    assert REBAL_MONTH == 4


@pytest.mark.parametrize(
    "data, esperado",
    [
        (date(2026, 1, 1), 2025),
        (date(2026, 3, 31), 2025),   # vespera: safra ainda e a anterior
        (date(2026, 4, 1), 2026),    # vira em 1o de abril
        (date(2026, 9, 21), 2026),
        (date(2026, 12, 31), 2026),
    ],
)
def test_safra_vigente_vira_em_abril(data, esperado):
    assert safra_vigente_em(data) == esperado


def test_aceita_timestamp_e_date():
    assert safra_vigente_em(pd.Timestamp("2026-04-01")) == 2026
    assert safra_vigente_em(date(2026, 4, 1)) == 2026


def test_janela_de_vigencia_e_abril_a_marco():
    inicio, fim = janela_de_vigencia(2026)
    assert inicio == pd.Timestamp("2026-04-01")
    assert fim == pd.Timestamp("2027-03-31")


def test_ano_base_e_o_exercicio_anterior():
    assert ano_base_do_score(2026) == 2025


def test_safra_vigente_nunca_usa_balanco_do_futuro():
    """Propriedade: em toda data de 2010 a 2030, o exercicio-base da safra
    vigente e estritamente anterior ao ano da propria data. Se esta
    propriedade cair, a tela esta publicando look-ahead."""
    for d in pd.date_range("2010-01-01", "2030-12-31", freq="D"):
        safra = safra_vigente_em(d)
        assert ano_base_do_score(safra) < d.year, d


def test_safra_completa_so_apos_o_fim_da_janela():
    assert safra_completa(2025, hoje=pd.Timestamp("2026-04-01")) is True
    assert safra_completa(2026, hoje=pd.Timestamp("2026-09-21")) is False
    assert safra_completa(2026, hoje=pd.Timestamp("2027-03-30")) is False
    assert safra_completa(2026, hoje=pd.Timestamp("2027-03-31")) is True


def _nomes_atribuidos(caminho: Path) -> set[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    nomes.add(alvo.id)
    return nomes


def test_mes_de_rebalance_definido_num_lugar_so():
    """Guarda duplicada nao fica igual: tres copias da regra de abril ja
    existiram e uma delas (o grafico) ficou para tras. O caminho e resolvido
    a partir da raiz do pacote, nao por `parts` de caminho absoluto --
    filtro por caminho absoluto nao visita nada dentro de worktree."""
    suspeitos = [
        RAIZ / "views" / "portfolio_b3.py",
        RAIZ / "views" / "empresas_b3.py",
        RAIZ / "core" / "b3_safras.py",
    ]
    for caminho in suspeitos:
        if not caminho.exists():
            continue
        definidos = {n for n in _nomes_atribuidos(caminho)
                     if "REBAL" in n.upper() and "MONTH" in n.upper()}
        assert not definidos, (
            f"{caminho.relative_to(RAIZ)} define {sorted(definidos)}; "
            "o mes de rebalance mora so em core/b3_vigencia.py"
        )
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_vigencia.py -q
```

Esperado: FAIL com `ModuleNotFoundError: No module named 'core.b3_vigencia'`.

- [ ] **Step 3: Criar `core/b3_vigencia.py`**

```python
"""
core/b3_vigencia.py — a regra de vigencia da safra B3, num lugar so.

Uma safra N e pontuada com dados ate N-1 (lag=1) e vigora de 01/04/N a
31/03/N+1: os balancos do exercicio N-1 so sao publicos ate 31/03 (CVM),
entao uma carteira que assume em janeiro/N usa informacao contabil que
ainda nao existia. Medir jan-dez/N seria look-ahead.

Esta regra ja existia em tres implementacoes independentes
(_simular_seg_backtest, _rank_ic_por_ano, o filtro de inicio do backtest)
e um quarto consumidor -- o grafico de desempenho -- tinha ficado de fora.
Modulo puro: sem streamlit, sem banco. Coberto por tests/test_b3_vigencia.py.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

# Balancos FY N-1 publicados ate 31/03 (CVM). Auditoria 2026-07.
REBAL_MONTH = 4


def _ts(data: pd.Timestamp | datetime | date) -> pd.Timestamp:
    return pd.Timestamp(data)


def safra_vigente_em(data: pd.Timestamp | datetime | date) -> int:
    """Ano N cuja safra esta em vigor na data.

    Antes de abril a safra vigente ainda e a do ano anterior -- e por isso
    que "ano atual" nunca serve como rotulo: em fevereiro/2027 quem manda
    ainda e a safra 2026.
    """
    d = _ts(data)
    return int(d.year) if int(d.month) >= REBAL_MONTH else int(d.year) - 1


def janela_de_vigencia(safra: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(1o de abril da safra, 31 de marco do ano seguinte)."""
    safra = int(safra)
    return (pd.Timestamp(safra, REBAL_MONTH, 1),
            pd.Timestamp(safra + 1, REBAL_MONTH, 1) - pd.Timedelta(days=1))


def ano_base_do_score(safra: int) -> int:
    """Exercicio cujos balancos alimentaram o score da safra."""
    return int(safra) - 1


def safra_completa(safra: int,
                   hoje: pd.Timestamp | datetime | date | None = None) -> bool:
    """A janela da safra ja fechou? Safra incompleta nao entra em media."""
    fim = janela_de_vigencia(safra)[1]
    return _ts(hoje if hoje is not None else pd.Timestamp.now()) >= fim
```

- [ ] **Step 4: Rodar o teste e confirmar que passa, exceto a varredura AST**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_vigencia.py -q
```

Esperado: todos PASS menos `test_mes_de_rebalance_definido_num_lugar_so`, que FALHA apontando `views/empresas_b3.py define ['_REBAL_MONTH']`.

- [ ] **Step 5: Migrar `views/empresas_b3.py`**

Em `views/empresas_b3.py:2077`, remover a linha `_REBAL_MONTH = 4` e, no bloco de imports do topo do arquivo, acrescentar:

```python
from core.b3_vigencia import REBAL_MONTH as _REBAL_MONTH
```

O alias preserva o nome usado nas 6 referências existentes do arquivo (linhas 2206, 2571, 2652, 2811, 3125) e o reexport que `views/portfolio_b3.py:44` consome. Nenhuma outra linha muda.

- [ ] **Step 6: Migrar `views/portfolio_b3.py`**

Em `views/portfolio_b3.py`, remover `_REBAL_MONTH` da lista de nomes importados de `views.empresas_b3` (linha 44) e acrescentar, junto aos demais imports de `core`:

```python
from core.b3_vigencia import (
    REBAL_MONTH as _REBAL_MONTH,
    ano_base_do_score,
    janela_de_vigencia,
    safra_vigente_em,
)
```

As três funções extras são consumidas na Task 2; importá-las agora evita um segundo toque no bloco de imports.

- [ ] **Step 7: Rodar a suíte inteira**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q
```

Esperado: PASS. A suíte oscila em torno do TTL de cache de 900s — se falhar em `market_read` ou em testes que o diff não toca, rodar de novo antes de investigar.

- [ ] **Step 8: Lint**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m ruff check core/b3_vigencia.py views/empresas_b3.py views/portfolio_b3.py tests/test_b3_vigencia.py
```

Esperado: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add core/b3_vigencia.py tests/test_b3_vigencia.py views/empresas_b3.py views/portfolio_b3.py
git commit -m "refactor(b3): regra de vigencia abril-abril num modulo so

_REBAL_MONTH vivia em views/empresas_b3.py e a regra tinha tres
implementacoes independentes. core/b3_vigencia.py passa a ser a unica
definicao, com teste de propriedade (nenhuma data de 2010 a 2030 usa
balanco do futuro) e varredura AST que falha se alguem redefinir o mes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Corrigir a janela do gráfico de desempenho

**Files:**
- Modify: `views/portfolio_b3.py:4343-4412` (seção "DESEMPENHO PARCIAL ANO ATUAL")

**Interfaces:**
- Consumes: `core.b3_vigencia.safra_vigente_em`, `janela_de_vigencia`, `ano_base_do_score` (Task 1).
- Produces: nada consumido por tarefas seguintes.

Não há teste unitário nesta task: a seção é renderização Streamlit acoplada a `_batch_yf_precos_mensais` e a `st.session_state`. A garantia vem do teste de propriedade da Task 1 (a data de início não pode mais ser anterior a abril) e da verificação visual do Step 4. Extrair a seção inteira para função pura seria refatoração maior do que a correção justifica.

- [ ] **Step 1: Trocar a âncora da janela**

Em `views/portfolio_b3.py`, no início da seção (linha ~4343), substituir o bloco que hoje é:

```python
    st.markdown("<hr style='margin:24px 0;border-color:var(--app-border);'>",
                unsafe_allow_html=True)
    _sec_hdr(f"📈 Desempenho parcial das selecionadas (ano atual: {ano_atual})")
    st.caption("Acompanhamento de aportes mensais de R$1.000 desde janeiro do ano atual.")
```

por:

```python
    st.markdown("<hr style='margin:24px 0;border-color:var(--app-border);'>",
                unsafe_allow_html=True)
    _hoje_vig = pd.Timestamp.now()
    _safra_vig = safra_vigente_em(_hoje_vig)
    _ini_vig, _fim_vig = janela_de_vigencia(_safra_vig)
    _sec_hdr(
        f"📈 Desempenho da safra {_safra_vig} "
        f"(balanços de {ano_base_do_score(_safra_vig)})"
    )
    st.caption(
        f"Aportes mensais de R$1.000 desde **abril/{_safra_vig}**, quando a "
        f"safra entrou em vigor. A janela vai até {_fim_vig:%m/%Y}. Antes de "
        "abril a carteira não existia: os balanços do exercício-base só são "
        "públicos até 31/03, e começar em janeiro mostraria o desempenho de "
        "uma carteira que ninguém poderia ter montado."
    )
    st.caption(
        "⚠️ Look-ahead residual: a carteira aqui simulada usa o piso de "
        "liquidez e a diversificação por correlação com dados de **hoje**, "
        "não de abril. A distorção é a do intervalo abril→hoje, não a do ano "
        "inteiro — mas não é zero."
    )
```

- [ ] **Step 2: Trocar a data de início da simulação**

Logo abaixo, substituir:

```python
            data_ini_ano = pd.Timestamp(ano_atual, 1, 1)
            df_ano = df_prec_prox[df_prec_prox.index >= data_ini_ano].copy()
```

por:

```python
            df_ano = df_prec_prox[
                (df_prec_prox.index >= _ini_vig) & (df_prec_prox.index <= _fim_vig)
            ].copy()
```

O recorte superior por `_fim_vig` importa: sem ele, em abril/2027 a tela mostraria a safra 2026 estendida além da própria vigência.

- [ ] **Step 3: Ajustar o título do gráfico e o fallback**

Substituir:

```python
                        f'Comparativo de desempenho parcial em {ano_atual}</div>',
```

por:

```python
                        f'Safra {_safra_vig} — abril/{_safra_vig} em diante</div>',
```

E substituir a mensagem de vazio `st.caption("Dados insuficientes para o ano atual.")` por:

```python
                st.caption(
                    f"Sem preços mensais na janela da safra {_safra_vig} "
                    f"(a partir de abril/{_safra_vig})."
                )
```

- [ ] **Step 4: Verificar na tela**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m streamlit run app.py
```

Abrir Criação de Portfólio B3, rodar a análise e conferir: o eixo X do gráfico começa em **abril**, não em janeiro; o cabeçalho nomeia a safra e o exercício-base; as duas legendas aparecem. Fechar o servidor depois.

- [ ] **Step 5: Rodar a suíte e o lint**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q && "/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m ruff check views/portfolio_b3.py
```

Esperado: PASS e `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add views/portfolio_b3.py
git commit -m "fix(b3): grafico de desempenho comeca em abril, nao em janeiro

A secao ancorava em pd.Timestamp(ano_atual, 1, 1) e aplicava a carteira
calculada hoje retroativamente a janeiro -- nove meses de look-ahead, num
motor cujo backtest ja media marco/N a marco/N+1. Passa a usar a janela de
vigencia da safra e a nomear a safra e o exercicio-base no cabecalho.

O look-ahead que permanece (liquidez e correlacao com dados de hoje) esta
declarado na tela.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Expor as carteiras históricas do motor

**Files:**
- Modify: `views/portfolio_b3.py:1204` (dict de retorno de `_processar_segmento`)

**Interfaces:**
- Consumes: nada.
- Produces: cada dict de `resultados` passa a conter `"lids_por_ano": dict[int, list[str]]` e `"pesos_por_ano": dict[int, dict[str, float]]`.

`lids_por_ano` e `pesos_por_ano` já são construídos em `_processar_segmento` (linhas 927 e 944) e já são point-in-time por construção — score com `lag=1`, dados até N−1. Eles simplesmente nunca saíram da função. Esta é a única alteração no motor.

- [ ] **Step 1: Acrescentar as duas chaves ao dict de retorno**

Em `views/portfolio_b3.py`, no `return {` da linha 1204, logo após `"pesos_prox": pesos_prox,`, inserir:

```python
        # Carteira historica por safra. Ja eram PIT (score com lag=1, dados
        # ate N-1) mas morriam dentro desta funcao -- por isso nunca houve
        # tela mostrando quem entrou na carteira de cada ano.
        "lids_por_ano": lids_por_ano,
        "pesos_por_ano": pesos_por_ano,
```

- [ ] **Step 2: Invalidar o cache de sessão de resultados antigos**

`views/portfolio_b3.py:3105` já invalida `pb3_resultados` quando falta uma chave esperada. Acrescentar a nova chave à condição:

```python
    if resultados and any(
        "val_est_oos" not in r or "lids_por_ano" not in r for r in resultados
    ):
```

Sem isso, uma sessão aberta antes do deploy renderia o relatório de safras vazio e sem erro — o defeito silencioso se esconde melhor que o erro.

- [ ] **Step 3: Verificar que as chaves chegam**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q -k portfolio_b3
```

Esperado: PASS (nenhum teste existente quebra com chaves novas no dict).

- [ ] **Step 4: Commit**

```bash
git add views/portfolio_b3.py
git commit -m "feat(b3): expor lids_por_ano e pesos_por_ano no resultado do segmento

Sao a carteira point-in-time de cada safra e ja existiam dentro de
_processar_segmento -- mas morriam ali, e por isso nao havia como mostrar
quem entrou na carteira de cada ano. O cache de sessao passa a invalidar
quando a chave falta, para nao renderizar relatorio vazio em silencio.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Motor das safras (Bloco 1)

**Files:**
- Create: `core/b3_safras.py`
- Create: `tests/test_b3_safras.py`

**Interfaces:**
- Consumes: `core.b3_vigencia` (Task 1); `resultados` com `lids_por_ano` / `pesos_por_ano` (Task 3).
- Produces:
  - `SafraCarteira` (dataclass congelada) com campos `safra: int`, `ano_base: int`, `inicio: pd.Timestamp`, `fim: pd.Timestamp`, `completa: bool`, `pesos: dict[str, float]`, `universo: tuple[str, ...]`, `segmentos: int`.
  - `carteiras_por_safra(resultados: list[dict], *, hoje: pd.Timestamp | None = None) -> list[SafraCarteira]`
  - `retorno_da_safra(carteira: SafraCarteira, df_precos: pd.DataFrame, *, selic_por_ano: dict[int, float], taxa_selic_aa: float) -> dict`
  - `tabela_de_safras(resultados, df_precos, *, selic_por_ano, taxa_selic_aa, hoje=None) -> pd.DataFrame`

O orçamento entre segmentos replica o que a tela já faz (`views/portfolio_b3.py:3593-3624`): fração igual por segmento, pesos internos do score, soma dos orçamentos quando o mesmo ticker aparece em mais de um segmento.

`carteiras_por_safra` recebe a lista de `resultados` **já filtrada** pela chamadora. É assim que a Task 7 mede o viés de universo sem duplicar código: mesma função, duas entradas (aprovados / todos).

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_b3_safras.py`:

```python
import numpy as np
import pandas as pd
import pytest

from core.b3_safras import (
    SafraCarteira,
    carteiras_por_safra,
    retorno_da_safra,
    tabela_de_safras,
)

HOJE = pd.Timestamp("2026-09-21")


def _resultado(segmento, lids_por_ano, pesos_por_ano, tickers):
    return {
        "setor": "S", "subsetor": "SS", "segmento": segmento,
        "tickers": tickers,
        "lids_por_ano": lids_por_ano,
        "pesos_por_ano": pesos_por_ano,
    }


def _precos(datas, valores_por_ticker):
    return pd.DataFrame(valores_por_ticker, index=pd.DatetimeIndex(datas))


def test_carteira_da_safra_soma_um():
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3", "BBBB3"])]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.safra == 2024
    assert carteira.ano_base == 2023
    assert carteira.inicio == pd.Timestamp("2024-04-01")
    assert carteira.fim == pd.Timestamp("2025-03-31")
    assert carteira.completa is True
    assert pytest.approx(sum(carteira.pesos.values())) == 1.0


def test_orcamento_igual_entre_segmentos():
    """Dois segmentos, um lider cada: 50% para cada segmento."""
    res = [
        _resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
        _resultado("B", {2024: ["BBBB3"]}, {2024: {"BBBB3": 1.0}}, ["BBBB3"]),
    ]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.pesos == pytest.approx({"AAAA3": 0.5, "BBBB3": 0.5})
    assert carteira.segmentos == 2


def test_ticker_em_dois_segmentos_soma_os_orcamentos():
    res = [
        _resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
        _resultado("B", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
    ]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.pesos == pytest.approx({"AAAA3": 1.0})


def test_universo_e_todos_os_tickers_dos_segmentos():
    """O equal-weight compara 'escolher os lideres' com 'comprar o segmento
    inteiro' -- entao o universo tem que ter os nao-selecionados tambem."""
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}},
                      ["AAAA3", "BBBB3", "CCCC3"])]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.universo == ("AAAA3", "BBBB3", "CCCC3")


def test_safra_vigente_vem_marcada_como_incompleta():
    res = [_resultado("A", {2024: ["AAAA3"], 2026: ["AAAA3"]},
                      {2024: {"AAAA3": 1.0}, 2026: {"AAAA3": 1.0}}, ["AAAA3"])]
    por_safra = {c.safra: c for c in carteiras_por_safra(res, hoje=HOJE)}
    assert por_safra[2024].completa is True
    assert por_safra[2026].completa is False


def test_retorno_usa_so_precos_da_janela():
    """Preco de janeiro/2024 e de maio/2025 sao armadilhas: se entrarem no
    calculo, o retorno sai diferente de +50%."""
    df = _precos(
        ["2024-01-31", "2024-04-30", "2024-12-31", "2025-03-31", "2025-05-31"],
        {"AAAA3": [1.0, 10.0, 12.0, 15.0, 999.0]},
    )
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["retorno_estrategia"] == pytest.approx(0.5)


def test_peso_sem_preco_rende_zero_e_e_reportado():
    """Mesma convencao de core/fii_validation.py: a fatia ausente nao rende o
    que os sobreviventes renderam, e nao e redistribuida entre eles."""
    df = _precos(["2024-04-30", "2025-03-31"],
                 {"AAAA3": [10.0, 20.0], "BBBB3": [np.nan, np.nan]})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 0.5, "BBBB3": 0.5},
        universo=("AAAA3", "BBBB3"), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["retorno_estrategia"] == pytest.approx(0.5)   # 0.5*1.0 + 0.5*0
    assert out["peso_ausente"] == pytest.approx(0.5)


def test_selic_da_janela_cruza_dois_anos():
    """A janela abril/2024 a marco/2025 pega 9 meses de 2024 e 3 de 2025."""
    df = _precos(pd.date_range("2024-04-30", "2025-03-31", freq="ME"),
                 {"AAAA3": [10.0] * 12})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df,
                           selic_por_ano={2024: 0.12, 2025: 0.12},
                           taxa_selic_aa=0.0)
    assert out["retorno_selic"] == pytest.approx(0.12, abs=0.005)


def test_tabela_exclui_safra_incompleta_das_medias():
    res = [_resultado("A", {2024: ["AAAA3"], 2026: ["AAAA3"]},
                      {2024: {"AAAA3": 1.0}, 2026: {"AAAA3": 1.0}}, ["AAAA3"])]
    df = _precos(pd.date_range("2024-04-30", "2026-09-30", freq="ME"),
                 {"AAAA3": np.linspace(10.0, 30.0, 30)})
    tabela = tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                              hoje=HOJE)
    assert set(tabela["Safra"]) == {2024, 2026}
    assert tabela.loc[tabela["Safra"] == 2026, "Completa"].iloc[0] is np.False_ \
        or not bool(tabela.loc[tabela["Safra"] == 2026, "Completa"].iloc[0])
    assert tabela.attrs["safras_completas"] == [2024]
    assert 2026 not in tabela.attrs["safras_completas"]
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_safras.py -q
```

Esperado: FAIL com `ModuleNotFoundError: No module named 'core.b3_safras'`.

- [ ] **Step 3: Criar `core/b3_safras.py`**

```python
"""
core/b3_safras.py — a carteira de cada safra e o que ela rendeu.

O motor de scoring ja monta `lids_por_ano` e `pesos_por_ano` por segmento,
point-in-time (score com lag=1, dados ate N-1). Este modulo agrega isso
entre segmentos com o mesmo orcamento que a tela usa e mede o retorno da
janela de vigencia de cada safra.

`carteiras_por_safra` recebe a lista de resultados JA FILTRADA pela
chamadora. E assim que a medicao do vies de universo funciona: a mesma
funcao roda com os segmentos aprovados e com todos, e a distancia entre as
duas curvas e o tamanho do vies.

Modulo puro: sem streamlit, sem banco. Coberto por tests/test_b3_safras.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.b3_vigencia import ano_base_do_score, janela_de_vigencia, safra_completa


@dataclass(frozen=True)
class SafraCarteira:
    """A carteira que o motor teria montado numa safra, e seu universo."""
    safra: int
    ano_base: int
    inicio: pd.Timestamp
    fim: pd.Timestamp
    completa: bool
    pesos: dict[str, float]
    universo: tuple[str, ...]
    segmentos: int


def carteiras_por_safra(resultados: list[dict], *,
                        hoje: pd.Timestamp | None = None
                        ) -> list[SafraCarteira]:
    """Agrega os lideres por segmento na carteira consolidada de cada safra.

    Orcamento igual por segmento que tem lider na safra; dentro dele, os
    pesos do score. Ticker em mais de um segmento soma os orcamentos --
    mesma regra de views/portfolio_b3.py:3593-3624.
    """
    anos: set[int] = set()
    for res in resultados or []:
        anos.update(int(a) for a in (res.get("lids_por_ano") or {}))

    saida: list[SafraCarteira] = []
    for safra in sorted(anos):
        contribuintes = [
            res for res in resultados
            if (res.get("lids_por_ano") or {}).get(safra)
        ]
        if not contribuintes:
            continue
        orcamento = 1.0 / len(contribuintes)

        pesos: dict[str, float] = {}
        universo: list[str] = []
        for res in contribuintes:
            lids = list((res.get("lids_por_ano") or {})[safra])
            internos = dict((res.get("pesos_por_ano") or {}).get(safra) or {})
            total = sum(float(v) for v in internos.values())
            if total <= 0:
                internos = {tk: 1.0 / len(lids) for tk in lids}
                total = 1.0
            for tk in lids:
                fatia = orcamento * float(internos.get(tk, 0.0)) / total
                pesos[tk] = pesos.get(tk, 0.0) + fatia
            universo.extend(str(t).upper() for t in (res.get("tickers") or []))

        inicio, fim = janela_de_vigencia(safra)
        saida.append(SafraCarteira(
            safra=safra,
            ano_base=ano_base_do_score(safra),
            inicio=inicio,
            fim=fim,
            completa=safra_completa(safra, hoje=hoje),
            pesos=pesos,
            # dict.fromkeys preserva a ordem de insercao; sorted seria uma
            # ordem alfabetica que apaga a estrutura por segmento.
            universo=tuple(dict.fromkeys(universo)),
            segmentos=len(contribuintes),
        ))
    return saida


def _preco_nas_pontas(serie: pd.Series, inicio: pd.Timestamp,
                      fim: pd.Timestamp) -> tuple[float, float] | None:
    """Primeiro e ultimo preco valido DENTRO da janela, ou None."""
    dentro = serie[(serie.index >= inicio) & (serie.index <= fim)].dropna()
    dentro = dentro[dentro > 0]
    if len(dentro) < 2:
        return None
    return float(dentro.iloc[0]), float(dentro.iloc[-1])


def _retorno_selic(inicio: pd.Timestamp, fim: pd.Timestamp,
                   selic_por_ano: dict[int, float],
                   taxa_selic_aa: float) -> float:
    """Composto mes a mes, com a taxa do ano de cada mes -- a janela cruza
    dois anos civis e usar a taxa de um so deles distorce o benchmark."""
    acumulado = 1.0
    for mes in pd.date_range(inicio, fim, freq="MS"):
        taxa_aa = float(selic_por_ano.get(int(mes.year), taxa_selic_aa) or 0.0)
        acumulado *= (1.0 + taxa_aa) ** (1.0 / 12.0)
    return acumulado - 1.0


def retorno_da_safra(carteira: SafraCarteira, df_precos: pd.DataFrame, *,
                     selic_por_ano: dict[int, float],
                     taxa_selic_aa: float) -> dict:
    """Retorno buy-and-hold da janela de vigencia, contra Selic e equal-weight.

    Ticker sem duas cotacoes validas na janela rende ZERO e seu peso e
    reportado em `peso_ausente`. Nao redistribuimos a fatia entre os
    sobreviventes: isso faria a carteira render o que os sobreviventes
    renderam, que e exatamente o vies que a medicao existe para evitar.
    """
    inicio, fim = carteira.inicio, carteira.fim

    retorno_est = 0.0
    peso_ausente = 0.0
    for tk, peso in carteira.pesos.items():
        pontas = (_preco_nas_pontas(df_precos[tk], inicio, fim)
                  if tk in df_precos.columns else None)
        if pontas is None:
            peso_ausente += float(peso)
            continue
        p0, p1 = pontas
        retorno_est += float(peso) * (p1 / p0 - 1.0)

    retornos_ew: list[float] = []
    for tk in carteira.universo:
        pontas = (_preco_nas_pontas(df_precos[tk], inicio, fim)
                  if tk in df_precos.columns else None)
        if pontas is not None:
            p0, p1 = pontas
            retornos_ew.append(p1 / p0 - 1.0)
    retorno_ew = float(np.mean(retornos_ew)) if retornos_ew else float("nan")

    retorno_selic = _retorno_selic(inicio, fim, selic_por_ano or {},
                                   taxa_selic_aa)
    return {
        "retorno_estrategia": retorno_est,
        "retorno_equal_weight": retorno_ew,
        "retorno_selic": retorno_selic,
        "excesso_selic": retorno_est - retorno_selic,
        "excesso_equal_weight": (retorno_est - retorno_ew
                                 if np.isfinite(retorno_ew) else float("nan")),
        "peso_ausente": peso_ausente,
        "n_universo": len(retornos_ew),
    }


def tabela_de_safras(resultados: list[dict], df_precos: pd.DataFrame, *,
                     selic_por_ano: dict[int, float],
                     taxa_selic_aa: float,
                     hoje: pd.Timestamp | None = None) -> pd.DataFrame:
    """Uma linha por safra. `attrs['safras_completas']` lista as que podem
    entrar em media -- a safra vigente tem janela aberta e fica de fora."""
    linhas: list[dict] = []
    completas: list[int] = []
    for carteira in carteiras_por_safra(resultados, hoje=hoje):
        metricas = retorno_da_safra(carteira, df_precos,
                                    selic_por_ano=selic_por_ano,
                                    taxa_selic_aa=taxa_selic_aa)
        maiores = sorted(carteira.pesos.items(),
                         key=lambda kv: (-kv[1], kv[0]))[:5]
        linhas.append({
            "Safra": carteira.safra,
            "Exercício-base": carteira.ano_base,
            "Janela": f"{carteira.inicio:%m/%Y} a {carteira.fim:%m/%Y}",
            "Completa": carteira.completa,
            "Segmentos": carteira.segmentos,
            "Ativos": len(carteira.pesos),
            "Maiores posições": ", ".join(f"{tk} {p:.0%}" for tk, p in maiores),
            "Estratégia (%)": round(metricas["retorno_estrategia"] * 100, 1),
            "Equal-weight (%)": round(metricas["retorno_equal_weight"] * 100, 1),
            "Selic (%)": round(metricas["retorno_selic"] * 100, 1),
            "Excesso s/ Selic (pp)": round(metricas["excesso_selic"] * 100, 1),
            "Peso sem preço (%)": round(metricas["peso_ausente"] * 100, 1),
        })
        if carteira.completa:
            completas.append(carteira.safra)

    tabela = pd.DataFrame(linhas)
    tabela.attrs["safras_completas"] = completas
    return tabela
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_safras.py -q
```

Esperado: PASS (10 testes).

- [ ] **Step 5: Confirmar que o módulo é puro**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -c "import sys; import core.b3_safras; assert 'streamlit' not in sys.modules, 'core/b3_safras.py arrastou streamlit'; print('puro')"
```

Esperado: `puro`.

- [ ] **Step 6: Lint e commit**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m ruff check core/b3_safras.py tests/test_b3_safras.py
git add core/b3_safras.py tests/test_b3_safras.py
git commit -m "feat(b3): motor das safras -- carteira e retorno da janela de vigencia

Agrega lids_por_ano/pesos_por_ano entre segmentos com o mesmo orcamento
que a tela usa e mede o retorno de abril/N a abril/N+1 contra Selic e
equal-weight do segmento inteiro.

Peso sem preco na janela rende zero e e reportado, nao redistribuido --
redistribuir faria a carteira render o que os sobreviventes renderam.
Safra vigente fica fora das medias: janela aberta.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Tela do relatório de safras (Bloco 1)

**Files:**
- Create: `views/portfolio_b3_safras.py`
- Modify: `views/portfolio_b3.py` (import + uma chamada, logo após a seção de desempenho da Task 2)

**Interfaces:**
- Consumes: `core.b3_safras.tabela_de_safras` (Task 4); `design.componentes.card_metrica`.
- Produces: `views.portfolio_b3_safras.render_safras(resultados, df_precos, *, selic_por_ano, taxa_selic_aa, resultados_todos=None) -> None`. A Task 5 usa só os quatro primeiros; `resultados_todos` entra na Task 7.

- [ ] **Step 1: Criar `views/portfolio_b3_safras.py`**

```python
"""
views/portfolio_b3_safras.py — relatorio de desempenho safra a safra.

Renderizacao apenas: toda a aritmetica mora em core/b3_safras.py.
views/portfolio_b3.py ja tem 4.466 linhas e nao recebe logica nova.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from core.b3_safras import tabela_de_safras
from design.componentes import card_metrica


def render_safras(resultados: list[dict], df_precos: pd.DataFrame, *,
                  selic_por_ano: dict[int, float],
                  taxa_selic_aa: float) -> None:
    """Bloco 1: a tabela de safras e a curva encadeada."""
    st.markdown("<hr style='margin:24px 0;border-color:var(--app-border);'>",
                unsafe_allow_html=True)
    st.markdown(
        '<div style="font-weight:700;font-size:1.05rem;color:var(--app-text);'
        'margin-bottom:8px;">🗂️ Desempenho safra a safra</div>',
        unsafe_allow_html=True,
    )

    if not resultados or df_precos is None or df_precos.empty:
        st.caption("Rode a análise para reconstruir as safras.")
        return

    tabela = tabela_de_safras(resultados, df_precos,
                              selic_por_ano=selic_por_ano,
                              taxa_selic_aa=taxa_selic_aa)
    if tabela.empty:
        st.caption("Nenhuma safra com líderes reconstruídos.")
        return

    completas = tabela[tabela["Completa"]]
    st.caption(
        f"{len(completas)} safra(s) com janela fechada, de "
        f"{int(tabela['Safra'].min())} a {int(tabela['Safra'].max())}. "
        "Cada safra é pontuada com dados até o ano anterior e vigora de "
        "abril a março. A safra vigente aparece na tabela com a janela em "
        "curso e **não entra nas médias**."
    )

    cols = st.columns(3)
    if not completas.empty:
        with cols[0]:
            card_metrica("Safras medidas", f"{len(completas)}",
                         ajuda="Apenas janelas fechadas")
        with cols[1]:
            media = float(completas["Excesso s/ Selic (pp)"].mean())
            card_metrica("Excesso médio s/ Selic", f"{media:+.1f} pp",
                         positivo=media > 0,
                         ajuda="Média simples das safras completas")
        with cols[2]:
            venceu = int((completas["Excesso s/ Selic (pp)"] > 0).sum())
            card_metrica("Safras acima da Selic",
                         f"{venceu} de {len(completas)}",
                         ajuda="Contagem, não significância")
    else:
        with cols[0]:
            card_metrica("Safras medidas", "0",
                         ajuda="Nenhuma janela fechada ainda")

    st.dataframe(tabela, width="stretch", hide_index=True)

    if not completas.empty:
        longo = completas.melt(
            id_vars="Safra",
            value_vars=["Estratégia (%)", "Equal-weight (%)", "Selic (%)"],
            var_name="Série", value_name="Retorno da safra (%)",
        )
        fig = px.bar(longo, x="Safra", y="Retorno da safra (%)",
                     color="Série", barmode="group")
        fig.update_layout(height=360, margin=dict(l=8, r=8, t=8, b=8))
        st.plotly_chart(fig, width="stretch",
                        config={"displayModeBar": False},
                        key="pb3_safras_barras")
        st.caption(
            "Barras, não curva acumulada: encadear os retornos produziria um "
            "número grande e único, que esconde quantas safras individuais "
            "ficaram atrás do benchmark."
        )

    ausente = float(tabela["Peso sem preço (%)"].max() or 0.0)
    if ausente > 0:
        st.info(
            f"Em pelo menos uma safra, até {ausente:.1f}% do peso ficou sem "
            "preço na janela — deslistagem, incorporação ou buraco de dado. "
            "Essa fatia rende **zero** no cálculo: não inventamos a perda, "
            "mas ela também não rende o que os sobreviventes renderam."
        )
```

- [ ] **Step 2: Chamar a partir de `views/portfolio_b3.py`**

Acrescentar ao bloco de imports:

```python
from views.portfolio_b3_safras import render_safras
```

E, imediatamente após o fim da seção de desempenho da safra vigente (antes de `_render_metodologia_portfolio()`), inserir:

```python
    render_safras(
        resultados,
        st.session_state.get("pb3_precos_all", pd.DataFrame()),
        selic_por_ano=selic_macro,
        taxa_selic_aa=taxa_selic_aa,
    )
```

`selic_macro` (`dict[int, float]`, de `_db.load_selic_macro()` em `views/portfolio_b3.py:2812`), `taxa_selic_aa` e `resultados` estão todos em escopo nesse ponto — é a mesma função que chama `_render_metodologia_portfolio()` logo abaixo.

- [ ] **Step 3: Verificar na tela**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m streamlit run app.py
```

Abrir Criação de Portfólio B3, rodar a análise, rolar até "Desempenho safra a safra". Conferir: os três cards aparecem com borda e conteúdo dentro (não moldura vazia); a tabela lista uma linha por safra; a safra vigente aparece com `Completa = False`; o gráfico de barras só mostra safras completas.

- [ ] **Step 4: Rodar a suíte e o lint**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q && "/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m ruff check views/portfolio_b3_safras.py views/portfolio_b3.py
```

- [ ] **Step 5: Commit**

```bash
git add views/portfolio_b3_safras.py views/portfolio_b3.py
git commit -m "feat(b3): tela do relatorio de safras

Uma linha por safra com a carteira que o motor teria montado e o retorno
da janela de vigencia contra Selic e equal-weight. Barras por safra em vez
de curva acumulada: o numero unico esconde quantas safras ficaram atras.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Bloco 3 — banda e fragilidade

**Files:**
- Modify: `core/b3_safras.py` (duas funções novas)
- Modify: `tests/test_b3_safras.py` (testes novos)
- Modify: `views/portfolio_b3_safras.py` (render do bloco)

**Interfaces:**
- Consumes: `core.b3_evidence.classify_evidence`, `minimum_detectable_effect`; `tabela_de_safras` (Task 4).
- Produces:
  - `bootstrap_excesso(excessos: list[float], *, n_reamostras: int = 10000, seed: int = 20260921) -> tuple[float | None, float | None]`
  - `fragilidade_leave_one_out(ic_values: list[float]) -> dict` com chaves `estado_completo: str`, `safras_que_viram: int`, `estados_loo: list[str]`.

A semente do bootstrap é fixa por reprodutibilidade. A fragilidade **não** é medida re-semeando — é medida pelo leave-one-out, que é o que realmente testa se a conclusão depende de uma safra específica.

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar a `tests/test_b3_safras.py`:

```python
from core.b3_safras import bootstrap_excesso, fragilidade_leave_one_out


def test_bootstrap_de_excessos_todos_positivos_nao_atravessa_zero():
    baixo, alto = bootstrap_excesso([0.10, 0.12, 0.11, 0.09, 0.13])
    assert baixo > 0
    assert alto > baixo


def test_bootstrap_de_excessos_mistos_atravessa_zero():
    baixo, alto = bootstrap_excesso([0.20, -0.18, 0.15, -0.22, 0.05])
    assert baixo < 0 < alto


def test_bootstrap_com_amostra_minima_devolve_none():
    assert bootstrap_excesso([0.1]) == (None, None)
    assert bootstrap_excesso([]) == (None, None)


def test_bootstrap_e_reprodutivel():
    assert bootstrap_excesso([0.1, -0.05, 0.2, 0.0, 0.07]) == \
           bootstrap_excesso([0.1, -0.05, 0.2, 0.0, 0.07])


def test_leave_one_out_detecta_conclusao_que_depende_de_uma_safra():
    """Quatro ICs modestos e um outlier que sustenta sozinho a media.
    Remover o outlier tem que mudar o estado -- se a funcao devolver
    zero safras que viram, ela nao esta recalculando de verdade."""
    ic_values = [0.02, 0.01, 0.0, 0.01, 0.60]
    out = fragilidade_leave_one_out(ic_values)
    assert out["safras_que_viram"] >= 1
    assert len(out["estados_loo"]) == len(ic_values)


def test_leave_one_out_em_evidencia_robusta_nao_vira():
    ic_values = [0.30, 0.32, 0.28, 0.31, 0.29, 0.33]
    out = fragilidade_leave_one_out(ic_values)
    assert out["safras_que_viram"] == 0
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_safras.py -q -k "bootstrap or leave_one_out"
```

Esperado: FAIL com `ImportError: cannot import name 'bootstrap_excesso'`.

- [ ] **Step 3: Implementar em `core/b3_safras.py`**

Acrescentar ao fim do arquivo:

```python
def bootstrap_excesso(excessos: list[float], *,
                      n_reamostras: int = 10_000,
                      seed: int = 20260921
                      ) -> tuple[float | None, float | None]:
    """Intervalo de 95% do excesso medio por safra, por reamostragem.

    Semente fixa: o mesmo conjunto de safras tem que dar o mesmo intervalo
    entre execucoes. A fragilidade da conclusao NAO se mede re-semeando --
    trocar a semente so troca o ruido de Monte Carlo. Quem mede se a
    conclusao depende de uma safra especifica e fragilidade_leave_one_out.
    """
    valores = np.asarray([float(v) for v in (excessos or [])
                          if v is not None and np.isfinite(float(v))],
                         dtype=float)
    if len(valores) < 2:
        return (None, None)
    rng = np.random.default_rng(seed)
    medias = rng.choice(valores, size=(n_reamostras, len(valores)),
                        replace=True).mean(axis=1)
    return (float(np.percentile(medias, 2.5)),
            float(np.percentile(medias, 97.5)))


def fragilidade_leave_one_out(ic_values: list[float]) -> dict:
    """Quantas safras precisam sair para o veredito virar.

    Reusa core.b3_evidence.classify_evidence -- o mesmo classificador que
    a tela ja aplica, para que o veredito do LOO seja comparavel ao
    veredito publicado, e nao um segundo criterio parecido.
    """
    from core.b3_evidence import classify_evidence

    limpos = [float(v) for v in (ic_values or [])
              if v is not None and np.isfinite(float(v))]
    completo = classify_evidence(ic_values=limpos).estado
    estados: list[str] = []
    for i in range(len(limpos)):
        sem_i = limpos[:i] + limpos[i + 1:]
        estados.append(classify_evidence(ic_values=sem_i).estado)
    return {
        "estado_completo": completo,
        "estados_loo": estados,
        "safras_que_viram": sum(1 for e in estados if e != completo),
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_safras.py -q
```

Esperado: PASS (16 testes).

- [ ] **Step 5: Renderizar o bloco**

Acrescentar a `views/portfolio_b3_safras.py`:

```python
def render_expectativa(resultados: list[dict], tabela: pd.DataFrame) -> None:
    """Bloco 3: o motor ordena? supera? e quao fragil e a conclusao?

    Tres perguntas distintas. Publicar um unico numero de "expectativa"
    juntaria as tres e leria como previsao um resultado que a amostra nao
    sustenta: ordenar nao e superar.
    """
    from core.b3_evidence import evidence_label, classify_evidence
    from core.b3_safras import bootstrap_excesso, fragilidade_leave_one_out

    st.markdown(
        '<div style="font-weight:700;font-size:1.05rem;color:var(--app-text);'
        'margin:20px 0 8px;">🎯 O que esperar da safra vigente</div>',
        unsafe_allow_html=True,
    )

    ic_values = [float(v) for res in (resultados or [])
                 for v in (res.get("rank_ic_values") or [])
                 if v is not None and pd.notna(v)]
    completas = tabela[tabela["Completa"]] if not tabela.empty else tabela
    excessos = ([v / 100.0 for v in completas["Excesso s/ Selic (pp)"]]
                if not completas.empty else [])

    veredito = classify_evidence(ic_values=ic_values)
    baixo, alto = bootstrap_excesso(excessos)
    loo = fragilidade_leave_one_out(ic_values)

    cols = st.columns(3)
    with cols[0]:
        card_metrica("Ordena?", evidence_label(veredito),
                     ajuda=(f"{veredito.anos_medidos} ano(s) de Rank-IC. "
                            f"Efeito mínimo detectável: "
                            f"{veredito.efeito_minimo_detectavel:.3f}"
                            if veredito.efeito_minimo_detectavel is not None
                            else f"{veredito.anos_medidos} ano(s) de Rank-IC"))
    with cols[1]:
        texto = (f"{baixo:+.1%} a {alto:+.1%}" if baixo is not None else "—")
        card_metrica("Supera? (excesso s/ Selic)", texto,
                     positivo=(baixo is not None and baixo > 0),
                     ajuda="Intervalo de 95% por reamostragem das safras")
    with cols[2]:
        card_metrica("Fragilidade", f"{loo['safras_que_viram']} safra(s)",
                     positivo=loo["safras_que_viram"] == 0,
                     ajuda="Quantas precisam sair para o veredito virar")

    if baixo is not None and baixo <= 0 <= alto:
        st.warning(
            f"O intervalo de 95% do excesso sobre a Selic vai de {baixo:+.1%} "
            f"a {alto:+.1%} por safra: ele **atravessa o zero**. Nesta "
            "amostra, a vantagem observada não é distinguível de acaso. "
            "Ordenar não é superar — o Rank-IC pode indicar que o motor "
            "discrimina retornos sem que isso vire vantagem líquida."
        )
    if loo["safras_que_viram"] > 0:
        st.warning(
            f"O veredito muda se {loo['safras_que_viram']} das "
            f"{len(loo['estados_loo'])} safras for removida. Uma conclusão "
            "que depende de uma safra específica não é uma conclusão sobre a "
            "estratégia — é uma conclusão sobre aquele ano."
        )
    if not excessos:
        st.caption("Nenhuma safra com janela fechada: sem base para a banda.")
```

E, ao fim de `render_safras`, antes do bloco de peso ausente, chamar:

```python
    render_expectativa(resultados, tabela)
```

- [ ] **Step 6: Verificar na tela, rodar suíte e lint**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q && "/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m ruff check core/b3_safras.py views/portfolio_b3_safras.py tests/test_b3_safras.py
```

Na tela, conferir que os três cards aparecem e que os avisos surgem quando cabem.

- [ ] **Step 7: Commit**

```bash
git add core/b3_safras.py views/portfolio_b3_safras.py tests/test_b3_safras.py
git commit -m "feat(b3): expectativa da safra como banda e fragilidade, nao numero

Tres perguntas separadas: ordena (Rank-IC + efeito minimo detectavel),
supera (banda bootstrap do excesso sobre a Selic) e quao fragil e a
conclusao (leave-one-out sobre as safras).

O veredito da B3 ja passou a APROVADO por 0,004 e reprovava de novo ao
tirar 1 de 7 dos 10 anos. Essa fragilidade passa a estar na tela.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Bloco 2 — tamanho do viés de universo

**Files:**
- Modify: `views/portfolio_b3_safras.py` (bloco sob demanda)
- Modify: `views/portfolio_b3.py` (passar a lista não filtrada)
- Modify: `tests/test_b3_safras.py` (teste 7)

**Interfaces:**
- Consumes: `core.b3_safras.tabela_de_safras` (Task 4).
- Produces: `render_vies_universo(resultados_aprovados, resultados_todos, df_precos, *, selic_por_ano, taxa_selic_aa) -> None`.

O orçamento entre segmentos usa os segmentos **aprovados**, e a aprovação sai do teste OOS com FDR sobre a amostra inteira. O universo da carteira de hoje foi escolhido com informação posterior às safras antigas. Não dá para tornar isso PIT (nas primeiras safras não há janela OOS e nenhum segmento seria aprovado), então medimos o tamanho.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar a `tests/test_b3_safras.py`:

```python
def test_vies_de_universo_aparece_quando_ha_segmento_reprovado():
    """Se as duas curvas forem identicas num cenario com segmento reprovado,
    a medicao do vies nao esta medindo nada."""
    aprovado = _resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"])
    reprovado = _resultado("B", {2024: ["BBBB3"]}, {2024: {"BBBB3": 1.0}}, ["BBBB3"])
    df = _precos(["2024-04-30", "2025-03-31"],
                 {"AAAA3": [10.0, 20.0], "BBBB3": [10.0, 5.0]})

    so_aprovados = tabela_de_safras([aprovado], df, selic_por_ano={},
                                    taxa_selic_aa=0.0, hoje=HOJE)
    todos = tabela_de_safras([aprovado, reprovado], df, selic_por_ano={},
                             taxa_selic_aa=0.0, hoje=HOJE)

    ret_aprovados = float(so_aprovados["Estratégia (%)"].iloc[0])
    ret_todos = float(todos["Estratégia (%)"].iloc[0])
    assert ret_aprovados == pytest.approx(100.0)
    assert ret_todos == pytest.approx(25.0)      # (100 + (-50)) / 2
    assert abs(ret_aprovados - ret_todos) > 1.0
```

- [ ] **Step 2: Rodar e confirmar que passa**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_safras.py -q -k vies_de_universo
```

Esperado: PASS. Este teste exercita `tabela_de_safras` já implementada — ele existe para provar que a medição do viés é possível pela substituição de entrada, e falharia se alguém acoplasse a aprovação dentro do motor.

- [ ] **Step 3: Renderizar o bloco sob demanda**

Acrescentar a `views/portfolio_b3_safras.py`:

```python
def render_vies_universo(resultados_aprovados: list[dict],
                         resultados_todos: list[dict],
                         df_precos: pd.DataFrame, *,
                         selic_por_ano: dict[int, float],
                         taxa_selic_aa: float) -> None:
    """Bloco 2: o tamanho do vies de selecao de universo, sob demanda."""
    st.markdown(
        '<div style="font-weight:700;font-size:1.05rem;color:var(--app-text);'
        'margin:20px 0 8px;">🔍 Tamanho do viés de universo</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "O score de cada safra é point-in-time, mas o **conjunto de "
        "segmentos** que compõe a carteira foi escolhido com o teste OOS "
        "sobre a amostra inteira, até hoje. A safra de 2015 é reconstruída "
        "com segmentos aprovados por evidência de 2026. Isto mede o tamanho "
        "disso: as mesmas safras, com todos os segmentos que tinham score "
        "naquele ano, sem gate de aprovação."
    )

    if not st.button("Medir o viés de universo", key="pb3_btn_vies"):
        medido = st.session_state.get("pb3_vies_universo")
        if medido is not None:
            st.caption(f"Última medição: {medido['quando']}")
            st.dataframe(medido["tabela"], width="stretch", hide_index=True)
        return

    with st.spinner("Reconstruindo as safras sem o gate de aprovação…"):
        com_gate = tabela_de_safras(resultados_aprovados, df_precos,
                                    selic_por_ano=selic_por_ano,
                                    taxa_selic_aa=taxa_selic_aa)
        sem_gate = tabela_de_safras(resultados_todos, df_precos,
                                    selic_por_ano=selic_por_ano,
                                    taxa_selic_aa=taxa_selic_aa)

    if com_gate.empty or sem_gate.empty:
        st.caption("Sem safras suficientes para medir.")
        return

    comparacao = com_gate[["Safra", "Completa", "Estratégia (%)"]].merge(
        sem_gate[["Safra", "Estratégia (%)"]],
        on="Safra", suffixes=(" com gate", " sem gate"),
    )
    comparacao["Viés (pp)"] = (comparacao["Estratégia (%) com gate"]
                               - comparacao["Estratégia (%) sem gate"]).round(1)
    st.session_state["pb3_vies_universo"] = {
        "quando": pd.Timestamp.now().strftime("%d/%m/%Y %H:%M"),
        "tabela": comparacao,
    }

    fechadas = comparacao[comparacao["Completa"]]
    if not fechadas.empty:
        medio = float(fechadas["Viés (pp)"].mean())
        cols = st.columns(1)
        with cols[0]:
            card_metrica("Viés médio de universo", f"{medio:+.1f} pp",
                         positivo=abs(medio) < 1.0,
                         ajuda="Com gate menos sem gate, safras fechadas")
        st.caption(
            f"O gate de aprovação adiciona {medio:+.1f} pp por safra em "
            "média. Esse ganho **não era conhecido na época** de cada safra: "
            "ele vem de saber, hoje, quais segmentos passaram no teste. É o "
            "tamanho do viés, não um resultado da estratégia."
        )
    st.dataframe(comparacao, width="stretch", hide_index=True)
```

- [ ] **Step 4: Passar a lista não filtrada a partir de `views/portfolio_b3.py`**

Na chamada inserida na Task 5, acrescentar a lista completa. `resultados` (`st.session_state["pb3_resultados"]`) já é a lista **não filtrada**; a filtrada é `aprovados` (`views/portfolio_b3.py:3207`). Ajustar a chamada para:

```python
    render_safras(
        aprovados,
        st.session_state.get("pb3_precos_all", pd.DataFrame()),
        selic_por_ano=selic_macro,
        taxa_selic_aa=taxa_selic_aa,
        resultados_todos=resultados,
    )
```

E em `render_safras`, acrescentar o parâmetro `resultados_todos: list[dict] | None = None` e, ao fim, chamar:

```python
    if resultados_todos:
        render_vies_universo(resultados, resultados_todos, df_precos,
                             selic_por_ano=selic_por_ano,
                             taxa_selic_aa=taxa_selic_aa)
```

(Dentro de `render_safras`, o primeiro parâmetro posicional continua se chamando `resultados` e agora carrega os aprovados.)

- [ ] **Step 5: Verificar na tela, suíte e lint**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q && "/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m ruff check views/portfolio_b3_safras.py views/portfolio_b3.py tests/test_b3_safras.py
```

Na tela: o bloco aparece com o botão; clicar mede e mostra a tabela comparativa; recarregar a seção mantém a última medição com a data.

- [ ] **Step 6: Commit**

```bash
git add views/portfolio_b3_safras.py views/portfolio_b3.py tests/test_b3_safras.py
git commit -m "feat(b3): medir o tamanho do vies de universo, sob demanda

O score de cada safra e PIT, mas o conjunto de segmentos vem do teste OOS
sobre a amostra inteira -- a safra de 2015 e reconstruida com segmentos
aprovados por evidencia de 2026. Tornar isso PIT e inviavel (as primeiras
safras nao tem janela OOS), entao medimos: as mesmas safras sem o gate, e
a distancia em pontos percentuais.

Vies sem tamanho nao e acionavel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Carimbar a safra na carteira-modelo salva

**Files:**
- Modify: `core/b3_portfolio_model.py:245-300`
- Create: `tests/test_b3_portfolio_model_safra.py`

**Interfaces:**
- Consumes: `core.b3_vigencia.safra_vigente_em`, `janela_de_vigencia` (Task 1).
- Produces: `save_b3_portfolio_model(..., hoje: pd.Timestamp | None = None)` grava `ano_compra = safra_vigente_em(hoje)` e `params_json["safra"]`, `params_json["vigencia_inicio"]`, `params_json["vigencia_fim"]`.

Nenhuma tabela nova: `b3_portfolio_models` e `b3_portfolio_model_items` já existem e `save_b3_portfolio_model` **arquiva** a versão anterior em vez de apagá-la, então o histórico já se acumula.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_b3_portfolio_model_safra.py`:

```python
"""A safra de uma carteira salva nao pode ser inferida de created_at.

Em fevereiro de 2027 a safra vigente ainda e a de 2026: date.today().year
carimbaria 2027 e a carteira apareceria no relatorio no ano errado, sem
erro visivel.
"""
import pandas as pd
import pytest

from core.b3_portfolio_model import _params_com_safra


def test_carimbo_em_fevereiro_usa_a_safra_do_ano_anterior():
    params, ano_compra = _params_com_safra({}, hoje=pd.Timestamp("2027-02-10"))
    assert ano_compra == 2026
    assert params["safra"] == 2026
    assert params["vigencia_inicio"] == "2026-04-01"
    assert params["vigencia_fim"] == "2027-03-31"


def test_carimbo_em_abril_usa_a_safra_do_proprio_ano():
    params, ano_compra = _params_com_safra({}, hoje=pd.Timestamp("2026-04-01"))
    assert ano_compra == 2026
    assert params["safra"] == 2026


def test_ano_compra_explicito_do_usuario_prevalece():
    params, ano_compra = _params_com_safra({"ano_compra": 2023},
                                           hoje=pd.Timestamp("2026-09-21"))
    assert ano_compra == 2023
    assert params["safra"] == 2023
    assert params["vigencia_inicio"] == "2023-04-01"


def test_nao_perde_as_chaves_que_ja_vinham():
    params, _ = _params_com_safra({"score_version": "v9"},
                                  hoje=pd.Timestamp("2026-09-21"))
    assert params["score_version"] == "v9"
```

- [ ] **Step 2: Rodar e confirmar que falha**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_portfolio_model_safra.py -q
```

Esperado: FAIL com `ImportError: cannot import name '_params_com_safra'`.

- [ ] **Step 3: Implementar em `core/b3_portfolio_model.py`**

Acrescentar ao bloco de imports:

```python
from core.b3_vigencia import janela_de_vigencia, safra_vigente_em
```

E, acima de `save_b3_portfolio_model`, a função pura:

```python
def _params_com_safra(params: dict | None, *, hoje=None) -> tuple[dict, int]:
    """Carimba safra e janela de vigencia nos params, e devolve o ano_compra.

    `date.today().year` carimbaria 2027 numa carteira salva em fevereiro de
    2027, quando a safra vigente ainda e a de 2026. A safra nunca deve
    precisar ser inferida de created_at.
    """
    import pandas as pd

    params = dict(params or {})
    hoje = pd.Timestamp(hoje) if hoje is not None else pd.Timestamp.now()
    safra = int(params.get("ano_compra") or safra_vigente_em(hoje))
    inicio, fim = janela_de_vigencia(safra)
    params["safra"] = safra
    params["vigencia_inicio"] = inicio.strftime("%Y-%m-%d")
    params["vigencia_fim"] = fim.strftime("%Y-%m-%d")
    return params, safra
```

Em `save_b3_portfolio_model`, substituir:

```python
    params = dict(params or {})
    params.setdefault("score_version", SCORE_VERSION)
    params.setdefault("model_schema_version", MODEL_SCHEMA_VERSION)
    owner = _owner_id()
    ano_compra = int(params.get("ano_compra") or date.today().year)
```

por:

```python
    params = dict(params or {})
    params.setdefault("score_version", SCORE_VERSION)
    params.setdefault("model_schema_version", MODEL_SCHEMA_VERSION)
    params, ano_compra = _params_com_safra(params)
    owner = _owner_id()
```

Se `date` deixar de ser usado no arquivo, remover o import para o ruff não acusar `F401`.

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests/test_b3_portfolio_model_safra.py -q
```

Esperado: PASS (4 testes).

- [ ] **Step 5: Suíte completa e lint**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q && "/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m ruff check core/b3_portfolio_model.py tests/test_b3_portfolio_model_safra.py
```

- [ ] **Step 6: Commit**

```bash
git add core/b3_portfolio_model.py tests/test_b3_portfolio_model_safra.py
git commit -m "fix(b3): carimbar safra e vigencia na carteira-modelo salva

ano_compra usava date.today().year: uma carteira salva em fevereiro de
2027 seria carimbada como 2027, quando a safra vigente ainda e a de 2026.
params_json passa a carregar safra e janela explicitas -- uma safra nunca
deve precisar ser inferida de created_at.

Nenhuma tabela nova: save_b3_portfolio_model ja arquiva a versao anterior,
entao o historico de carteiras-modelo ja se acumula.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Documentação e PR

**Files:**
- Modify: `docs/guia_metodologia_carteira_b3.md`
- Modify: `views/documentacao.py:458` (a linha "Publication lag = 1 (point-in-time)")

- [ ] **Step 1: Registrar a vigência no guia de metodologia**

Em `docs/guia_metodologia_carteira_b3.md`, acrescentar seção:

```markdown
## Vigência da safra

A safra N é pontuada com dados até N−1 (`lag = 1`) e vigora de **01/04/N a
31/03/N+1**: os balanços do exercício N−1 só são públicos até 31/03 (CVM).
A regra vive em `core/b3_vigencia.py` e é a única definição do mês de
rebalance no projeto — um teste de AST falha se outro arquivo redefinir.

O relatório de safras (`core/b3_safras.py`) reconstrói a carteira de cada
safra e mede o retorno da janela de vigência. Três limites declarados na
tela:

1. **Viés de universo.** O conjunto de segmentos vem do teste OOS sobre a
   amostra inteira; safras antigas são reconstruídas com segmentos
   aprovados por evidência posterior. O tamanho é medido no bloco "viés de
   universo", não apenas declarado.
2. **Pós-filtros não são PIT.** Piso de liquidez e diversificação por
   correlação usam dados de hoje. A carteira reconstruída é a dos líderes
   por segmento, sem eles.
3. **Peso sem preço rende zero.** Não é redistribuído entre sobreviventes.
```

- [ ] **Step 2: Atualizar a linha da tela de documentação**

Em `views/documentacao.py:458`, substituir `"Publication lag = 1 (point-in-time)"` por:

```python
                "Publication lag = 1 — safra N vigora de abril/N a março/N+1",
```

- [ ] **Step 3: Rodar a suíte inteira uma última vez**

```bash
"/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q
```

Confirmar verde. Se houver falha em teste que o diff não toca, rodar de novo antes de investigar — a suíte oscila em torno do TTL de cache de 900s.

- [ ] **Step 4: Validar com `GITHUB_ACTIONS=true`**

```bash
GITHUB_ACTIONS=true "/c/Users/Tiago Barros/AppData/Local/Programs/Python/Python312/python.exe" -m pytest tests -q
```

O CI roda com essa variável e alguns testes mudam de caminho por causa dela. Verde local não garante verde no CI.

- [ ] **Step 5: Commit e PR**

```bash
git add docs/guia_metodologia_carteira_b3.md views/documentacao.py
git commit -m "docs(b3): registrar a vigencia da safra e os limites do relatorio

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push -u origin b3-safras-vigencia
```

Abrir o PR com corpo descrevendo: o look-ahead corrigido, o relatório de safras, os três limites declarados, e o fato de que o bloco de expectativa publica banda em vez de número. Terminar com:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Após o merge, voltar para `main` — a rotina noturna que commita o snapshot de FII recusa rodar fora dela.

---

## Fora deste plano

- **Replay PIT do pipeline completo** (piso de liquidez, classes irmãs, correlação em janela da safra). Exige giro histórico derivado do COTAHIST no armazém local. A Task 8 deixa o gancho: com o tempo, comparar a safra reconstruída com a salva mede o efeito dos pós-filtros sem o replay.
- **FII e EUA.** Cadências diferentes (mensal e trimestral); abril seria errado nos dois. `core/b3_vigencia.py` é deliberadamente específico da B3 — generalizar antes de ter um segundo caso concreto produziria a abstração errada.
