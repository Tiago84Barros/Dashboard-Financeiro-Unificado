# IPCA Pessoal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nova aba "🧮  IPCA Pessoal" no Controle Financeiro, que calcula a inflação da cesta de consumo do usuário nos últimos 12 meses e a compara com o IPCA oficial do IBGE.

**Architecture:** Índice de Laspeyres com pesos fixos na janela, por categoria de despesa. A variação de preço vem dos itens recorrentes do próprio usuário quando eles cobrem ≥30% da categoria; senão, do IPCA do grupo equivalente (SIDRA 7060). A série do IBGE fica num arquivo versionado (`data/public/ipca_grupos.json.gz`), publicado por script e pela agenda de publicação — o Supabase está em 497/500 MB. A conta é pura (`core/ipca_pessoal.py`), a leitura é separada (`core/ipca_pessoal_dados.py`) e a tela só desenha (`views/controle_ipca_pessoal.py`).

**Tech Stack:** Python 3.11+, Streamlit, Plotly, pandas, SQLAlchemy (conexão existente de `core/database.py`), pytest.

**Design:** `docs/superpowers/specs/2026-11-23-ipca-pessoal-design.md`.

## Global Constraints

- Nenhuma conexão de banco nova: leitura só por `core.database.get_engine()`.
- Nenhuma tabela nova no Supabase (497/500 MB no plano free).
- A tela nunca chama o IBGE; só lê o arquivo publicado.
- Interface, lógica e coleta em módulos separados (`views/`, `core/`, `scripts/`).
- Nunca mostrar índice com dado faltando como se estivesse completo: toda lacuna vira aviso nomeado.
- Estrutura do app muda → Mapa do App, Documentação e Grau de Confiança mudam **no mesmo PR** (regra de 29/10/2026); `tests/test_configuracoes_sincronizadas.py` tem de passar.
- Textos de tela e de contexto em português, números no formato brasileiro (`10,03%`, `+1,42 p.p.`).
- Commits com `git add` por caminho explícito: a árvore tem muitos arquivos alheios modificados. Nunca `git add -A` / `git add .`. Nunca ler nem commitar `SECRET DO STREAMLIT.txt` ou `.env`.
- Cada mensagem de commit termina com a linha `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Rodar testes com `python -m pytest` a partir da raiz do repositório.

## File Structure

| Arquivo | Papel |
|---|---|
| `core/ipca_grupos.py` (novo) | Série do IBGE: grupos do SIDRA 7060, parser, serialização do arquivo, leitura, download, mapeamento categoria → grupo |
| `scripts/publicar_ipca_grupos.py` (novo) | Baixa do SIDRA e grava `data/public/ipca_grupos.json.gz` |
| `data/public/ipca_grupos.json.gz` (novo, versionado) | A série publicada |
| `core/publicacao_agenda.py` (alterar) | Alvo `ipca_grupos` na agenda semanal |
| `core/ipca_pessoal.py` (novo) | Conta pura: filtro, recorrentes, pesos, índice, contribuição, linhas de contexto do chat |
| `core/ipca_pessoal_dados.py` (novo) | Leitura dos lançamentos (Supabase ou mock) e conta do mês |
| `views/controle_ipca_pessoal.py` (novo) | A aba |
| `views/controle_financeiro.py` (alterar) | `_SECOES` + chamada da aba + IPCA no chat |
| `core/llm_context_financeiro.py` (alterar) | Bloco IPCA PESSOAL no contexto do chat |
| `core/confianca_secao.py` (alterar) | Componente "Série do IPCA por grupo" na seção Controle Financeiro |
| `views/documentacao.py` (alterar) | Aba nova no fluxo do Controle Financeiro |
| `docs/mapa_app/areas/controle_financeiro.json` (alterar) + `mapa.json` (regerado) | Ramo da aba nova |
| `tests/test_ipca_grupos_sidra.py`, `tests/test_ipca_pessoal.py`, `tests/test_ipca_pessoal_dados.py`, `tests/test_controle_ipca_pessoal_view.py`, `tests/test_confianca_ipca_grupos.py`, `tests/test_contexto_financeiro_ipca.py` (novos) | Testes |

---

### Task 1: Série do IPCA por grupo (SIDRA 7060) em arquivo publicado

**Files:**
- Create: `core/ipca_grupos.py`
- Create: `scripts/publicar_ipca_grupos.py`
- Create: `data/public/ipca_grupos.json.gz` (gerado pelo script)
- Modify: `core/publicacao_agenda.py` (depois do `Alvo` do CDI, que termina em `artefatos=("data/public/cdi_diario.json.gz",),\n    ),`)
- Test: `tests/test_ipca_grupos_sidra.py`

**Interfaces:**
- Consumes: `core.categorias.normalizar(value) -> str`.
- Produces:
  - `INDICE_GERAL: str = "Índice geral"`; `GRUPOS_SIDRA: dict[str, str]` (código → nome, Índice geral primeiro, depois os 9 grupos na ordem do IBGE); `URL_SIDRA: str`; `CAMINHO_IPCA_GRUPOS: Path`.
  - `@dataclass(frozen=True) SerieIpcaGrupos(variacao: dict[(date, str), float], peso: dict[(date, str), float], gerado_em: datetime | None)` — valores em % (0.48 = 0,48%); `meses() -> list[date]` (meses com Índice geral, ordenados); `ultimo_mes() -> date | None`.
  - `parse_sidra(linhas: list) -> SerieIpcaGrupos`
  - `serializar_ipca_grupos(serie, gerado_em: datetime) -> bytes`; `desserializar_ipca_grupos(dados: bytes) -> SerieIpcaGrupos`
  - `ler_ipca_grupos_publicado(caminho: Path | None = None) -> SerieIpcaGrupos` (série vazia quando o arquivo falta ou está ilegível; nunca levanta)
  - `baixar_sidra(*, timeout=45.0, obter=None) -> tuple[SerieIpcaGrupos, str | None]`
  - `grupo_da_categoria(nome: str) -> tuple[str, bool]` (grupo, definido)
  - `scripts.publicar_ipca_grupos.main(argv=None, *, baixar=baixar_sidra) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ipca_grupos_sidra.py`:

```python
"""Série do IPCA por grupo (IBGE, SIDRA 7060): parser, arquivo e publicador.

Sem rede: a resposta do SIDRA é uma fixture em linha, no formato real da API
(primeira linha = cabeçalho, ``D2C`` = variável, ``D3C`` = mês, ``D4C`` = grupo).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from core import ipca_grupos as ig
from core.ipca_grupos import INDICE_GERAL, SerieIpcaGrupos
from scripts import publicar_ipca_grupos as pub

CABECALHO = {"V": "Valor", "D1C": "Brasil (Código)", "D2C": "Variável (Código)",
             "D3C": "Mês (Código)",
             "D4C": "Geral, grupo, subgrupo, item e subitem (Código)"}


def _l(var, mes, grupo, valor):
    return {"D1C": "1", "D2C": var, "D3C": mes, "D4C": grupo, "V": valor}


LINHAS = [
    CABECALHO,
    _l("63", "202609", "7169", "0.48"),
    _l("63", "202610", "7169", "0.09"),
    _l("63", "202610", "7170", "-0.34"),
    _l("66", "202610", "7170", "21.7123"),
    _l("66", "202610", "7169", "100.0000"),
    _l("63", "202610", "7445", "..."),     # IBGE sem valor: ignora
    _l("63", "202610", "9999", "1.0"),     # grupo fora da lista: ignora
]
SET, OUT = date(2026, 9, 1), date(2026, 10, 1)
ALIM = "Alimentação e bebidas"


def test_parse_sidra_separa_variacao_e_peso():
    serie = ig.parse_sidra(LINHAS)
    assert serie.variacao == {(SET, INDICE_GERAL): 0.48, (OUT, INDICE_GERAL): 0.09,
                              (OUT, ALIM): -0.34}
    assert serie.peso == {(OUT, ALIM): 21.7123, (OUT, INDICE_GERAL): 100.0}
    assert serie.meses() == [SET, OUT]
    assert serie.ultimo_mes() == OUT


def test_serie_vazia_nao_tem_mes():
    assert SerieIpcaGrupos().meses() == []
    assert SerieIpcaGrupos().ultimo_mes() is None


def test_arquivo_ida_e_volta_com_bytes_deterministicos():
    serie = ig.parse_sidra(LINHAS)
    quando = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    dados = ig.serializar_ipca_grupos(serie, quando)
    assert dados[:2] == b"\x1f\x8b"
    assert dados == ig.serializar_ipca_grupos(serie, quando)
    volta = ig.desserializar_ipca_grupos(dados)
    assert volta.variacao == serie.variacao
    assert volta.peso == serie.peso
    assert volta.gerado_em == quando


def test_schema_desconhecido_e_recusado():
    import gzip
    dados = gzip.compress(json.dumps({"schema": 99, "linhas": []}).encode())
    with pytest.raises(ValueError):
        ig.desserializar_ipca_grupos(dados)


def test_ler_arquivo_ausente_ou_ilegivel_devolve_serie_vazia(tmp_path):
    assert ig.ler_ipca_grupos_publicado(tmp_path / "nao_existe.json.gz").meses() == []
    lixo = tmp_path / "lixo.json.gz"
    lixo.write_bytes(b"isto nao e gzip")
    assert ig.ler_ipca_grupos_publicado(lixo).meses() == []


def test_ler_arquivo_valido(tmp_path):
    alvo = tmp_path / "ipca.json.gz"
    alvo.write_bytes(ig.serializar_ipca_grupos(ig.parse_sidra(LINHAS),
                                               datetime(2026, 11, 23, tzinfo=timezone.utc)))
    assert ig.ler_ipca_grupos_publicado(alvo).ultimo_mes() == OUT


def test_url_pede_as_duas_variaveis_e_os_dez_grupos():
    assert ig.URL_SIDRA == (
        "https://apisidra.ibge.gov.br/values/t/7060/n1/all/v/63,66/p/all/c315/"
        "7169,7170,7445,7486,7558,7625,7660,7712,7766,7786")


def test_baixar_sidra_ok():
    chamadas = []

    def obter(url, timeout):
        chamadas.append((url, timeout))
        return LINHAS

    serie, erro = ig.baixar_sidra(obter=obter, timeout=10.0)
    assert erro is None
    assert serie.ultimo_mes() == OUT
    assert chamadas == [(ig.URL_SIDRA, 10.0)]


def test_baixar_sidra_nomeia_a_falha():
    def sem_rede(url, timeout):
        raise ConnectionError("sem rede")

    serie, erro = ig.baixar_sidra(obter=sem_rede)
    assert erro == "ConnectionError: sem rede"
    assert serie.meses() == []

    _, erro = ig.baixar_sidra(obter=lambda u, t: {"erro": "x"})
    assert "não é uma lista" in erro

    _, erro = ig.baixar_sidra(obter=lambda u, t: [CABECALHO])
    assert "sem nenhuma linha válida" in erro


@pytest.mark.parametrize("categoria, grupo, definido", [
    ("Moradia", "Habitação", True),
    ("Conta de Luz", "Habitação", True),
    ("Gás", "Habitação", True),
    ("Gastos gerais", INDICE_GERAL, False),   # "gas" não casa dentro de "gastos"
    ("Bem-Estar", "Saúde e cuidados pessoais", True),
    ("Tarifas & Anuidade", "Despesas pessoais", True),
    ("Supermercado", "Alimentação e bebidas", True),
    ("Alimentação", "Alimentação e bebidas", True),
    ("Transporte", "Transportes", True),
    ("Educação", "Educação", True),
    ("Vestuário", "Vestuário", True),
    ("Assinaturas", "Comunicação", True),
    ("Outros", INDICE_GERAL, False),
    ("A Revisar", INDICE_GERAL, False),
])
def test_grupo_da_categoria(categoria, grupo, definido):
    assert ig.grupo_da_categoria(categoria) == (grupo, definido)


def test_publicador_grava_o_arquivo(tmp_path, capsys):
    saida = tmp_path / "ipca_grupos.json.gz"
    codigo = pub.main(["--saida", str(saida)],
                      baixar=lambda: (ig.parse_sidra(LINHAS), None))
    assert codigo == 0
    assert ig.ler_ipca_grupos_publicado(saida).ultimo_mes() == OUT
    relatorio = json.loads(capsys.readouterr().out)
    assert relatorio["status"] == "ok"
    assert relatorio["gravado"] is True
    assert relatorio["meses"] == 2
    assert relatorio["ultimo"] == "2026-10"


def test_publicador_com_erro_nao_grava(tmp_path, capsys):
    saida = tmp_path / "ipca_grupos.json.gz"
    codigo = pub.main(["--saida", str(saida)],
                      baixar=lambda: (SerieIpcaGrupos(), "ConnectionError: sem rede"))
    assert codigo == 1
    assert not saida.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "erro"


def test_publicador_dry_run_nao_grava(tmp_path, capsys):
    saida = tmp_path / "ipca_grupos.json.gz"
    codigo = pub.main(["--saida", str(saida), "--dry-run"],
                      baixar=lambda: (ig.parse_sidra(LINHAS), None))
    assert codigo == 0
    assert not saida.exists()
    assert json.loads(capsys.readouterr().out)["gravado"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ipca_grupos_sidra.py -q`
Expected: FAIL / ERROR com `ModuleNotFoundError: No module named 'core.ipca_grupos'`.

- [ ] **Step 3: Write `core/ipca_grupos.py`**

```python
"""IPCA por grupo e pesos do IBGE (SIDRA, tabela 7060).

A série mora num arquivo versionado, ``data/public/ipca_grupos.json.gz``,
publicado por ``scripts/publicar_ipca_grupos.py`` (agenda de publicação,
alvo ``ipca_grupos``). A tela só lê o arquivo; nunca chama o IBGE. Não há
tabela no Supabase: o plano free está no teto (497/500 MB em 23/11/2026).

Variáveis da tabela 7060: 63 = variação mensal (%), 66 = peso mensal (%).
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from core.categorias import normalizar

logger = logging.getLogger(__name__)

CAMINHO_IPCA_GRUPOS = (Path(__file__).resolve().parents[1]
                       / "data" / "public" / "ipca_grupos.json.gz")
FONTE = "ibge_sidra_7060"
SCHEMA = 1
INDICE_GERAL = "Índice geral"

GRUPOS_SIDRA: dict[str, str] = {
    "7169": INDICE_GERAL,
    "7170": "Alimentação e bebidas",
    "7445": "Habitação",
    "7486": "Artigos de residência",
    "7558": "Vestuário",
    "7625": "Transportes",
    "7660": "Saúde e cuidados pessoais",
    "7712": "Despesas pessoais",
    "7766": "Educação",
    "7786": "Comunicação",
}
VAR_VARIACAO = "63"
VAR_PESO = "66"
URL_SIDRA = ("https://apisidra.ibge.gov.br/values/t/7060/n1/all/v/63,66/p/all/c315/"
             + ",".join(GRUPOS_SIDRA))

# Palavra-chave (já normalizada) → grupo. Casa por palavra inteira, para
# "gas" não casar dentro de "gastos". A primeira regra que casa vence.
_PALAVRAS_GRUPO: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Habitação", ("moradia", "condominio", "luz", "agua", "gas", "aluguel",
                   "casa & construcao")),
    ("Alimentação e bebidas", ("alimentacao", "mercado", "supermercado")),
    ("Transportes", ("transporte", "combustivel", "automotivo")),
    ("Saúde e cuidados pessoais", ("saude", "bem estar", "cuidados pessoais")),
    ("Educação", ("educacao",)),
    ("Despesas pessoais", ("lazer", "pets", "seguros", "despesas domesticas",
                           "tarifas & anuidade")),
    ("Vestuário", ("vestuario",)),
    ("Comunicação", ("assinaturas", "internet", "telefone")),
)


@dataclass(frozen=True)
class SerieIpcaGrupos:
    """``variacao[(mes, grupo)]`` e ``peso[(mes, grupo)]`` em % (0.48 = 0,48%).

    ``mes`` é sempre o dia 1 do mês.
    """
    variacao: dict = field(default_factory=dict)
    peso: dict = field(default_factory=dict)
    gerado_em: datetime | None = None

    def meses(self) -> list[date]:
        """Meses com Índice geral publicado, em ordem."""
        return sorted({m for (m, g) in self.variacao if g == INDICE_GERAL})

    def ultimo_mes(self) -> date | None:
        meses = self.meses()
        return meses[-1] if meses else None


def _float(valor: Any) -> float | None:
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_sidra(linhas: list) -> SerieIpcaGrupos:
    """Converte a resposta JSON do SIDRA. Ignora cabeçalho, grupo desconhecido
    e valor não numérico ("...", "-", "X")."""
    variacao: dict = {}
    peso: dict = {}
    for linha in linhas:
        if not isinstance(linha, dict):
            continue
        periodo = str(linha.get("D3C", ""))
        grupo = GRUPOS_SIDRA.get(str(linha.get("D4C", "")))
        valor = _float(linha.get("V"))
        if not re.fullmatch(r"\d{6}", periodo) or grupo is None or valor is None:
            continue
        try:
            mes = date(int(periodo[:4]), int(periodo[4:]), 1)
        except ValueError:
            continue
        variavel = str(linha.get("D2C", ""))
        if variavel == VAR_VARIACAO:
            variacao[(mes, grupo)] = valor
        elif variavel == VAR_PESO:
            peso[(mes, grupo)] = valor
    return SerieIpcaGrupos(variacao=variacao, peso=peso)


def serializar_ipca_grupos(serie: SerieIpcaGrupos, gerado_em: datetime) -> bytes:
    """Gzip determinístico (``mtime=0``, chaves ordenadas)."""
    chaves = sorted(set(serie.variacao) | set(serie.peso))
    linhas = [[f"{m:%Y-%m}", g, serie.variacao.get((m, g)), serie.peso.get((m, g))]
              for m, g in chaves]
    doc = {"schema": SCHEMA, "fonte": FONTE, "gerado_em": gerado_em.isoformat(),
           "linhas": linhas}
    texto = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return gzip.compress(texto.encode("utf-8"), mtime=0)


def desserializar_ipca_grupos(dados: bytes) -> SerieIpcaGrupos:
    doc = json.loads(gzip.decompress(dados).decode("utf-8"))
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"schema desconhecido: {doc.get('schema')!r}")
    variacao: dict = {}
    peso: dict = {}
    for mes_txt, grupo, var, pes in doc["linhas"]:
        mes = date(int(mes_txt[:4]), int(mes_txt[5:7]), 1)
        if var is not None:
            variacao[(mes, grupo)] = float(var)
        if pes is not None:
            peso[(mes, grupo)] = float(pes)
    gerado = doc.get("gerado_em")
    return SerieIpcaGrupos(variacao=variacao, peso=peso,
                           gerado_em=datetime.fromisoformat(gerado) if gerado else None)


def ler_ipca_grupos_publicado(caminho: Path | None = None) -> SerieIpcaGrupos:
    """Lê o arquivo publicado. Ausente ou ilegível → série vazia (quem chama
    nomeia a falha); nunca levanta."""
    alvo = Path(caminho) if caminho is not None else CAMINHO_IPCA_GRUPOS
    try:
        return desserializar_ipca_grupos(alvo.read_bytes())
    except (OSError, ValueError, EOFError, KeyError, TypeError) as exc:
        logger.info("ipca_grupos: arquivo %s ausente ou ilegível (%s)", alvo, exc)
        return SerieIpcaGrupos()


def _obter_requests(url: str, timeout: float) -> Any:
    import requests

    resposta = requests.get(url, timeout=timeout)
    resposta.raise_for_status()
    return resposta.json()


def baixar_sidra(*, timeout: float = 45.0,
                 obter: Callable[[str, float], Any] | None = None,
                 ) -> tuple[SerieIpcaGrupos, str | None]:
    """Baixa a tabela 7060. Devolve ``(serie, erro)``; ``erro`` nomeia a falha."""
    try:
        dados = (obter or _obter_requests)(URL_SIDRA, timeout)
    except Exception as exc:  # noqa: BLE001 -- a falha volta nomeada ao chamador
        return SerieIpcaGrupos(), f"{type(exc).__name__}: {exc}"
    if not isinstance(dados, list):
        return SerieIpcaGrupos(), (
            f"resposta do SIDRA não é uma lista ({type(dados).__name__})")
    serie = parse_sidra(dados)
    if not serie.variacao:
        return serie, "resposta do SIDRA sem nenhuma linha válida"
    return serie, None


def grupo_da_categoria(nome: str) -> tuple[str, bool]:
    """Grupo do IPCA para uma categoria do app: ``(grupo, definido)``.

    Sem palavra-chave conhecida → ``(INDICE_GERAL, False)``: a categoria usa o
    Índice geral e a tela marca "sem grupo definido".
    """
    texto = normalizar(nome)
    for grupo, palavras in _PALAVRAS_GRUPO:
        for palavra in palavras:
            if re.search(rf"(?<!\w){re.escape(palavra)}(?!\w)", texto):
                return grupo, True
    return INDICE_GERAL, False
```

- [ ] **Step 4: Write `scripts/publicar_ipca_grupos.py`**

```python
"""Publica o IPCA por grupo e os pesos do IBGE (SIDRA 7060) num arquivo versionado.

Uso:
    python scripts/publicar_ipca_grupos.py            # baixa e grava
    python scripts/publicar_ipca_grupos.py --dry-run  # baixa e só relata

Sem rede ou com resposta vazia, não grava nada e sai com código 1: o arquivo
anterior continua valendo e a aba IPCA Pessoal segue com ele.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ipca_grupos import (  # noqa: E402
    CAMINHO_IPCA_GRUPOS,
    baixar_sidra,
    serializar_ipca_grupos,
)


def main(argv: list[str] | None = None, *, baixar=baixar_sidra) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--saida", type=Path, default=CAMINHO_IPCA_GRUPOS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    serie, erro = baixar()
    meses = serie.meses()
    if erro or not meses:
        print(json.dumps({"status": "erro",
                          "erro": erro or "nenhum mês com Índice geral"},
                         ensure_ascii=False))
        return 1

    relatorio = {
        "status": "ok",
        "meses": len(meses),
        "primeiro": f"{meses[0]:%Y-%m}",
        "ultimo": f"{meses[-1]:%Y-%m}",
        "linhas": len(set(serie.variacao) | set(serie.peso)),
        "gravado": False,
    }
    if not args.dry_run:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_bytes(serializar_ipca_grupos(serie, datetime.now(timezone.utc)))
        relatorio["gravado"] = True
        relatorio["saida"] = str(args.saida)
    print(json.dumps(relatorio, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_ipca_grupos_sidra.py -q`
Expected: todos PASS.

- [ ] **Step 6: Publicar o arquivo de verdade**

Run: `python scripts/publicar_ipca_grupos.py`
Expected: JSON com `"status": "ok"`, `"ultimo": "2026-10"` (ou mais recente), `"gravado": true`. Conferir que `meses` ≥ 70 (a tabela começa em 01/2020).

- [ ] **Step 7: Registrar o alvo na agenda de publicação**

Em `core/publicacao_agenda.py`, logo depois do `Alvo` do CDI (o que termina em `artefatos=("data/public/cdi_diario.json.gz",),` seguido de `    ),`) e antes do `Alvo(chave="macro_brasil"...`, inserir:

```python
    Alvo(
        chave="ipca_grupos",
        titulo="IPCA por grupo e pesos do IBGE (SIDRA 7060)",
        passos=(("scripts/publicar_ipca_grupos.py",),),
        cadencia_dias=7,
        modulo="macro",
        precisa_armazem=False,
        artefatos=("data/public/ipca_grupos.json.gz",),
    ),
```

O teste da agenda exige o artefato rastreado pelo git; por isso o `git add` vem antes do teste:

Run: `git add data/public/ipca_grupos.json.gz && python -m pytest tests/test_publicacao_agenda.py -q`
Expected: todos PASS.

- [ ] **Step 8: Commit**

```bash
git add core/ipca_grupos.py scripts/publicar_ipca_grupos.py data/public/ipca_grupos.json.gz core/publicacao_agenda.py tests/test_ipca_grupos_sidra.py
git commit -m "IPCA Pessoal: serie do IPCA por grupo do IBGE (SIDRA 7060) em arquivo publicado

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A conta do IPCA Pessoal (módulo puro)

**Files:**
- Create: `core/ipca_pessoal.py`
- Test: `tests/test_ipca_pessoal.py`

**Interfaces:**
- Consumes (Task 1): `INDICE_GERAL`, `GRUPOS_SIDRA`, `SerieIpcaGrupos` (`variacao`/`peso` em %, `ultimo_mes()`), `grupo_da_categoria(nome) -> (grupo, definido)`; `core.controle.canonical_transaction_type(tipo, categoria="", amount=None) -> str`; `core.categorias.normalizar`.
- Produces:
  - Constantes `MESES_JANELA=12`, `ORIGEM_ITENS="seus itens"`, `ORIGEM_OFICIAL="estimativa oficial"`, `ORIGEM_SEM_GRUPO="sem grupo definido"`, `CARTAO_SEM_DETALHE="Cartão (sem detalhe)"`.
  - `Lancamento(data: date, valor: float, categoria: str, descricao: str, tipo: str, conta_tipo: str, fonte: str, parcela_total: int = 1)` — `valor` com o sinal do banco (saída < 0).
  - `PontoMensal(mes, pessoal, oficial, acum_pessoal, acum_oficial)` — frações (0.004 = 0,4%).
  - `CategoriaIpca(categoria, grupo, grupo_definido, peso, variacao_12m, origem, meses_proprios, cobertura, contribuicao, meses_fora: tuple[date, ...])`.
  - `Ignorado(item, categoria, mes, motivo)`.
  - `ResultadoIpcaPessoal(ok, motivo="", inicio=None, fim=None, acumulado_12m=None, oficial_12m=None, mensal=(), categorias=(), pesos_por_grupo=(), ignorados=(), avisos=())` — `pesos_por_grupo`: tuplas `(grupo, peso_usuario, peso_ibge | None)` em fração.
  - `somar_meses(d: date, n: int) -> date`, `normalizar_descricao(texto) -> str`, `filtrar_consumo(lancamentos) -> list[Lancamento]`, `calcular_ipca_pessoal(lancamentos, serie, ano, mes) -> ResultadoIpcaPessoal`, `pct_br(x, casas=2) -> str`, `pp_br(x) -> str`, `linhas_contexto_ipca(res | None) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ipca_pessoal.py`:

```python
"""A conta do IPCA Pessoal, com números pequenos conferidos à mão.

Convenção: ``FIM`` é o último mês da janela; ``_meses()`` devolve os 13 meses
de ``FIM − 12`` (base de preço) até ``FIM``. Descrições únicas por mês
(``_unico``) nunca viram item recorrente; descrições repetidas viram.
"""
from __future__ import annotations

from datetime import date

import pytest
from pytest import approx

from core import ipca_pessoal as ip
from core.ipca_grupos import INDICE_GERAL, SerieIpcaGrupos
from core.ipca_pessoal import Lancamento, calcular_ipca_pessoal, somar_meses

FIM = date(2026, 10, 1)
ALIM = "Alimentação e bebidas"


def _meses(fim=FIM):
    return [somar_meses(fim, -k) for k in range(12, -1, -1)]


def _serie(variacoes=None, *, geral=0.4, fim=FIM, faltando=(), pesos=None):
    """Série com variação mensal constante (em %) por grupo nos 13 meses até ``fim``.

    ``faltando``: pares ``(mes, grupo)`` que a série não tem.
    ``pesos``: ``{grupo: %}`` no mês ``fim``.
    """
    var = {}
    for m in _meses(fim):
        var[(m, INDICE_GERAL)] = geral
        for grupo, v in (variacoes or {}).items():
            var[(m, grupo)] = v
    for chave in faltando:
        var.pop(chave, None)
    peso = {(fim, g): p for g, p in (pesos or {}).items()}
    return SerieIpcaGrupos(variacao=var, peso=peso)


def _gasto(mes, valor, categoria, descricao, *, tipo="expense", conta="checking",
           fonte="manual", parcelas=1):
    """Saída de ``valor`` (positivo) no dia 10 de ``mes``."""
    return Lancamento(data=date(mes.year, mes.month, 10), valor=-float(valor),
                      categoria=categoria, descricao=descricao, tipo=tipo,
                      conta_tipo=conta, fonte=fonte, parcela_total=parcelas)


def _unico(prefixo, i):
    return f"{prefixo} {'abcdefghijklm'[i]}"


def _cesta_duas_categorias():
    """Alimentação 600/mês a 1% e Educação 400/mês a 0,5%, sem itens recorrentes."""
    lancs = []
    for i, m in enumerate(_meses()):
        lancs.append(_gasto(m, 600, "Alimentação", _unico("feira", i)))
        lancs.append(_gasto(m, 400, "Educação", _unico("curso", i)))
    serie = _serie({ALIM: 1.0, "Educação": 0.5},
                   pesos={ALIM: 21.5, "Educação": 6.0})
    return lancs, serie


def _cat(res, nome):
    return next(c for c in res.categorias if c.categoria == nome)


# ── normalização e filtro ────────────────────────────────────────────────────

@pytest.mark.parametrize("texto, esperado", [
    ("GIZELE [Despesas Domésticas]", "gizele"),
    ("Conta de Luz 10/2026", "conta de luz"),
    ("NETFLIX.COM | Compra 03/03/2026 | Cartão 9637", "netflix.com"),
    ("Açaí 2", "acai"),
    ("", ""),
    (None, ""),
])
def test_normalizar_descricao(texto, esperado):
    assert ip.normalizar_descricao(texto) == esperado


def test_filtro_mantem_so_consumo():
    m = FIM
    lancs = [
        _gasto(m, 100, "Alimentação", "Mercado"),
        Lancamento(date(2026, 10, 5), 5000.0, "Salário", "Salário", "income",
                   "checking", "manual"),
        _gasto(m, 300, "Transferência", "Pix poupança", tipo="transfer"),
        _gasto(m, 1000, "Renda Fixa", "CDB"),            # investimento pela categoria
        _gasto(m, 900, "Financiamento", "Parcela do carro"),
        _gasto(m, 50, "Impostos e Taxas", "IPVA"),
        _gasto(m, 20, "", "sem categoria"),
    ]
    saida = ip.filtrar_consumo(lancs)
    assert [(l.categoria, l.valor) for l in saida] == [
        ("Alimentação", 100.0), (ip.SEM_CATEGORIA, 20.0)]


def test_pagamento_de_fatura_so_entra_sem_o_detalhe_do_cartao():
    setembro, outubro = somar_meses(FIM, -1), FIM
    lancs = [
        _gasto(setembro, 1000, "Pagamento de Cartão", "Pagamento fatura"),
        _gasto(outubro, 1200, "Pagamento de Cartão", "Pagamento fatura"),
        _gasto(outubro, 80, "Alimentação", "IFOOD", conta="credit_card", fonte="csv"),
    ]
    saida = ip.filtrar_consumo(lancs)
    assert [(l.data.month, l.categoria, l.valor) for l in saida] == [
        (9, ip.CARTAO_SEM_DETALHE, 1000.0),
        (10, "Alimentação", 80.0),
    ]


# ── quando não há conta ──────────────────────────────────────────────────────

def test_doze_meses_de_gasto_nao_bastam():
    lancs = [_gasto(m, 100, "Alimentação", _unico("feira", i))
             for i, m in enumerate(_meses()) if i > 0]
    res = calcular_ipca_pessoal(lancs, _serie({ALIM: 1.0}), 2026, 10)
    assert not res.ok
    assert "há 12 meses e falta 1" in res.motivo
    assert "10/2025 a 10/2026" in res.motivo
    assert res.acumulado_12m is None


def test_sem_serie_do_ibge_nao_calcula():
    lancs, _ = _cesta_duas_categorias()
    res = calcular_ipca_pessoal(lancs, SerieIpcaGrupos(), 2026, 10)
    assert not res.ok
    assert "7060" in res.motivo


def test_indice_geral_faltando_num_mes_da_janela_nao_calcula():
    lancs, _ = _cesta_duas_categorias()
    buraco = somar_meses(FIM, -5)
    serie = _serie({ALIM: 1.0, "Educação": 0.5}, faltando=[(buraco, INDICE_GERAL)])
    res = calcular_ipca_pessoal(lancs, serie, 2026, 10)
    assert not res.ok
    assert "Índice geral" in res.motivo and f"{buraco:%m/%Y}" in res.motivo


# ── a conta ──────────────────────────────────────────────────────────────────

def test_encadeamento_duas_categorias():
    lancs, serie = _cesta_duas_categorias()
    res = calcular_ipca_pessoal(lancs, serie, 2026, 10)
    assert res.ok
    assert (res.inicio, res.fim) == (date(2025, 11, 1), FIM)
    assert len(res.mensal) == 12
    assert res.mensal[0].pessoal == approx(0.008)
    assert res.acumulado_12m == approx(1.008 ** 12 - 1)
    assert res.oficial_12m == approx(1.004 ** 12 - 1)
    assert res.mensal[-1].acum_pessoal == approx(res.acumulado_12m)
    alim, edu = res.categorias                     # ordem: maior contribuição
    assert (alim.categoria, edu.categoria) == ("Alimentação", "Educação")
    assert (alim.peso, edu.peso) == (approx(0.6), approx(0.4))
    assert alim.variacao_12m == approx(1.01 ** 12 - 1)
    assert alim.origem == edu.origem == ip.ORIGEM_OFICIAL
    assert alim.contribuicao == approx(0.75 * res.acumulado_12m)
    assert edu.contribuicao == approx(0.25 * res.acumulado_12m)


def test_condominio_recorrente_usa_seus_itens():
    lancs = [_gasto(m, 1000 * 1.01 ** k, "Moradia", "Condomínio Edifício Sol")
             for k, m in enumerate(_meses())]
    res = calcular_ipca_pessoal(lancs, _serie({"Habitação": 0.3}), 2026, 10)
    [cat] = res.categorias
    assert cat.origem == ip.ORIGEM_ITENS
    assert cat.meses_proprios == 12
    assert cat.cobertura == approx(1.0)
    assert cat.variacao_12m == approx(1.01 ** 12 - 1)
    assert res.acumulado_12m == approx(1.01 ** 12 - 1)


@pytest.mark.parametrize("meses_condo, origem, proprios, esperado", [
    (2, ip.ORIGEM_OFICIAL, 0, 1.003 ** 12 - 1),
    (3, ip.ORIGEM_ITENS, 2, 1.003 ** 10 * 1.01 ** 2 - 1),
])
def test_item_recorrente_precisa_de_3_meses(meses_condo, origem, proprios, esperado):
    meses = _meses()
    lancs = [_gasto(m, 100, "Moradia", _unico("reforma", i)) for i, m in enumerate(meses)]
    for k, m in enumerate(meses[-meses_condo:]):
        lancs.append(_gasto(m, 1000 * 1.01 ** k, "Moradia", "Condomínio"))
    res = calcular_ipca_pessoal(lancs, _serie({"Habitação": 0.3}), 2026, 10)
    [cat] = res.categorias
    assert cat.origem == origem
    assert cat.meses_proprios == proprios
    assert cat.variacao_12m == approx(esperado)


def test_parcela_nao_e_item_mas_conta_no_peso():
    lancs = []
    for i, m in enumerate(_meses()):
        lancs.append(_gasto(m, 500, "Vestuário", "Loja Renner", parcelas=10))
        lancs.append(_gasto(m, 500, "Alimentação", _unico("feira", i)))
    res = calcular_ipca_pessoal(lancs, _serie({"Vestuário": 0.2, ALIM: 1.0}), 2026, 10)
    vest = _cat(res, "Vestuário")
    assert vest.peso == approx(0.5)
    assert vest.cobertura == 0.0
    assert vest.origem == ip.ORIGEM_OFICIAL


def test_salto_acima_de_50_por_cento_e_ignorado():
    meses = _meses()
    lancs = [_gasto(m, 200 if i < 7 else 400, "Moradia", "Conta de Luz 10/2026")
             for i, m in enumerate(meses)]
    res = calcular_ipca_pessoal(lancs, _serie({"Habitação": 1.0}), 2026, 10)
    [cat] = res.categorias
    assert cat.origem == ip.ORIGEM_ITENS
    assert cat.meses_proprios == 11
    assert cat.variacao_12m == approx(0.01)      # o mês do salto usa o IPCA do grupo
    [ign] = res.ignorados
    assert (ign.item, ign.categoria, ign.mes) == ("conta de luz", "Moradia", meses[7])
    assert "+100%" in ign.motivo
    assert f"{meses[6]:%m/%Y} e {meses[7]:%m/%Y}" in ign.motivo


def test_cobertura_abaixo_de_30_por_cento_usa_estimativa_oficial():
    lancs = []
    for i, m in enumerate(_meses()):
        lancs.append(_gasto(m, 200, "Alimentação", "Padaria Pão Quente"))
        lancs.append(_gasto(m, 800, "Alimentação", _unico("mercado", i)))
    res = calcular_ipca_pessoal(lancs, _serie({ALIM: 1.0}), 2026, 10)
    [cat] = res.categorias
    assert cat.cobertura == approx(0.2)
    assert cat.origem == ip.ORIGEM_OFICIAL
    assert cat.meses_proprios == 0
    assert res.ignorados == ()


def test_categoria_sem_grupo_usa_indice_geral_e_avisa():
    lancs = [_gasto(m, 100, "Outros", _unico("coisa", i)) for i, m in enumerate(_meses())]
    res = calcular_ipca_pessoal(lancs, _serie(), 2026, 10)
    [cat] = res.categorias
    assert cat.origem == ip.ORIGEM_SEM_GRUPO
    assert (cat.grupo, cat.grupo_definido) == (INDICE_GERAL, False)
    assert res.acumulado_12m == approx(res.oficial_12m)
    assert any("sem grupo do IPCA definido" in a and "Outros" in a for a in res.avisos)
    nome, peso_usuario, peso_ibge = res.pesos_por_grupo[-1]
    assert (nome, peso_usuario, peso_ibge) == ("Sem grupo definido", approx(1.0), None)


def test_cartao_sem_detalhe_nunca_usa_seus_itens():
    lancs = [_gasto(m, 1000 * 1.01 ** k, "Pagamento de Cartão", "Pagamento fatura")
             for k, m in enumerate(_meses())]
    res = calcular_ipca_pessoal(lancs, _serie(), 2026, 10)
    [cat] = res.categorias
    assert cat.categoria == ip.CARTAO_SEM_DETALHE
    assert cat.origem == ip.ORIGEM_SEM_GRUPO
    assert cat.meses_proprios == 0


def test_mes_sem_dado_renormaliza_so_aquele_mes():
    buraco = somar_meses(FIM, -3)
    lancs = []
    for i, m in enumerate(_meses()):
        lancs.append(_gasto(m, 500, "Alimentação", _unico("feira", i)))
        lancs.append(_gasto(m, 500, "Educação", _unico("curso", i)))
    serie = _serie({ALIM: 1.0, "Educação": 0.5}, faltando=[(buraco, "Educação")])
    res = calcular_ipca_pessoal(lancs, serie, 2026, 10)
    assert res.ok
    assert res.acumulado_12m == approx(1.0075 ** 11 * 1.01 - 1)
    assert next(p for p in res.mensal if p.mes == buraco).pessoal == approx(0.01)
    assert _cat(res, "Educação").meses_fora == (buraco,)
    assert any("Educação" in a and f"{buraco:%m/%Y}" in a for a in res.avisos)


def test_contribuicoes_somam_o_acumulado():
    buraco = somar_meses(FIM, -5)
    lancs = []
    for i, m in enumerate(_meses()):
        lancs.append(_gasto(m, 1000 * 1.01 ** i, "Moradia", "Condomínio"))
        lancs.append(_gasto(m, 800, "Alimentação", _unico("feira", i)))
        lancs.append(_gasto(m, 300, "Transporte", _unico("posto", i)))
        lancs.append(_gasto(m, 100, "Outros", _unico("coisa", i)))
    serie = _serie({ALIM: 1.0, "Transportes": -0.3, "Habitação": 0.2},
                   faltando=[(buraco, "Transportes")])
    res = calcular_ipca_pessoal(lancs, serie, 2026, 10)
    assert res.ok
    assert sum(c.contribuicao for c in res.categorias) == approx(res.acumulado_12m)
    assert sum(c.peso for c in res.categorias) == approx(1.0)


def test_mes_pedido_alem_do_ibge_encosta_no_ultimo_mes_publicado():
    lancs, serie = _cesta_duas_categorias()
    res = calcular_ipca_pessoal(lancs, serie, 2026, 11)
    assert res.ok and res.fim == FIM
    assert any("10/2026" in a for a in res.avisos)
    assert not any("60 dias" in a for a in res.avisos)
    res = calcular_ipca_pessoal(lancs, serie, 2027, 2)
    assert res.ok and res.fim == FIM
    assert any("60 dias" in a for a in res.avisos)


def test_pesos_por_grupo_contra_o_ibge():
    lancs, serie = _cesta_duas_categorias()
    res = calcular_ipca_pessoal(lancs, serie, 2026, 10)
    pesos = {g: (u, i) for g, u, i in res.pesos_por_grupo}
    assert len(res.pesos_por_grupo) == 9
    assert INDICE_GERAL not in pesos
    assert pesos[ALIM] == (approx(0.6), approx(0.215))
    assert pesos["Educação"] == (approx(0.4), approx(0.06))
    assert pesos["Habitação"] == (0.0, None)


# ── formatação e contexto do chat ────────────────────────────────────────────

def test_formatos_brasileiros():
    assert ip.pct_br(0.10034) == "10,03%"
    assert ip.pct_br(0.6, 1) == "60,0%"
    assert ip.pp_br(0.0142) == "+1,42 p.p."
    assert ip.pp_br(-0.005) == "-0,50 p.p."


def test_linhas_de_contexto():
    texto = "\n".join(ip.linhas_contexto_ipca(None))
    assert "IPCA PESSOAL" in texto and "leitura dos lançamentos falhou" in texto

    falha = ip.ResultadoIpcaPessoal(ok=False, motivo="faltam dados")
    assert "Não calculado: faltam dados" in "\n".join(ip.linhas_contexto_ipca(falha))

    lancs, serie = _cesta_duas_categorias()
    res = calcular_ipca_pessoal(lancs, serie, 2026, 10)
    texto = "\n".join(ip.linhas_contexto_ipca(res))
    assert ip.pct_br(res.acumulado_12m) in texto
    assert ip.pct_br(res.oficial_12m) in texto
    assert "11/2025 a 10/2026" in texto
    assert "Alimentação" in texto and ip.ORIGEM_OFICIAL in texto
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ipca_pessoal.py -q`
Expected: ERROR com `ModuleNotFoundError: No module named 'core.ipca_pessoal'`.

- [ ] **Step 3: Write `core/ipca_pessoal.py`**

```python
"""IPCA Pessoal: a inflação da cesta de consumo do usuário.

Conta pura, sem banco: recebe lançamentos e a série do IBGE e devolve um
``ResultadoIpcaPessoal``. Método em
``docs/superpowers/specs/2026-11-23-ipca-pessoal-design.md``.

Índice de Laspeyres com pesos fixos na janela de 12 meses:

    w_c       = gasto da categoria c na janela / gasto total da janela
    I_t       = Σ_c (w_c / Σ_{c com dado em t} w_c) · r_{c,t}
    Acum_12m  = Π_t (1 + I_t) − 1
    Contrib_c = Acum_12m · Σ_t parte_{c,t} / Σ_t I_t

``r_{c,t}`` vem dos itens recorrentes da categoria ("seus itens") quando eles
cobrem ≥30% do gasto dela; nos meses sem par válido, e nas demais categorias,
vem do IPCA do grupo equivalente ("estimativa oficial"), ou do Índice geral
("sem grupo definido").
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date

from core.categorias import normalizar
from core.controle import canonical_transaction_type
from core.ipca_grupos import (
    GRUPOS_SIDRA,
    INDICE_GERAL,
    SerieIpcaGrupos,
    grupo_da_categoria,
)

MESES_JANELA = 12
MIN_MESES_RECORRENTE = 3
COBERTURA_MINIMA = 0.30
VARIACAO_MAXIMA = 0.50
ATRASO_MAXIMO_DIAS = 60

# Normalizadas (core.categorias.normalizar). Financiamento é dívida, não
# consumo; imposto e taxa o IPCA também não mede.
CATEGORIAS_FORA = frozenset({"financiamento", "impostos e taxas"})
CATEGORIAS_PAGAMENTO_CARTAO = frozenset({"pagamento de cartao", "pagamento de fatura"})
CARTAO_SEM_DETALHE = "Cartão (sem detalhe)"
SEM_CATEGORIA = "Sem categoria"
SEM_GRUPO_DEFINIDO = "Sem grupo definido"

ORIGEM_ITENS = "seus itens"
ORIGEM_OFICIAL = "estimativa oficial"
ORIGEM_SEM_GRUPO = "sem grupo definido"


@dataclass(frozen=True)
class Lancamento:
    data: date
    valor: float           # sinal do banco: saída < 0
    categoria: str
    descricao: str
    tipo: str              # transactions.type
    conta_tipo: str        # accounts.type ('' sem conta)
    fonte: str             # transactions.source ('manual', 'csv', ...)
    parcela_total: int = 1


@dataclass(frozen=True)
class PontoMensal:
    mes: date
    pessoal: float
    oficial: float
    acum_pessoal: float
    acum_oficial: float


@dataclass(frozen=True)
class CategoriaIpca:
    categoria: str
    grupo: str
    grupo_definido: bool
    peso: float
    variacao_12m: float
    origem: str
    meses_proprios: int
    cobertura: float
    contribuicao: float
    meses_fora: tuple[date, ...] = ()


@dataclass(frozen=True)
class Ignorado:
    item: str
    categoria: str
    mes: date
    motivo: str


@dataclass(frozen=True)
class ResultadoIpcaPessoal:
    ok: bool
    motivo: str = ""
    inicio: date | None = None
    fim: date | None = None
    acumulado_12m: float | None = None
    oficial_12m: float | None = None
    mensal: tuple[PontoMensal, ...] = ()
    categorias: tuple[CategoriaIpca, ...] = ()
    pesos_por_grupo: tuple[tuple[str, float, float | None], ...] = ()
    ignorados: tuple[Ignorado, ...] = ()
    avisos: tuple[str, ...] = ()


# ── utilitários de mês ───────────────────────────────────────────────────────

def somar_meses(d: date, n: int) -> date:
    """Dia 1 do mês ``n`` meses depois de ``d`` (``n`` pode ser negativo)."""
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _mes(d: date) -> date:
    return date(d.year, d.month, 1)


def _distancia_meses(a: date, b: date) -> int:
    """Meses de ``a`` até ``b``."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def _falha(motivo: str, avisos=()) -> ResultadoIpcaPessoal:
    return ResultadoIpcaPessoal(ok=False, motivo=motivo, avisos=tuple(avisos))


# ── formatação ───────────────────────────────────────────────────────────────

def pct_br(x: float, casas: int = 2) -> str:
    """0.10034 → '10,03%'."""
    return f"{x * 100:.{casas}f}".replace(".", ",") + "%"


def pp_br(x: float) -> str:
    """0.0142 → '+1,42 p.p.'."""
    return f"{x * 100:+.2f}".replace(".", ",") + " p.p."


# ── filtro e normalização ────────────────────────────────────────────────────

def normalizar_descricao(texto: str | None) -> str:
    """Chave do item recorrente: sem o que vem depois de ' | ', sem sufixo entre
    colchetes da importação antiga, sem acento/caixa e sem token só de dígitos."""
    base = str(texto or "").split(" | ", 1)[0]
    base = re.sub(r"\[[^\]]*\]", " ", base)
    base = normalizar(base)
    return " ".join(tok for tok in base.split() if any(ch.isalpha() for ch in tok))


def filtrar_consumo(lancamentos: list[Lancamento]) -> list[Lancamento]:
    """Só despesa de consumo, com ``valor`` positivo.

    Fora: entrada, transferência, investimento, Financiamento, Impostos e Taxas.
    Pagamento de fatura (fora da conta do cartão) entra como
    ``CARTAO_SEM_DETALHE`` só no mês em que a fatura não foi importada em CSV;
    no mês com CSV, as compras do cartão já estão lá, e contar o pagamento
    seria contar o mesmo gasto duas vezes.
    """
    meses_com_fatura_csv = {
        _mes(l.data) for l in lancamentos
        if l.conta_tipo == "credit_card" and l.fonte == "csv" and l.valor < 0
    }
    saida: list[Lancamento] = []
    for l in lancamentos:
        if l.valor >= 0:
            continue
        if canonical_transaction_type(l.tipo, l.categoria, l.valor) != "expense":
            continue
        normalizada = normalizar(l.categoria)
        if normalizada in CATEGORIAS_FORA:
            continue
        categoria = (l.categoria or "").strip() or SEM_CATEGORIA
        if normalizada in CATEGORIAS_PAGAMENTO_CARTAO and l.conta_tipo != "credit_card":
            if _mes(l.data) in meses_com_fatura_csv:
                continue
            categoria = CARTAO_SEM_DETALHE
        saida.append(replace(l, valor=-l.valor, categoria=categoria))
    return saida


# ── preço pelos itens do usuário ─────────────────────────────────────────────

def _variacao_por_itens(linhas: list[Lancamento], categoria: str, base: date,
                        janela: list[date]):
    """Variação mensal pelos itens recorrentes de uma categoria.

    Devolve ``(r, cobertura, ignorados)``: ``r[mes]`` só nos meses com ao menos
    um par válido; ``cobertura`` = gasto recorrente na janela / gasto da
    categoria na janela.
    """
    meses_validos = {base, *janela}
    janela_set = set(janela)
    por_item: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for l in linhas:
        if l.parcela_total > 1:
            continue
        item = normalizar_descricao(l.descricao)
        mes = _mes(l.data)
        if item and mes in meses_validos:
            por_item[item][mes] += l.valor
    recorrentes = {i: v for i, v in por_item.items() if len(v) >= MIN_MESES_RECORRENTE}

    gasto_categoria = sum(l.valor for l in linhas if _mes(l.data) in janela_set)
    gasto_recorrente = sum(v for valores in recorrentes.values()
                           for m, v in valores.items() if m in janela_set)
    cobertura = gasto_recorrente / gasto_categoria if gasto_categoria > 0 else 0.0

    r: dict[date, float] = {}
    ignorados: list[Ignorado] = []
    for t in janela:
        anterior = somar_meses(t, -1)
        soma_base = 0.0
        soma_ponderada = 0.0
        for item, valores in sorted(recorrentes.items()):
            if anterior not in valores or t not in valores or valores[anterior] <= 0:
                continue
            v = valores[t] / valores[anterior] - 1
            if abs(v) > VARIACAO_MAXIMA:
                ignorados.append(Ignorado(
                    item, categoria, t,
                    f"variação de {v:+.0%} entre {anterior:%m/%Y} e {t:%m/%Y}: "
                    "tratada como troca de produto ou erro de lançamento"))
                continue
            soma_base += valores[anterior]
            soma_ponderada += valores[anterior] * v
        if soma_base > 0:
            r[t] = soma_ponderada / soma_base
    return r, cobertura, ignorados


# ── a conta ──────────────────────────────────────────────────────────────────

def calcular_ipca_pessoal(lancamentos: list[Lancamento], serie: SerieIpcaGrupos,
                          ano: int, mes: int) -> ResultadoIpcaPessoal:
    avisos: list[str] = []
    ultimo = serie.ultimo_mes()
    if ultimo is None:
        return _falha("A série do IPCA por grupo do IBGE (SIDRA, tabela 7060) não "
                      "está disponível: o arquivo publicado está ausente ou ilegível.")

    pedido = date(ano, mes, 1)
    fim = min(pedido, ultimo)
    if fim < pedido:
        avisos.append(f"O IBGE publicou o IPCA até {ultimo:%m/%Y}; a janela termina "
                      f"nesse mês, e não em {pedido:%m/%Y}.")
        if (pedido - ultimo).days > ATRASO_MAXIMO_DIAS:
            avisos.append(f"A série do IBGE está com mais de {ATRASO_MAXIMO_DIAS} dias "
                          "de atraso em relação ao mês pedido.")
    base = somar_meses(fim, -MESES_JANELA)
    janela = [somar_meses(base, k) for k in range(1, MESES_JANELA + 1)]
    janela_set = set(janela)

    consumo_ate_fim = [l for l in filtrar_consumo(lancamentos) if _mes(l.data) <= fim]
    primeiro = min((_mes(l.data) for l in consumo_ate_fim), default=None)
    if primeiro is None or primeiro > base:
        ha = 0 if primeiro is None else _distancia_meses(primeiro, fim) + 1
        falta = MESES_JANELA + 1 - ha
        return _falha(
            f"São precisos {MESES_JANELA + 1} meses de gastos ({base:%m/%Y} a "
            f"{fim:%m/%Y}); há {ha} meses e falta{'m' if falta > 1 else ''} {falta}.",
            avisos)
    consumo = [l for l in consumo_ate_fim if _mes(l.data) >= base]

    gasto: dict[str, float] = defaultdict(float)
    for l in consumo:
        if _mes(l.data) in janela_set:
            gasto[l.categoria] += l.valor
    total = sum(gasto.values())
    if total <= 0:
        return _falha(f"Não há gasto de consumo entre {janela[0]:%m/%Y} e {fim:%m/%Y}.",
                      avisos)
    pesos = {c: v / total for c, v in sorted(gasto.items()) if v > 0}

    oficial: dict[date, float] = {}
    for t in janela:
        v = serie.variacao.get((t, INDICE_GERAL))
        if v is None:
            return _falha(f"A série do IBGE não tem o Índice geral de {t:%m/%Y}.", avisos)
        oficial[t] = v / 100

    linhas_por_categoria: dict[str, list[Lancamento]] = defaultdict(list)
    for l in consumo:
        linhas_por_categoria[l.categoria].append(l)

    r_cat: dict[str, dict[date, float]] = {}
    info: dict[str, tuple] = {}
    ignorados: list[Ignorado] = []
    for cat in pesos:
        grupo, definido = grupo_da_categoria(cat)
        if cat == CARTAO_SEM_DETALHE:
            r_itens, cobertura, ign = {}, 0.0, []
        else:
            r_itens, cobertura, ign = _variacao_por_itens(
                linhas_por_categoria[cat], cat, base, janela)
        usa_itens = cobertura >= COBERTURA_MINIMA and bool(r_itens)
        r: dict[date, float] = {}
        fora: list[date] = []
        proprios = 0
        for t in janela:
            oficial_grupo = serie.variacao.get((t, grupo))
            if usa_itens and t in r_itens:
                r[t] = r_itens[t]
                proprios += 1
            elif oficial_grupo is not None:
                r[t] = oficial_grupo / 100
            else:
                fora.append(t)
        if usa_itens:
            origem = ORIGEM_ITENS
            ignorados.extend(ign)
        else:
            origem = ORIGEM_OFICIAL if definido else ORIGEM_SEM_GRUPO
        r_cat[cat] = r
        info[cat] = (grupo, definido, origem, proprios, cobertura, tuple(fora))

    mensal_pessoal: dict[date, float] = {}
    soma_partes: dict[str, float] = defaultdict(float)
    for t in janela:
        disponiveis = [c for c in pesos if t in r_cat[c]]
        soma_w = sum(pesos[c] for c in disponiveis)
        if soma_w <= 0:
            return _falha(f"Nenhuma categoria tem variação de preço em {t:%m/%Y}.",
                          avisos)
        indice = 0.0
        for c in disponiveis:
            parte = pesos[c] / soma_w * r_cat[c][t]
            soma_partes[c] += parte
            indice += parte
        mensal_pessoal[t] = indice

    mensal: list[PontoMensal] = []
    fator_pessoal = fator_oficial = 1.0
    for t in janela:
        fator_pessoal *= 1 + mensal_pessoal[t]
        fator_oficial *= 1 + oficial[t]
        mensal.append(PontoMensal(t, mensal_pessoal[t], oficial[t],
                                  fator_pessoal - 1, fator_oficial - 1))
    acumulado = fator_pessoal - 1
    soma_indices = sum(mensal_pessoal.values())
    reparte = abs(soma_indices) >= 1e-12
    if not reparte:
        avisos.append("A soma das variações mensais é zero: a repartição do "
                      "acumulado por categoria é aproximada.")

    categorias: list[CategoriaIpca] = []
    for c in pesos:
        grupo, definido, origem, proprios, cobertura, fora = info[c]
        contribuicao = (acumulado * soma_partes[c] / soma_indices if reparte
                        else soma_partes[c])
        categorias.append(CategoriaIpca(
            categoria=c, grupo=grupo, grupo_definido=definido, peso=pesos[c],
            variacao_12m=math.prod(1 + x for x in r_cat[c].values()) - 1,
            origem=origem, meses_proprios=proprios, cobertura=cobertura,
            contribuicao=contribuicao, meses_fora=fora))
        if fora:
            avisos.append(
                f"{c}: sem variação de preço em "
                f"{', '.join(f'{m:%m/%Y}' for m in fora)}; nesses meses o índice foi "
                "calculado sem ela, com os pesos das outras categorias renormalizados.")
    categorias.sort(key=lambda x: (-x.contribuicao, x.categoria))

    sem_grupo = [c for c in categorias if not c.grupo_definido]
    peso_sem_grupo = sum(c.peso for c in sem_grupo)
    if sem_grupo:
        nomes = ", ".join(c.categoria for c in sorted(sem_grupo, key=lambda x: -x.peso))
        avisos.append(
            f"{len(sem_grupo)} categoria(s) sem grupo do IPCA definido "
            f"({pct_br(peso_sem_grupo, 1)} do gasto) usam o Índice geral quando não "
            f"há medida pelos seus itens: {nomes}.")

    peso_grupo: dict[str, float] = defaultdict(float)
    for c in categorias:
        if c.grupo_definido:
            peso_grupo[c.grupo] += c.peso
    pesos_por_grupo: list[tuple[str, float, float | None]] = []
    for g in GRUPOS_SIDRA.values():
        if g == INDICE_GERAL:
            continue
        ibge = serie.peso.get((fim, g))
        pesos_por_grupo.append((g, peso_grupo.get(g, 0.0),
                                None if ibge is None else ibge / 100))
    if sem_grupo:
        pesos_por_grupo.append((SEM_GRUPO_DEFINIDO, peso_sem_grupo, None))

    return ResultadoIpcaPessoal(
        ok=True, inicio=janela[0], fim=fim, acumulado_12m=acumulado,
        oficial_12m=fator_oficial - 1, mensal=tuple(mensal),
        categorias=tuple(categorias), pesos_por_grupo=tuple(pesos_por_grupo),
        ignorados=tuple(sorted(ignorados, key=lambda i: (i.mes, i.categoria, i.item))),
        avisos=tuple(avisos))


# ── contexto do chat ─────────────────────────────────────────────────────────

def linhas_contexto_ipca(res: ResultadoIpcaPessoal | None) -> list[str]:
    """Bloco do contexto do chat financeiro. ``None`` = a leitura falhou."""
    linhas = ["", "IPCA PESSOAL (inflação da sua cesta, 12 meses):"]
    if res is None:
        linhas.append("  (não calculado nesta conversa: a leitura dos lançamentos "
                      "falhou — não conclua nada sobre a inflação pessoal)")
        return linhas
    if not res.ok:
        linhas.append(f"  Não calculado: {res.motivo}")
        return linhas
    linhas.append(f"  Janela: {res.inicio:%m/%Y} a {res.fim:%m/%Y}")
    linhas.append(
        f"  IPCA Pessoal: {pct_br(res.acumulado_12m)}; IPCA oficial (IBGE, Índice "
        f"geral): {pct_br(res.oficial_12m)}; diferença: "
        f"{pp_br(res.acumulado_12m - res.oficial_12m)}")
    linhas.append("  Maiores contribuições:")
    for c in res.categorias[:3]:
        linhas.append(
            f"    - {c.categoria}: peso {pct_br(c.peso, 1)}, variação de preço "
            f"{pct_br(c.variacao_12m)}, contribuição {pp_br(c.contribuicao)}, "
            f"origem: {c.origem}")
    for aviso in res.avisos:
        linhas.append(f"  Aviso: {aviso}")
    linhas.append(f"  Origem '{ORIGEM_ITENS}' = preço medido nos lançamentos "
                  f"recorrentes do usuário; '{ORIGEM_OFICIAL}' = variação do grupo "
                  f"do IPCA; '{ORIGEM_SEM_GRUPO}' = Índice geral.")
    return linhas
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ipca_pessoal.py -q`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add core/ipca_pessoal.py tests/test_ipca_pessoal.py
git commit -m "IPCA Pessoal: conta pura (Laspeyres por categoria, itens recorrentes e IPCA do grupo)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Leitura dos lançamentos e a conta do mês

**Files:**
- Create: `core/ipca_pessoal_dados.py`
- Test: `tests/test_ipca_pessoal_dados.py`

**Interfaces:**
- Consumes: Task 2 (`Lancamento`, `ResultadoIpcaPessoal`, `calcular_ipca_pessoal`, `somar_meses`, `ORIGEM_*`); Task 1 (`ler_ipca_grupos_publicado`, `SerieIpcaGrupos`, `INDICE_GERAL`); `core.previstos.sql_realizado(t, conta)`; `core.user_context.user_cache_data(ttl=...)`; `core.config.settings` (`MOCK_MODE`, `OWNER_USER_ID`); `core.database.get_engine()`.
- Produces:
  - `MESES_LIDOS = 24`
  - `carregar_lancamentos_ipca(ano: int, mes: int) -> list[Lancamento] | None` (cache por usuário; `None` quando a leitura falha)
  - `ipca_pessoal_do_mes(ano: int, mes: int) -> ResultadoIpcaPessoal | None` (`None` = leitura falhou)
  - `lancamentos_mock(ano: int, mes: int) -> list[Lancamento]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ipca_pessoal_dados.py`:

```python
"""Leitura do IPCA Pessoal: mock determinístico, falha propagada e série publicada."""
from __future__ import annotations

from datetime import date

from core import ipca_pessoal_dados as dados
from core.ipca_grupos import INDICE_GERAL, SerieIpcaGrupos
from core.ipca_pessoal import (
    ORIGEM_ITENS,
    ORIGEM_OFICIAL,
    calcular_ipca_pessoal,
    somar_meses,
)
from core.previstos import sql_realizado

FIM = date(2026, 10, 1)


def _serie():
    var = {}
    for k in range(24):
        m = somar_meses(FIM, -k)
        var.update({(m, INDICE_GERAL): 0.4, (m, "Habitação"): 0.3,
                    (m, "Alimentação e bebidas"): 0.5, (m, "Transportes"): 0.2})
    return SerieIpcaGrupos(variacao=var)


def test_mock_da_um_resultado_completo():
    res = calcular_ipca_pessoal(dados.lancamentos_mock(2026, 10), _serie(), 2026, 10)
    assert res.ok
    cats = {c.categoria: c for c in res.categorias}
    assert cats["Moradia"].origem == ORIGEM_ITENS
    assert cats["Moradia"].meses_proprios == 12
    assert cats["Alimentação"].origem == ORIGEM_OFICIAL
    assert cats["Transporte"].origem == ORIGEM_OFICIAL


def test_mock_e_deterministico():
    assert dados.lancamentos_mock(2026, 10) == dados.lancamentos_mock(2026, 10)


def test_falha_de_leitura_vira_none(monkeypatch):
    monkeypatch.setattr(dados, "carregar_lancamentos_ipca", lambda ano, mes: None)
    assert dados.ipca_pessoal_do_mes(2026, 10) is None


def test_conta_do_mes_usa_a_serie_publicada(monkeypatch):
    monkeypatch.setattr(dados, "carregar_lancamentos_ipca",
                        lambda ano, mes: dados.lancamentos_mock(ano, mes))
    monkeypatch.setattr(dados, "ler_ipca_grupos_publicado", _serie)
    res = dados.ipca_pessoal_do_mes(2026, 10)
    assert res.ok and res.fim == FIM


def test_sql_le_so_realizado_e_saida():
    assert sql_realizado("t", "ac") in dados._SQL
    assert "t.amount < 0" in dados._SQL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ipca_pessoal_dados.py -q`
Expected: ERROR com `ModuleNotFoundError: No module named 'core.ipca_pessoal_dados'`.

- [ ] **Step 3: Write `core/ipca_pessoal_dados.py`**

```python
"""Leitura dos lançamentos para o IPCA Pessoal e a conta do mês.

Usa a conexão única do app (``core.database.get_engine``). A conta mora em
``core.ipca_pessoal``; a série do IBGE, em ``core.ipca_grupos``.
"""
from __future__ import annotations

import logging
from datetime import date

from core.config import settings
from core.ipca_grupos import ler_ipca_grupos_publicado
from core.ipca_pessoal import (
    Lancamento,
    ResultadoIpcaPessoal,
    calcular_ipca_pessoal,
    somar_meses,
)
from core.previstos import sql_realizado
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)

# 13 meses fazem a janela; 24 dão folga para a janela recuar até o último mês
# publicado pelo IBGE.
MESES_LIDOS = 24

_SQL = """
    SELECT t.due_date,
           t.amount,
           COALESCE(c.name, '')              AS categoria,
           COALESCE(t.description, '')       AS descricao,
           COALESCE(t.type, '')              AS tipo,
           COALESCE(ac.type, '')             AS conta_tipo,
           COALESCE(t.source, '')            AS fonte,
           COALESCE(t.installment_total, 1)  AS parcela_total
    FROM   transactions t
    LEFT JOIN accounts   ac ON ac.id = t.account_id
    LEFT JOIN categories c  ON c.id  = t.category_id
    WHERE  t.user_id = :uid
      AND  t.amount < 0
      AND  t.due_date >= :inicio
      AND  t.due_date <  :fim
      AND  """ + sql_realizado("t", "ac")


@user_cache_data(ttl=300)
def carregar_lancamentos_ipca(ano: int, mes: int) -> list[Lancamento] | None:
    """Saídas realizadas dos 24 meses até ``ano/mes``; ``None`` se a leitura falha."""
    if settings.MOCK_MODE:
        return lancamentos_mock(ano, mes)
    pedido = date(ano, mes, 1)
    params = {"uid": settings.OWNER_USER_ID,
              "inicio": somar_meses(pedido, 1 - MESES_LIDOS),
              "fim": somar_meses(pedido, 1)}
    try:
        from sqlalchemy import text

        from core.database import get_engine

        with get_engine().connect() as conn:
            rows = conn.execute(text(_SQL), params).fetchall()
    except Exception as exc:  # noqa: BLE001 -- a tela nomeia a falha
        logger.warning("ipca_pessoal: leitura dos lançamentos falhou: %s", exc)
        return None
    return [
        Lancamento(data=r.due_date, valor=float(r.amount), categoria=r.categoria,
                   descricao=r.descricao, tipo=r.tipo, conta_tipo=r.conta_tipo,
                   fonte=r.fonte, parcela_total=int(r.parcela_total or 1))
        for r in rows
    ]


def ipca_pessoal_do_mes(ano: int, mes: int) -> ResultadoIpcaPessoal | None:
    """A conta para o mês escolhido; ``None`` quando a leitura dos lançamentos falha."""
    lancamentos = carregar_lancamentos_ipca(ano, mes)
    if lancamentos is None:
        return None
    return calcular_ipca_pessoal(lancamentos, ler_ipca_grupos_publicado(), ano, mes)


# Nomes que giram a cada 7 meses: nenhum aparece 3 vezes em 13 meses, então
# Alimentação e Transporte caem na estimativa oficial; Moradia usa os itens.
_LOJAS = ("Supermercado Bom Preço", "Atacadão Centro", "Feira do Produtor",
          "Mercadinho da Esquina", "Hortifruti Verde", "Açougue Boi Gordo",
          "Padaria Trigo Bom")
_POSTOS = ("Posto Shell Centro", "Posto Ipiranga Sul", "Uber", "Posto BR Norte",
           "Estacionamento Shopping", "99 Táxi", "Posto Ale Rodovia")


def lancamentos_mock(ano: int, mes: int) -> list[Lancamento]:
    """Cesta fictícia e determinística de 24 meses, para o modo de teste."""
    pedido = date(ano, mes, 1)
    saida: list[Lancamento] = []
    for k in range(MESES_LIDOS):
        m = somar_meses(pedido, k - (MESES_LIDOS - 1))
        saida += [
            Lancamento(date(m.year, m.month, 5), -round(800 * 1.005 ** k, 2),
                       "Moradia", "Condomínio Edifício Sol", "expense", "checking",
                       "manual"),
            Lancamento(date(m.year, m.month, 12), -round(180 * 1.01 ** k, 2),
                       "Moradia", "Conta de Luz", "expense", "checking", "manual"),
            Lancamento(date(m.year, m.month, 8), -1200.0, "Alimentação",
                       _LOJAS[k % len(_LOJAS)], "expense", "checking", "manual"),
            Lancamento(date(m.year, m.month, 15), -450.0, "Transporte",
                       _POSTOS[k % len(_POSTOS)], "expense", "checking", "manual"),
        ]
    return saida
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ipca_pessoal_dados.py -q`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add core/ipca_pessoal_dados.py tests/test_ipca_pessoal_dados.py
git commit -m "IPCA Pessoal: leitura dos lancamentos (conexao unica) e cesta de teste

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: A aba, o menu do Controle Financeiro, o Mapa do App e a Documentação

As três mudanças vão no mesmo commit: `tests/test_configuracoes_sincronizadas.py` quebra se `_SECOES` mudar sem o Mapa e a Documentação.

**Files:**
- Create: `views/controle_ipca_pessoal.py`
- Modify: `views/controle_financeiro.py` (import perto da linha 80; `_SECOES` perto da linha 3886)
- Modify: `views/documentacao.py` (`FLOW_CONTROLE_FINANCEIRO`, perto das linhas 928, 933, 1044 e do nó `graficos`)
- Modify: `docs/mapa_app/areas/controle_financeiro.json` (linha 4 e fim do arquivo); regenerar o mapa
- Test: `tests/test_controle_ipca_pessoal_view.py`

**Interfaces:**
- Consumes: Task 2 (`ResultadoIpcaPessoal`, `CategoriaIpca`, `PontoMensal`, `ORIGEM_ITENS`, `ORIGEM_SEM_GRUPO`, `MESES_JANELA`, `pct_br`, `pp_br`); Task 3 (`ipca_pessoal_do_mes`); `design.componentes.card_metrica(titulo, valor, delta=None, positivo=None, ...)`, `design.componentes.estado_vazio(mensagem, icone)`; `design.lacunas.aviso_lacuna(mensagem, *, codigo, nivel="info")` (só registra; a tela mostra), `design.lacunas.falha_de_acao(mensagem, exc)`.
- Produces: `render_ipca_pessoal(ano: int, mes: int) -> None`; funções puras `rotulo_origem(cat) -> str`, `tabela_categorias(res) -> pd.DataFrame`, `tabela_pesos(res) -> pd.DataFrame`, `grafico_acumulado(res) -> go.Figure`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_controle_ipca_pessoal_view.py`:

```python
"""Partes puras da aba IPCA Pessoal (tabelas, rótulos, gráfico)."""
from __future__ import annotations

from datetime import date

from pytest import approx

from core.ipca_grupos import INDICE_GERAL
from core.ipca_pessoal import (
    ORIGEM_ITENS,
    ORIGEM_SEM_GRUPO,
    CategoriaIpca,
    PontoMensal,
    ResultadoIpcaPessoal,
)
from views import controle_ipca_pessoal as v


def _res():
    cats = (
        CategoriaIpca("Moradia", "Habitação", True, 0.6, 0.05, ORIGEM_ITENS, 11, 0.9, 0.03),
        CategoriaIpca("Outros", INDICE_GERAL, False, 0.4, 0.04, ORIGEM_SEM_GRUPO, 0, 0.0, 0.016),
    )
    return ResultadoIpcaPessoal(
        ok=True, inicio=date(2025, 11, 1), fim=date(2026, 10, 1),
        acumulado_12m=0.046, oficial_12m=0.048,
        mensal=(PontoMensal(date(2025, 11, 1), 0.004, 0.003, 0.004, 0.003),),
        categorias=cats,
        pesos_por_grupo=(("Habitação", 0.6, 0.15), ("Educação", 0.0, None),
                         ("Sem grupo definido", 0.4, None)),
    )


def test_rotulo_origem():
    moradia, outros = _res().categorias
    assert v.rotulo_origem(moradia) == "seus itens (11 de 12 meses)"
    assert v.rotulo_origem(outros) == "sem grupo definido"


def test_tabela_categorias():
    df = v.tabela_categorias(_res())
    assert list(df.columns) == ["Categoria", "Grupo do IPCA", "Peso",
                                "Variação de preço (12m)", "Origem", "Contribuição"]
    linha = df.iloc[0]
    assert (linha["Categoria"], linha["Peso"], linha["Variação de preço (12m)"],
            linha["Contribuição"]) == ("Moradia", "60,0%", "5,00%", "+3,00 p.p.")


def test_tabela_pesos():
    df = v.tabela_pesos(_res())
    assert list(df.columns) == ["Grupo do IPCA", "Seu peso", "Peso no IPCA (IBGE)"]
    assert df.iloc[0]["Peso no IPCA (IBGE)"] == "15,0%"
    assert df.iloc[1]["Peso no IPCA (IBGE)"] == "—"
    assert df.iloc[2]["Grupo do IPCA"] == "Sem grupo definido"


def test_grafico_tem_as_duas_linhas_em_percentual():
    fig = v.grafico_acumulado(_res())
    assert [t.name for t in fig.data] == ["IPCA Pessoal", "IPCA oficial"]
    assert fig.data[0].y[0] == approx(0.4)
    assert fig.data[1].y[0] == approx(0.3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_controle_ipca_pessoal_view.py -q`
Expected: ERROR com `ModuleNotFoundError: No module named 'views.controle_ipca_pessoal'`.

- [ ] **Step 3: Write `views/controle_ipca_pessoal.py`**

```python
"""Aba "🧮  IPCA Pessoal" do Controle Financeiro: só desenha.

A conta mora em ``core/ipca_pessoal.py``; a leitura, em
``core/ipca_pessoal_dados.py``; a série do IBGE, em ``core/ipca_grupos.py``.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.ipca_pessoal import (
    MESES_JANELA,
    ORIGEM_ITENS,
    CategoriaIpca,
    ResultadoIpcaPessoal,
    pct_br,
    pp_br,
)
from core.ipca_pessoal_dados import ipca_pessoal_do_mes
from design.componentes import card_metrica, estado_vazio
from design.lacunas import aviso_lacuna, falha_de_acao

_COR_PESSOAL = "#FC5C7D"
_COR_OFICIAL = "#4A9EFF"

_EXPLICACAO = """
O IPCA do IBGE mede a inflação da família média: cada grupo de gasto
(alimentação, habitação, transportes…) entra com o peso que tem no orçamento
dessa família. Aqui a conta é a mesma, mas com **o seu** orçamento.

1. **Peso de cada categoria** = quanto você gastou nela nos 12 meses ÷ o seu
   gasto total no período. O peso fica parado nos 12 meses, como no IBGE.
2. **Variação de preço de cada categoria, mês a mês:**
   - **seus itens** — quando a categoria tem gastos que se repetem (mesma
     descrição em 3 meses ou mais, sem parcelamento) e eles somam pelo menos
     30% do gasto dela, a variação vem desses valores. Num mês sem par
     comparável, vale a do grupo do IPCA.
   - **estimativa oficial** — a variação do grupo equivalente do IPCA.
   - **sem grupo definido** — a categoria não tem grupo equivalente e usa o
     Índice geral do IPCA.
3. **IPCA Pessoal do mês** = soma de peso × variação de cada categoria. Os 12
   meses são encadeados, como o IBGE faz.

**Fica de fora:** receitas, transferências, investimentos, Financiamento e
Impostos e Taxas. O pagamento da fatura do cartão só entra (como "Cartão (sem
detalhe)") nos meses em que a fatura não foi importada; com a fatura importada,
entram as compras dela, para o mesmo gasto não ser contado duas vezes.

**Ignorados:** quando o valor de um gasto repetido muda mais de 50% de um mês
para o outro, o par sai da conta — parece troca de produto ou erro de
lançamento, não preço.
"""


def rotulo_origem(cat: CategoriaIpca) -> str:
    if cat.origem == ORIGEM_ITENS:
        return f"{ORIGEM_ITENS} ({cat.meses_proprios} de {MESES_JANELA} meses)"
    return cat.origem


def tabela_categorias(res: ResultadoIpcaPessoal) -> pd.DataFrame:
    return pd.DataFrame([{
        "Categoria": c.categoria,
        "Grupo do IPCA": c.grupo,
        "Peso": pct_br(c.peso, 1),
        "Variação de preço (12m)": pct_br(c.variacao_12m),
        "Origem": rotulo_origem(c),
        "Contribuição": pp_br(c.contribuicao),
    } for c in res.categorias])


def tabela_pesos(res: ResultadoIpcaPessoal) -> pd.DataFrame:
    return pd.DataFrame([{
        "Grupo do IPCA": grupo,
        "Seu peso": pct_br(usuario, 1),
        "Peso no IPCA (IBGE)": "—" if ibge is None else pct_br(ibge, 1),
    } for grupo, usuario, ibge in res.pesos_por_grupo])


def grafico_acumulado(res: ResultadoIpcaPessoal) -> go.Figure:
    meses = [p.mes for p in res.mensal]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=meses, y=[p.acum_pessoal * 100 for p in res.mensal], name="IPCA Pessoal",
        mode="lines+markers", line=dict(color=_COR_PESSOAL, width=3)))
    fig.add_trace(go.Scatter(
        x=meses, y=[p.acum_oficial * 100 for p in res.mensal], name="IPCA oficial",
        mode="lines+markers", line=dict(color=_COR_OFICIAL, width=2, dash="dot")))
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF", separators=",.", hovermode="x unified",
        legend=dict(orientation="h", y=-0.22),
        xaxis=dict(tickformat="%m/%Y", gridcolor="#1E2533"),
        yaxis=dict(ticksuffix="%", gridcolor="#1E2533"),
    )
    return fig


def render_ipca_pessoal(ano: int, mes: int) -> None:
    st.markdown("#### 🧮 IPCA Pessoal")
    st.caption("A inflação da sua cesta de consumo nos últimos 12 meses, ao lado "
               "do IPCA oficial do IBGE.")
    try:
        res = ipca_pessoal_do_mes(ano, mes)
    except Exception as exc:  # noqa: BLE001
        falha_de_acao("Não foi possível calcular o IPCA Pessoal agora.", exc)
        return

    if res is None:
        msg = ("A leitura dos seus lançamentos falhou; o IPCA Pessoal não foi "
               "calculado.")
        aviso_lacuna(msg, codigo="tela.controle.ipca_pessoal.leitura", nivel="warning")
        st.warning(msg)
        return
    if not res.ok:
        aviso_lacuna(res.motivo, codigo="tela.controle.ipca_pessoal.sem_calculo")
        estado_vazio(res.motivo, "🧮")
        for aviso in res.avisos:
            st.info(aviso)
        return

    diferenca = res.acumulado_12m - res.oficial_12m
    k1, k2, k3 = st.columns(3)
    with k1:
        card_metrica("IPCA Pessoal (12 meses)", pct_br(res.acumulado_12m))
    with k2:
        card_metrica("IPCA oficial (12 meses)", pct_br(res.oficial_12m))
    with k3:
        card_metrica("Diferença", pp_br(diferenca), positivo=diferenca <= 0)
    st.caption(f"Janela: {res.inicio:%m/%Y} a {res.fim:%m/%Y}.")
    for aviso in res.avisos:
        st.info(aviso)

    st.plotly_chart(grafico_acumulado(res), width="stretch",
                    config={"displayModeBar": False})

    st.markdown("##### Por categoria")
    st.dataframe(tabela_categorias(res), width="stretch", hide_index=True)

    st.markdown("##### Seus pesos × pesos do IPCA")
    st.caption(f"Peso do IBGE em {res.fim:%m/%Y}.")
    st.dataframe(tabela_pesos(res), width="stretch", hide_index=True)

    with st.expander("Como esta conta é feita"):
        st.markdown(_EXPLICACAO)
        if res.ignorados:
            st.markdown("**Itens ignorados na medida de preço**")
            st.dataframe(pd.DataFrame([{
                "Item": i.item, "Categoria": i.categoria,
                "Mês": f"{i.mes:%m/%Y}", "Motivo": i.motivo,
            } for i in res.ignorados]), width="stretch", hide_index=True)
```

- [ ] **Step 4: Run the view test**

Run: `python -m pytest tests/test_controle_ipca_pessoal_view.py -q`
Expected: todos PASS.

- [ ] **Step 5: Ligar a aba no Controle Financeiro**

Em `views/controle_financeiro.py`, logo abaixo de `from views.controle_previstos import render_previstos` (perto da linha 80):

```python
from views.controle_ipca_pessoal import render_ipca_pessoal
```

Trocar (perto da linha 3886):

```python
    _SECOES = ["📊  Dashboard", "📈  Análises", "🧾  Tabelas",
               "📅  A pagar e receber", "💳  Cartão de Crédito"]
```

por:

```python
    _SECOES = ["📊  Dashboard", "📈  Análises", "🧾  Tabelas",
               "📅  A pagar e receber", "💳  Cartão de Crédito",
               "🧮  IPCA Pessoal"]
```

e, no mesmo bloco, trocar:

```python
    elif secao == _SECOES[4]:
        _tab_cartao(d, sel["ano"], sel["mes"])
    else:
```

por:

```python
    elif secao == _SECOES[4]:
        _tab_cartao(d, sel["ano"], sel["mes"])
    elif secao == _SECOES[5]:
        render_ipca_pessoal(sel["ano"], sel["mes"])
    else:
```

- [ ] **Step 6: Rodar o teste de configurações e ver que ele falha**

Run: `python -m pytest tests/test_configuracoes_sincronizadas.py -q`
Expected: FAIL — a aba "IPCA Pessoal" está em `_SECOES`, mas não na Documentação nem no Mapa do App.

- [ ] **Step 7: Documentação (`views/documentacao.py`)**

Em `FLOW_CONTROLE_FINANCEIRO`, trocar:

```python
    abas=("Dashboard", "Análises", "Tabelas", "A pagar e receber", "Cartão de Crédito"),
```

por:

```python
    abas=("Dashboard", "Análises", "Tabelas", "A pagar e receber", "Cartão de Crédito",
          "IPCA Pessoal"),
```

Trocar:

```python
        ("Visualização", ("abas", "graficos")),
```

por:

```python
        ("Visualização", ("abas", "graficos", "ipca_pessoal")),
```

No nó `"abas"`, trocar:

```python
            "Navegação: Dashboard, Análises, Tabelas, A pagar e receber e Cartão de Crédito, "
```

por:

```python
            "Navegação: Dashboard, Análises, Tabelas, A pagar e receber, Cartão de Crédito "
            "e IPCA Pessoal, "
```

Logo depois do nó `"graficos"` (o que termina em `"Analisar tendências sem misturar fluxo de caixa com a fatura futura do cartão.",\n        ),`), acrescentar:

```python
        "ipca_pessoal": _node(
            "ipca_pessoal", "IPCA Pessoal", "Visualização",
            "A inflação da sua cesta nos últimos 12 meses ao lado do IPCA oficial: o "
            "peso de cada categoria vem do seu gasto; a variação de preço, dos seus "
            "gastos repetidos ou do grupo equivalente do IPCA.",
            (
                "IPCA Pessoal × IPCA oficial (12 meses) e a diferença",
                "Gráfico dos dois acumulados mês a mês",
                "Tabela por categoria: peso, variação, origem, contribuição",
                "Seus pesos × pesos do IBGE por grupo",
                "Série do IBGE (SIDRA 7060) lida de arquivo publicado",
            ),
            "Mostrar quanto a inflação pesa no seu bolso, e não na família média, "
            "dizendo de onde veio cada número.",
        ),
```

- [ ] **Step 8: Mapa do App**

Em `docs/mapa_app/areas/controle_financeiro.json`, linha 4, trocar o trecho:

```
navega por cinco abas (Dashboard, Análises, Tabelas, A pagar e receber e Cartão de Crédito)
```

por:

```
navega por seis abas (Dashboard, Análises, Tabelas, A pagar e receber, Cartão de Crédito e IPCA Pessoal)
```

Achar a linha de `def render_ipca_pessoal`:

Run: `grep -n "def render_ipca_pessoal" views/controle_ipca_pessoal.py`

No fim do arquivo, o último ramo de primeiro nível é "Cartão de Crédito"; o arquivo termina em:

```
          ]
        }
      ]
    }
  ]
}
```

Trocar esse fim por (usar no `fonte` a linha achada acima no lugar de `N`):

```
          ]
        }
      ]
    },
    {
      "nome": "IPCA Pessoal",
      "resumo": "Sua inflação × IPCA oficial em 12 meses",
      "explicacao": "Calcula a inflação da sua cesta de consumo nos últimos 12 meses e põe ao lado do IPCA oficial do IBGE. O peso de cada categoria vem do seu gasto; a variação de preço vem dos seus gastos repetidos (como condomínio e conta de luz) ou, na falta deles, do grupo equivalente do IPCA. Mostra a contribuição de cada categoria, a comparação dos seus pesos com os do IBGE e uma explicação da conta.",
      "so_admin": false,
      "fonte": "views/controle_ipca_pessoal.py:N",
      "filhos": []
    }
  ]
}
```

Regenerar o mapa:

Run: `python scripts/montar_mapa_app.py`
Expected: termina sem erro e atualiza `docs/mapa_app/mapa.json`.

- [ ] **Step 9: Rodar os testes de configuração e da aba**

Run: `python -m pytest tests/test_configuracoes_sincronizadas.py tests/test_controle_ipca_pessoal_view.py -q`
Expected: todos PASS. Se algum teste citar outro arquivo gerado pelo montador, incluí-lo no commit abaixo.

- [ ] **Step 10: Commit**

```bash
git status --short docs/mapa_app
git add views/controle_ipca_pessoal.py views/controle_financeiro.py views/documentacao.py docs/mapa_app/areas/controle_financeiro.json docs/mapa_app/mapa.json tests/test_controle_ipca_pessoal_view.py
git commit -m "IPCA Pessoal: aba no Controle Financeiro, Mapa do App e Documentacao

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Se o `git status` mostrar outro arquivo de `docs/mapa_app` alterado pelo montador, acrescentá-lo ao `git add`.)

---

### Task 5: Grau de Confiança — frescor da série do IBGE

**Files:**
- Modify: `core/confianca_secao.py` (novo `_componente_ipca_grupos` perto de `_componente_previstos_em_dia`; chamada em `confianca_controle_financeiro`, depois de `comps.extend(_componente_previstos_em_dia(engine, notas))`)
- Test: `tests/test_confianca_ipca_grupos.py`

**Interfaces:**
- Consumes: Task 1 (`core.ipca_grupos.ler_ipca_grupos_publicado`, `SerieIpcaGrupos.ultimo_mes()`); `Componente(nome, pct, peso, evidencia)`, `_dias_desde(valor)`, `_frescor_pct(dias, fresco, velho)` de `core/confianca_secao.py`; `_Conn`, `_Engine`, `_comp` de `tests/test_confianca_conciliacao.py`.
- Produces: `_componente_ipca_grupos(notas: list[str]) -> list[Componente]` com nome "Série do IPCA por grupo", peso 0.05.

- [ ] **Step 1: Write the failing test**

Create `tests/test_confianca_ipca_grupos.py`:

```python
"""Grau de Confiança do Controle Financeiro mede a série do IBGE da aba IPCA Pessoal.

Arquivo ausente é defeito (``pct=0.0``), não "não medido": sem ele a aba não
calcula. O IBGE publica o mês M por volta do dia 10 de M+1, então o último mês
tem entre ~40 e ~70 dias numa série em dia.
"""
from __future__ import annotations

from datetime import date, timedelta

from core import confianca_secao as cs
from core import ipca_grupos
from core.ipca_grupos import INDICE_GERAL, SerieIpcaGrupos
from tests.test_confianca_conciliacao import _Conn, _Engine, _comp


def _serie_ate(mes):
    return SerieIpcaGrupos(variacao={(mes, INDICE_GERAL): 0.4})


def _publicar(monkeypatch, serie):
    monkeypatch.setattr(ipca_grupos, "ler_ipca_grupos_publicado", lambda: serie)


def test_serie_em_dia_tem_nota_cheia(monkeypatch):
    _publicar(monkeypatch, _serie_ate((date.today() - timedelta(days=40)).replace(day=1)))
    [c] = cs._componente_ipca_grupos([])
    assert (c.nome, c.pct, c.peso) == ("Série do IPCA por grupo", 100.0, 0.05)


def test_serie_velha_perde_a_nota(monkeypatch):
    _publicar(monkeypatch, _serie_ate((date.today() - timedelta(days=200)).replace(day=1)))
    [c] = cs._componente_ipca_grupos([])
    assert c.pct == 0.0


def test_arquivo_ausente_e_defeito_e_vira_nota(monkeypatch):
    _publicar(monkeypatch, SerieIpcaGrupos())
    notas: list[str] = []
    [c] = cs._componente_ipca_grupos(notas)
    assert c.pct == 0.0
    assert any("IPCA" in n for n in notas)


def test_secao_controle_financeiro_inclui_o_componente(monkeypatch):
    _publicar(monkeypatch, _serie_ate((date.today() - timedelta(days=40)).replace(day=1)))
    secao = cs.confianca_controle_financeiro(_Engine(_Conn(0, 0, None)))
    c = _comp(secao, "série do ipca")
    assert c is not None and c.pct == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_confianca_ipca_grupos.py -q`
Expected: FAIL com `AttributeError: module 'core.confianca_secao' has no attribute '_componente_ipca_grupos'`.

- [ ] **Step 3: Implement**

Em `core/confianca_secao.py`, logo depois da função `_componente_previstos_em_dia`, acrescentar:

```python
def _componente_ipca_grupos(notas: list[str]) -> list[Componente]:
    """A aba IPCA Pessoal afirma a inflação do usuário com a série do IBGE
    publicada em ``data/public/ipca_grupos.json.gz``.

    Arquivo ausente ou ilegível é defeito (``pct=0.0``): a aba não calcula.
    O IBGE publica o mês M por volta do dia 10 de M+1, então o último mês tem
    ~40 a ~70 dias numa série em dia: 75 dias é fresco, 150 é velho.
    """
    from core.ipca_grupos import ler_ipca_grupos_publicado

    ultimo = ler_ipca_grupos_publicado().ultimo_mes()
    if ultimo is None:
        notas.append("serie do IPCA por grupo ausente: a aba IPCA Pessoal nao calcula")
        return [Componente("Série do IPCA por grupo", 0.0, 0.05,
                           "arquivo ipca_grupos.json.gz ausente ou ilegivel")]
    dias = _dias_desde(ultimo)
    return [Componente("Série do IPCA por grupo", _frescor_pct(dias, 75, 150), 0.05,
                       f"ultimo mes publicado {ultimo:%m/%Y}; ha {dias} dias")]
```

Em `confianca_controle_financeiro`, trocar:

```python
    comps.extend(_componente_saldo_conferido(engine, notas))
    comps.extend(_componente_previstos_em_dia(engine, notas))
```

por:

```python
    comps.extend(_componente_saldo_conferido(engine, notas))
    comps.extend(_componente_previstos_em_dia(engine, notas))
    comps.extend(_componente_ipca_grupos(notas))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_confianca_ipca_grupos.py tests/test_confianca_conciliacao.py tests/test_configuracoes_sincronizadas.py -q`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add core/confianca_secao.py tests/test_confianca_ipca_grupos.py
git commit -m "IPCA Pessoal: Grau de Confianca mede o frescor da serie do IBGE

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: IPCA Pessoal no contexto do chat financeiro

**Files:**
- Modify: `core/llm_context_financeiro.py` (assinatura de `build_financas_chat_context`, perto da linha 79; bloco depois de `L.extend(_bloco_previstos(previstos))`, perto da linha 318)
- Modify: `views/controle_financeiro.py` (helper perto de `_previstos_para_chat`, linha ~1261; chamada perto da linha 1385)
- Test: `tests/test_contexto_financeiro_ipca.py`

**Interfaces:**
- Consumes: Task 2 (`linhas_contexto_ipca(res | None) -> list[str]`, `ResultadoIpcaPessoal`, `CategoriaIpca`, `ORIGEM_ITENS`); Task 3 (`ipca_pessoal_do_mes`).
- Produces: parâmetro novo `ipca_pessoal` em `build_financas_chat_context`; valor-sentinela `SEM_IPCA_PESSOAL` (padrão: bloco omitido); `None` = leitura falhou (o bloco diz isso).

- [ ] **Step 1: Write the failing test**

Create `tests/test_contexto_financeiro_ipca.py`:

```python
"""IPCA Pessoal no contexto do chat do Controle Financeiro.

Premissa do projeto: a LLM recebe todo dado disponível. Quando a conta não é
possível, o contexto diz por quê, em vez de calar.
"""
from __future__ import annotations

from datetime import date

from core.ipca_pessoal import ORIGEM_ITENS, CategoriaIpca, ResultadoIpcaPessoal
from core.llm_context_financeiro import build_financas_chat_context


def _contexto(**extra):
    base = {"despesas": 0.0, "categorias": [], "data_source": "real",
            "num_transacoes": 0}
    contexto, _ = build_financas_chat_context(
        user_question="minha inflação está acima do IPCA?",
        dados_mes=base, historico=[], hist_anual={}, gastos_categoria_anual=[],
        gastos_cartao={}, evolucao={}, investido_mes=0.0, ano_ref=2026, mes_ref=10,
        **extra,
    )
    return contexto


def test_resultado_entra_no_contexto():
    res = ResultadoIpcaPessoal(
        ok=True, inicio=date(2025, 11, 1), fim=date(2026, 10, 1),
        acumulado_12m=0.0612, oficial_12m=0.0480,
        categorias=(CategoriaIpca("Moradia", "Habitação", True, 0.5, 0.07,
                                  ORIGEM_ITENS, 12, 0.9, 0.035),),
    )
    contexto = _contexto(ipca_pessoal=res)
    assert "IPCA PESSOAL" in contexto
    assert "6,12%" in contexto and "4,80%" in contexto and "+1,32 p.p." in contexto
    assert "Moradia" in contexto and ORIGEM_ITENS in contexto


def test_falha_de_leitura_e_nomeada():
    assert "leitura dos lançamentos falhou" in _contexto(ipca_pessoal=None)


def test_conta_impossivel_diz_o_motivo():
    res = ResultadoIpcaPessoal(ok=False, motivo="há 12 meses e falta 1")
    assert "Não calculado: há 12 meses e falta 1" in _contexto(ipca_pessoal=res)


def test_sem_o_parametro_o_bloco_nao_aparece():
    assert "IPCA PESSOAL" not in _contexto()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_contexto_financeiro_ipca.py -q`
Expected: FAIL com `TypeError: build_financas_chat_context() got an unexpected keyword argument 'ipca_pessoal'`.

- [ ] **Step 3: Implement in `core/llm_context_financeiro.py`**

Nos imports do topo do módulo, acrescentar:

```python
from core.ipca_pessoal import linhas_contexto_ipca
```

Logo abaixo dos imports, acrescentar:

```python
# Padrão de ``ipca_pessoal``: chamador que não calculou o IPCA Pessoal não ganha
# o bloco. ``None`` é diferente: a leitura falhou, e o bloco diz isso.
SEM_IPCA_PESSOAL = object()
```

Na assinatura de `build_financas_chat_context`, trocar:

```python
    previstos: list | None = None,
```

por:

```python
    previstos: list | None = None,
    ipca_pessoal=SEM_IPCA_PESSOAL,
```

E trocar:

```python
    L.extend(_bloco_previstos(previstos))
```

por:

```python
    L.extend(_bloco_previstos(previstos))
    if ipca_pessoal is not SEM_IPCA_PESSOAL:
        L.extend(linhas_contexto_ipca(ipca_pessoal))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_contexto_financeiro_ipca.py tests/test_contexto_financeiro_receitas.py -q`
Expected: todos PASS.

- [ ] **Step 5: Passar o IPCA Pessoal na chamada do chat**

Em `views/controle_financeiro.py`, nos imports do topo (junto dos outros `from core...`), acrescentar:

```python
from core.ipca_pessoal_dados import ipca_pessoal_do_mes
```

Logo depois da função `_previstos_para_chat`, acrescentar:

```python
def _ipca_pessoal_para_chat(ano: int, mes: int):
    """IPCA Pessoal para o contexto do chat; ``None`` quando a leitura falha,
    para o contexto dizer que não calculou em vez de calar."""
    try:
        return ipca_pessoal_do_mes(ano, mes)
    except Exception:  # noqa: BLE001
        return None
```

Na chamada de `build_financas_chat_context` (perto da linha 1385), trocar:

```python
                    previstos=_previstos_para_chat(),
                )
```

por:

```python
                    previstos=_previstos_para_chat(),
                    ipca_pessoal=_ipca_pessoal_para_chat(ano_ref, mes_ref),
                )
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_contexto_financeiro_ipca.py tests/test_contexto_financeiro_receitas.py tests/test_configuracoes_sincronizadas.py -q`
Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
git add core/llm_context_financeiro.py views/controle_financeiro.py tests/test_contexto_financeiro_ipca.py
git commit -m "IPCA Pessoal: resumo no contexto do chat financeiro

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Verificação final

**Files:** nenhum arquivo novo; só correções do que esta verificação achar.

- [ ] **Step 1: Suíte inteira**

Run: `python -m pytest -q -x`
Expected: tudo PASS. Falha em teste que não é deste plano: conferir com `git stash`-free — rodar o mesmo teste em `main` (`git worktree` ou CI) antes de culpar a mudança; relatar ao usuário, sem "consertar" fora do escopo.

- [ ] **Step 2: Gêmeos da regra (regra do CLAUDE.md)**

Run: `grep -rn "pagamento de cart\|Pagamento de Cart" core/ views/ --include=*.py | grep -v ipca`
Conferir: a regra de "pagamento de fatura só sem CSV no mês" é nova e própria do IPCA Pessoal (o resto do app trata fatura por outra via). Registrar no PR que as outras ocorrências ficam como estão e por quê.

- [ ] **Step 3: Rodar o app em modo de teste e abrir a aba**

Run (PowerShell): `$env:MOCK_MODE="true"; streamlit run app.py`
No navegador: Controle Financeiro → "🧮  IPCA Pessoal". Conferir:
- três cartões com números (IPCA Pessoal, IPCA oficial, Diferença);
- gráfico com duas linhas;
- tabela por categoria: Moradia "seus itens (12 de 12 meses)", Alimentação e Transporte "estimativa oficial";
- tabela de pesos com 9 grupos;
- expansor "Como esta conta é feita" abre;
- se o mês da barra lateral for depois do último mês do IBGE, aparece o aviso da janela.

- [ ] **Step 4: Reler os três informes de Configurações**

Abrir ⚙️ Configurações → 🗺️ Mapa do App, 📚 Documentação e 🎯 Grau de Confiança, e ler o nó do Controle Financeiro em cada uma: a aba nova aparece com o nome certo, "seis abas" está certo, e o componente "Série do IPCA por grupo" aparece no Grau de Confiança.

- [ ] **Step 5: Commit das correções, se houver**

```bash
git status --short
git add <só os arquivos corrigidos nesta tarefa, por caminho>
git commit -m "IPCA Pessoal: ajustes da verificacao final

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
