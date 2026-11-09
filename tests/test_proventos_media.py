from datetime import date

from core.proventos import media_por_periodo


def _m(mes: str, ano: int, total: float) -> dict:
    return {"mes": mes, "ano": ano, "label": f"{mes}/{str(ano)[2:]}", "total": total}


def test_media_mensal_simples():
    serie = [_m("Jan", 2026, 100.0), _m("Fev", 2026, 200.0), _m("Mar", 2026, 300.0)]
    r = media_por_periodo(serie, "Mensal", date(2026, 4, 10))
    assert r["media"] == 200.0
    assert r["n_periodos"] == 3
    assert not r["excluiu_corrente"]


def test_mes_sem_pagamento_conta_como_zero():
    # Fev não tem barra; a média é 400/3, não 400/2.
    serie = [_m("Jan", 2026, 100.0), _m("Mar", 2026, 300.0)]
    r = media_por_periodo(serie, "Mensal", date(2026, 4, 10))
    assert r["media"] == round(400 / 3, 2)
    assert r["periodos_zerados"] == 1


def test_mes_corrente_fica_fora_e_a_virada_de_ano_e_continua():
    serie = [_m("Dez", 2025, 100.0), _m("Jan", 2026, 300.0), _m("Fev", 2026, 5.0)]
    r = media_por_periodo(serie, "Mensal", date(2026, 2, 3))
    assert r["media"] == 200.0
    assert r["excluiu_corrente"]


def test_anual_exclui_ano_corrente():
    serie = [{"ano": a, "label": str(a), "total": t}
             for a, t in [(2023, 1000.0), (2024, 3000.0), (2026, 50.0)]]
    r = media_por_periodo(serie, "Anual", date(2026, 11, 4))
    # 2025 sem proventos conta como zero: (1000 + 3000 + 0) / 3
    assert r["media"] == round(4000 / 3, 2)
    assert r["excluiu_corrente"]


def test_so_periodo_corrente_nao_tem_media():
    assert media_por_periodo([_m("Nov", 2026, 10.0)], "Mensal", date(2026, 11, 4)) is None
    assert media_por_periodo([], "Mensal") is None


def test_meses_sem_pagamento_depois_da_ultima_barra_contam_como_zero():
    # Último pagamento em Mar; hoje é Jun: Abr e Mai fechados, sem provento.
    serie = [_m("Jan", 2026, 100.0), _m("Mar", 2026, 400.0)]
    r = media_por_periodo(serie, "Mensal", date(2026, 6, 1))
    assert r["n_periodos"] == 5
    assert r["media"] == 100.0
