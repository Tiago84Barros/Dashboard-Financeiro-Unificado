"""Atualidade trimestral da B3 por cobertura do universo (achado B3-02).

Fixtures com os números medidos em 04/10/2026:

- armazém local: 2026T1 com 397 empresas e 2026T2 com 1 (o ``MAX(data)``
  dizia 2026T2);
- Supabase: 2026T2 com 392 empresas;
- PETR4: margem TTM gravada 0,216898 = janela 2025T2..2026T1, com o 2026T2 já
  no banco (a janela até o 2026T2 dá 0,243861).
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from core import b3_atualidade_trimestral as at
from core.severidade_flags import SEVERIDADE_COBERTURA, severidade_flag

HOJE = date(2026, 10, 4)

# Contagens reais do armazém local (2025T1 a 2026T2).
LOCAL = {(2025, 1): 417, (2025, 2): 413, (2025, 3): 404, (2025, 4): 389,
         (2026, 1): 397, (2026, 2): 1}
# Supabase no mesmo dia: o 2026T2 chegou pela execução anual de 19/09.
SUPABASE = {(2025, 1): 421, (2025, 2): 418, (2025, 3): 410, (2025, 4): 395,
            (2026, 1): 402, (2026, 2): 392}

PETR4 = [(2025, 1, 35331e6, 123144e6), (2025, 2, 26774e6, 119128e6),
         (2025, 3, 32847e6, 127906e6), (2025, 4, 15653e6, 127371e6),
         (2026, 1, 32761e6, 123686e6), (2026, 2, 52495e6, 169530e6)]


def _margem(serie, fim):
    janela = serie[fim - 4:fim]
    return sum(r[2] for r in janela) / sum(r[3] for r in janela)


# ── trimestre vigente ──────────────────────────────────────────────────────

def test_1t_com_397_e_2t_com_1_vigente_e_o_1t_e_sai_aviso():
    av = at.avaliar_universo(LOCAL, HOJE)
    assert at.trimestre_vigente(LOCAL) == (2026, 1)
    assert av["vigente"] == (2026, 1)
    assert av["esperado"] == (2026, 2)
    assert av["atrasada"] is True and av["defasagem"] == 1
    assert av["parcial"]["trimestre"] == (2026, 2)
    assert av["parcial"]["empresas"] == 1
    assert "2026T1" in av["texto"] and "2026T2" in av["texto"]
    assert "1 trimestre(s) atrás" in av["texto"]


def test_max_data_diria_em_dia_a_cobertura_nao():
    """A armadilha do achado: o máximo é 2026T2 só por uma linha."""
    assert max(LOCAL, key=at.serial) == (2026, 2)
    assert at.trimestre_vigente(LOCAL) != (2026, 2)


def test_supabase_com_2t_de_392_esta_em_dia():
    av = at.avaliar_universo(SUPABASE, HOJE)
    assert av["vigente"] == (2026, 2) and av["atrasada"] is False
    assert av["parcial"] is None
    assert "Em dia" in av["texto"]


def test_entrega_tardia_de_algumas_dezenas_nao_derruba_o_trimestre():
    """O pior trimestre completo medido ficou em 92,8% (389 de 419)."""
    contagens = {**SUPABASE, (2026, 2): int(0.81 * 418)}
    assert at.trimestre_vigente(contagens) == (2026, 2)
    contagens[(2026, 2)] = int(0.79 * 418)
    assert at.trimestre_vigente(contagens) == (2026, 1)


def test_sem_dado_nao_afirma_nada():
    av = at.avaliar_universo({}, HOJE)
    assert av["vigente"] is None and av["atrasada"] is None


# ── calendário da CVM ──────────────────────────────────────────────────────

@pytest.mark.parametrize("hoje, esperado", [
    (date(2026, 4, 13), (2025, 3)),   # DFP vence 31/03, folga até 14/04
    (date(2026, 4, 14), (2025, 4)),
    (date(2026, 5, 28), (2025, 4)),   # ITR do 1T vence 15/05, folga até 29/05
    (date(2026, 5, 29), (2026, 1)),
    (date(2026, 8, 27), (2026, 1)),   # ITR do 2T vence 14/08
    (date(2026, 8, 28), (2026, 2)),
    (date(2026, 10, 4), (2026, 2)),
    (date(2026, 11, 28), (2026, 3)),  # ITR do 3T vence 14/11
    (date(2027, 1, 10), (2026, 3)),
])
def test_trimestre_esperado_pelo_calendario(hoje, esperado):
    assert at.trimestre_esperado(hoje) == esperado


# ── de qual trimestre é o score ────────────────────────────────────────────

def test_margem_gravada_identifica_a_janela_do_ttm():
    assert at.base_ttm_da_margem(PETR4, _margem(PETR4, 5)) == (2026, 1)
    assert at.base_ttm_da_margem(PETR4, _margem(PETR4, 6)) == (2026, 2)
    # o valor arredondado que estava gravado no Supabase em 04/10/2026
    assert at.base_ttm_da_margem(PETR4, 0.216898) == (2026, 1)


def test_janela_com_lacuna_ou_nulo_nao_casa():
    com_lacuna = [r for r in PETR4 if r[:2] != (2025, 3)]
    assert at.base_ttm_da_margem(com_lacuna, _margem(PETR4, 5)) is None
    com_nulo = [(*r[:2], None, r[3]) if r[:2] == (2025, 4) else r for r in PETR4]
    assert at.base_ttm_da_margem(com_nulo, _margem(PETR4, 5)) is None
    assert at.base_ttm_da_margem(PETR4, None) is None
    assert at.base_ttm_da_margem(PETR4, 0.99) is None


def test_resumo_conta_scores_defasados():
    series = {"PETR4": PETR4, "WEGE3": PETR4[:5]}
    margens = {"PETR4": _margem(PETR4, 5), "WEGE3": _margem(PETR4, 5),
               "SEMSERIE3": 0.1}
    bases = at.medir_base_do_score(series, margens)
    assert bases["PETR4"] == {"base": (2026, 1), "ultimo": (2026, 2)}
    assert bases["WEGE3"] == {"base": (2026, 1), "ultimo": (2026, 1)}
    resumo = at.resumo_base_do_score(bases)
    assert resumo == {"medidos": 2, "defasados": 1, "sem_casamento": 1,
                      "base_mais_comum": (2026, 1)}
    assert "1 de 2 scores" in at.texto_do_score(resumo)
    assert at.texto_do_score({"medidos": 2, "defasados": 0}) is None


# ── por empresa ─────────────────────────────────────────────────────────────

def test_score_com_trimestre_a_menos_que_o_banco_avisa():
    av = at.avaliar_ticker((2026, 2), (2026, 1), (2026, 2), (2026, 2))
    assert av["usado"] == (2026, 1) and av["atraso"] == 1
    assert len(av["flags"]) == 1 and av["flags"][0].startswith("DADOS:")
    assert "2026T1" in av["aviso"] and "2026T2" in av["aviso"]


def test_empresa_atrasada_com_universo_em_dia_aponta_a_empresa():
    av = at.avaliar_ticker((2026, 1), (2026, 1), (2026, 2), (2026, 2))
    assert av["flags"][0].startswith("COBERTURA:")
    assert "atraso da empresa" in av["flags"][0]
    assert av["aviso"]


def test_base_inteira_atrasada_aponta_a_base():
    av = at.avaliar_ticker((2026, 1), (2026, 1), (2026, 2), (2026, 1))
    assert "a base inteira está atrás" in av["flags"][0]


def test_em_dia_nao_avisa():
    av = at.avaliar_ticker((2026, 2), (2026, 2), (2026, 2), (2026, 2))
    assert av["flags"] == [] and av["aviso"] is None and av["atraso"] == 0


def test_flags_caem_em_limitacao_de_cobertura_nunca_em_risco():
    av = at.avaliar_ticker((2026, 1), (2025, 4), (2026, 2), (2026, 2))
    assert len(av["flags"]) == 2
    assert {severidade_flag(f) for f in av["flags"]} == {SEVERIDADE_COBERTURA}


# ── portão de publicação ───────────────────────────────────────────────────

def test_publicacao_que_trocaria_base_nova_por_velha_e_recusada():
    motivo = at.bloqueio_de_publicacao(LOCAL, SUPABASE)
    assert motivo and "2026T1" in motivo and "2026T2" in motivo


def test_publicacao_com_origem_igual_ou_mais_nova_passa():
    assert at.bloqueio_de_publicacao(SUPABASE, SUPABASE) is None
    assert at.bloqueio_de_publicacao(SUPABASE, LOCAL) is None
    assert at.bloqueio_de_publicacao(LOCAL, {}) is None


# ── leitores SQL e dossiê, contra um banco de verdade (SQLite) ─────────────

@pytest.fixture
def engine_fake():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _anexa(dbapi_conn, _rec):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market")

    with eng.begin() as c:
        c.execute(text("CREATE TABLE market.income_statements (ticker TEXT, "
                       "period TEXT, year INT, quarter INT, net_income REAL, "
                       "revenue REAL)"))
        c.execute(text("CREATE TABLE market.calculated_metrics (ticker TEXT, "
                       "period TEXT, metric_name TEXT, metric_value REAL, "
                       "calculation_method TEXT)"))
        linhas = [{"t": "PETR4", "a": a, "q": q, "ni": ni, "rv": rv}
                  for a, q, ni, rv in PETR4]
        # universo: 397 no 2026T1 e só a PETR4 no 2026T2, como o armazém
        for i in range(396):
            for a, q in [(2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1)]:
                linhas.append({"t": f"X{i:03d}3", "a": a, "q": q, "ni": 1.0, "rv": 10.0})
        c.execute(text("INSERT INTO market.income_statements VALUES "
                       "(:t, 'quarterly', :a, :q, :ni, :rv)"), linhas)
        c.execute(text("INSERT INTO market.calculated_metrics VALUES "
                       "('PETR4', 'ttm', 'Margem_Liquida', :v, 'net_income/revenue')"),
                  {"v": _margem(PETR4, 5)})
    return eng


def test_leitores_sql_contam_o_universo(engine_fake):
    with engine_fake.connect() as conn:
        contagens = at.ler_contagens(conn, at.desde_ano(HOJE))
        series, margens = at.ler_series_e_margens(conn, 2024, ticker="PETR4")
        medida = at.medir_banco(conn, HOJE)
    assert contagens[(2026, 1)] == 397 and contagens[(2026, 2)] == 1
    assert list(series) == ["PETR4"] and len(series["PETR4"]) == 6
    assert set(margens) == {"PETR4"}
    assert medida["universo"]["vigente"] == (2026, 1)
    assert medida["score"]["defasados"] == 1


def test_dossie_leva_atualidade_e_aviso_do_score(engine_fake, monkeypatch):
    from core import dossie_b3

    monkeypatch.setattr("core.database.get_engine", lambda: engine_fake)
    atual = dossie_b3._atualidade("PETR4", hoje=HOJE)
    assert atual["ultimo_trimestre"] == (2026, 2)
    assert atual["base_score"] == (2026, 1)
    assert atual["vigente_universo"] == (2026, 1)
    assert atual["aviso"] and atual["flags"][0].startswith("DADOS:")

    texto = dossie_b3.dossie_to_text({"ticker": "PETR4", "atualidade": atual})
    assert "ATUALIDADE: última demonstração 2026T2 | base do score/TTM 2026T1" in texto
