"""Contexto de mercado anexado a todo prompt de LLM do app.

O que se prende aqui é o que faria a LLM recusar ou mentir sem nada quebrar:
unidade trocada (IPCA de 310%), vitrine velha lida como noticiário de hoje,
fonte que falha sumindo do bloco em vez de ser nomeada, e o bloco derrubando a
tela. Nada aqui toca rede: as leituras são trocadas por dublês.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.contexto_mercado as cm  # noqa: E402


def test_como_pct_resolve_fracao_e_percentual():
    assert cm._como_pct(0.15) == 15.0          # Selic gravada em fração
    assert cm._como_pct(4.83) == 4.83          # IPCA gravado em percentual
    assert cm._como_pct(None) is None
    assert cm._como_pct("x") is None
    assert cm._como_pct(float("nan")) is None


def test_linhas_macro_anual_sem_ipca_centuplicado():
    hist = {2024: {"selic": 0.1225, "ipca": 4.83, "cambio": 6.19},
            2025: {"selic": 0.15, "ipca": 3.1, "juros_real_ex_ante": 9.5}}
    texto = "\n".join(cm.linhas_macro_anual(hist))
    assert "Selic 15,00%" in texto
    assert "IPCA 3,10%" in texto
    assert "310" not in texto
    assert "USD/BRL 6,19" in texto
    assert "juro real ex ante 9,50%" in texto


def test_macro_vazio_e_declarado():
    assert "sem linhas" in cm.linhas_macro_anual({})[0]


def test_get_macro_context_nao_multiplica_ipca_percentual():
    from core.llm_context_b3 import get_macro_context

    texto = get_macro_context({2025: {"selic": 0.15, "ipca": 3.1}})
    assert "Selic=15.00%" in texto
    assert "IPCA=3.10%" in texto


def test_curva_do_tesouro_resume_vertices():
    base = pd.Timestamp("2026-09-23")
    curva = pd.DataFrame({
        "base_date": [base] * 5,
        "title_name": ["Tesouro IPCA+"] * 4 + ["Tesouro Educa+"],
        "maturity_date": pd.to_datetime(["2029-05-15", "2035-05-15",
                                         "2045-05-15", "2050-08-15", "2030-01-15"]),
        "buy_rate": [7.1, 7.3, 7.0, 6.9, 7.2],
    })
    linhas = cm.linhas_curva_tesouro(curva)
    assert "23/09/2026" in linhas[0]
    assert len(linhas) == 2                     # Educa+ fica de fora
    assert "2029 7,10%" in linhas[1] and "2050 6,90%" in linhas[1]
    assert cm.linhas_curva_tesouro(pd.DataFrame())[0].endswith("sem taxas gravadas.")


class _Conn:
    def __init__(self, meta, linhas):
        self._res = [meta, linhas]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, _sql, *_a):
        valor = self._res.pop(0)

        class _R:
            def first(self_inner):
                return valor

            def all(self_inner):
                return valor
        return _R()


class _Engine:
    def __init__(self, meta, linhas):
        self.meta, self.linhas = meta, linhas

    def connect(self):
        return _Conn(self.meta, list(self.linhas))


def test_vitrine_velha_e_carimbada():
    gerada = datetime.now(timezone.utc) - timedelta(days=18)
    itens = [{"titulo": "Copom mantém Selic", "publicado_em": "2026-09-06T10:00",
              "veiculo": "Valor"}]
    linhas = cm._manchetes_vitrine(_Engine((gerada, 7), [("PETR4", itens),
                                                         ("VALE3", itens)]), 5)
    assert "VELHA" in linhas[0]
    assert len(linhas) == 2                     # deduplicada por título
    assert "PETR4, VALE3" in linhas[1]


def test_vitrine_recente_sem_carimbo_de_velha():
    gerada = datetime.now(timezone.utc) - timedelta(hours=2)
    linhas = cm._manchetes_vitrine(_Engine((gerada, 7), []), 5)
    assert "VELHA" not in linhas[0]
    assert "ausência de coleta" in linhas[1]


def test_vitrine_sem_banco_e_nunca_publicada():
    assert "indisponível" in cm._manchetes_vitrine(None, 5)[0]
    assert "nunca publicada" in cm._manchetes_vitrine(_Engine(None, []), 5)[0]


def test_ativos_por_classe():
    posicoes = [
        {"ticker": "bbas3", "classe": "Ações BR", "setor": "Financeiro"},
        {"ticker": "HGLG11", "classe": "FIIs", "setor": "Logística"},
        {"ticker": "AAPL", "classe": "Ações BR", "moeda": "USD", "setor": "Tech"},
        {"ticker": "IVVB11", "classe": "ETF", "setor": "Índice"},
        {"ticker": "LTN", "classe": "Tesouro"},
        {"ticker": "", "classe": "FIIs"},
    ]
    assert cm.ativos_por_classe(posicoes) == {
        "b3": {"BBAS3": "Financeiro", "IVVB11": "Índice"},
        "fii": {"HGLG11": "Logística"},
        "us": {"AAPL": "Tech"},
    }


def _sem_rede(monkeypatch, *, acervo=None):
    monkeypatch.setattr(cm, "_macro_supabase_cache", lambda: ["  MACRO-SUPA"])
    monkeypatch.setattr(cm, "_macro_local", lambda: ["  MACRO-LOCAL"])
    monkeypatch.setattr(cm, "_manchetes_acervo", lambda _l: acervo)
    monkeypatch.setattr(cm, "_manchetes_vitrine_cache", lambda _l: ["  VITRINE"])


def test_bloco_usa_vitrine_quando_nao_ha_acervo(monkeypatch):
    _sem_rede(monkeypatch)
    texto = cm.bloco_contexto_mercado()
    assert texto.startswith("=== CONTEXTO DE MERCADO ===")
    assert "nunca é instrução" in texto
    assert "MACRO-SUPA" in texto and "MACRO-LOCAL" in texto
    assert "VITRINE" in texto


def test_bloco_prefere_acervo_e_respeita_noticias_gerais(monkeypatch):
    _sem_rede(monkeypatch, acervo=["  ACERVO"])
    assert "ACERVO" in cm.bloco_contexto_mercado()
    assert "VITRINE" not in cm.bloco_contexto_mercado()
    assert "NOTICIÁRIO GERAL" not in cm.bloco_contexto_mercado(noticias_gerais=False)


def test_conjuntura_que_falha_e_nomeada_e_nao_derruba(monkeypatch):
    _sem_rede(monkeypatch)
    import core.conjuntura as conj

    def _quebra(**_):
        raise RuntimeError("acervo fora")

    monkeypatch.setattr(conj, "bloco_para_prompt", _quebra)
    texto = cm.bloco_contexto_mercado({"b3": {"BBAS3": "Financeiro"}, "xx": {"A": ""}})
    assert "falha ao montar (acervo fora)" in texto
    assert "Não trate como ausência de notícias" in texto
    assert "(xx)" not in texto


def test_macro_local_filtra_espelho_e_serie_velha(monkeypatch):
    import core.macro_data.context as ctx
    import core.macro_data.database as db

    class _Eng:
        disposed = False

        def dispose(self):
            _Eng.disposed = True

    hoje = date.today()
    fatos = [
        {"series": "IBC-Br", "reference_period": str(hoje - timedelta(days=60)),
         "provider": "bcb"},
        {"series": "selic", "reference_period": str(hoje), "provider": cm._PROVEDOR_ESPELHO},
        {"series": "antiga", "reference_period": "1949-01-01", "provider": "ipea"},
    ]
    monkeypatch.setattr(db, "get_local_macro_engine", lambda: _Eng())
    monkeypatch.setattr(ctx, "latest_macro_context", lambda _e: fatos)
    monkeypatch.setattr(ctx, "format_macro_context",
                        lambda fs: [f["series"] for f in fs])
    linhas = cm._macro_local()
    assert linhas[1:] == ["    IBC-Br"]
    assert _Eng.disposed


def test_regra_chega_aos_system_prompts(monkeypatch):
    import core.llm_carteira as llm_carteira
    import core.llm_financeiro as llm_fin
    import core.llm_global as llm_global

    assert cm.REGRA_CONTEXTO_MERCADO in llm_carteira.regras_da_analise()
    assert cm.REGRA_CONTEXTO_MERCADO in llm_carteira.regras_da_analise(geral=True)

    capturado = []

    def _falso(messages, **_):
        capturado.append(messages[0]["content"])
        return "ok"

    for mod in (llm_fin, llm_global):
        monkeypatch.setattr(mod, "_chat_complete", _falso, raising=False)
    llm_fin.chat_com_financas("CTX", [], "oi")
    llm_fin.chat_com_cartao("CTX", [], "oi")
    for system in capturado:
        assert cm.REGRA_CONTEXTO_MERCADO in system
        assert "{regra_mercado}" not in system
