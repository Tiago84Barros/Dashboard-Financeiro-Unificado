import core.data_healing as h


def test_requires_two_sources_no_single_source_write():
    # Só banco válido → não grava (precisa ≥2 fontes)
    r = h.resolve_field("ROE", bd=0.18, fundamentus=None, status_invest=None)
    assert r.novo is None
    assert r.acao == "sem_corroboracao"


def test_bank_corroborated_keeps_bank():
    r = h.resolve_field("ROE", bd=0.18, fundamentus=0.182, status_invest=None)
    assert r.novo is None
    assert r.acao == "mantido"


def test_bank_outlier_overwritten_when_two_web_agree():
    # UGPA3: banco 190% (inválido) + Fundamentus/Status ~2,1% concordam → corrige
    r = h.resolve_field("Margem_Liquida", bd=1.904, fundamentus=0.021, status_invest=0.022)
    assert r.acao in ("corrigido", "preenchido")  # 190% é inválido → tratado como ausente
    assert r.novo is not None
    assert abs(r.novo - 0.0215) < 1e-6
    assert r.fonte == "Fundamentus+StatusInvest"


def test_dy_zero_treated_as_missing_and_filled():
    # PETR3: banco DY=0 (faltante) + 2 fontes web 6,9% concordam → preenche
    r = h.resolve_field("DY", bd=0.0, fundamentus=0.069, status_invest=0.068)
    assert r.acao == "preenchido"
    assert r.novo is not None and abs(r.novo - 0.0685) < 1e-6


def test_divergence_unresolved_when_only_one_web_disagrees():
    # Banco válido mas diverge, e só 1 web disponível → não sobrescreve (não confia em 1 fonte)
    r = h.resolve_field("P/L", bd=6.0, fundamentus=12.0, status_invest=None)
    assert r.novo is None
    assert r.acao == "divergencia_nao_resolvida"


def test_sources_relatively_close_agree_not_identical():
    # ROE: banco ausente; Fundamentus 20% e Status Invest 21,5% NÃO são iguais,
    # mas relativamente próximos (≤15%) → preenche com a média.
    r = h.resolve_field("ROE", bd=None, fundamentus=0.20, status_invest=0.215)
    assert r.acao == "preenchido"
    assert abs(r.novo - 0.2075) < 1e-6


def test_sources_far_apart_do_not_agree():
    # DY 3% vs 7%: relativamente distantes → não corrobora → revisão (não grava).
    r = h.resolve_field("DY", bd=None, fundamentus=0.03, status_invest=0.07)
    assert r.novo is None
    assert r.acao == "divergencia_nao_resolvida"


def test_bank_kept_when_close_to_web():
    # Banco 18% e web 19% (próximos) → mantém banco, sem corrigir à toa.
    r = h.resolve_field("ROE", bd=0.18, fundamentus=0.19, status_invest=None)
    assert r.acao == "mantido"


def test_brapi_as_third_source_corroborates_fill():
    # Banco DY=0 (inválido); Fundamentus ausente; Status Invest e brapi concordam → preenche
    r = h.resolve_field("DY", bd=0.0, fundamentus=None, status_invest=0.069, brapi=0.070)
    assert r.acao == "preenchido"
    assert abs(r.novo - 0.0695) < 1e-6
    assert "StatusInvest" in r.fonte and "brapi" in r.fonte
    assert r.n_fontes == 2


def test_brapi_breaks_tie_when_two_web_agree_against_bank():
    # Banco P/L divergente; Fundamentus e brapi concordam (Status ausente) → corrige
    r = h.resolve_field("P/L", bd=6.0, fundamentus=12.0, status_invest=None, brapi=12.5)
    assert r.acao == "corrigido"
    assert "brapi" in r.fonte


def test_brapi_alone_is_single_source_no_write():
    # Só brapi válido (banco e demais ausentes) → 1 fonte → não grava
    r = h.resolve_field("ROE", bd=None, fundamentus=None, status_invest=None, brapi=0.20)
    assert r.novo is None
    assert r.acao == "sem_dado"


def test_resolve_ticker_and_proposals_only():
    sources = {
        "banco": {"Margem_Liquida": 1.904, "ROE": 0.18, "DY": 0.0},
        "fundamentus": {"Margem_Liquida": 0.021, "ROE": 0.182, "DY": 0.069},
        "status_invest": {"Margem_Liquida": 0.022, "ROE": None, "DY": 0.068},
    }
    res = h.resolve_ticker(sources, fields=("Margem_Liquida", "ROE", "DY"))
    props = h.proposals_only(res)
    fields_changed = {r.field for r in props}
    assert "Margem_Liquida" in fields_changed   # corrigido
    assert "DY" in fields_changed               # preenchido
    # ROE: banco 0.18 corroborado por fundamentus 0.182 → mantido (não propõe)
    assert "ROE" not in fields_changed
