"""CF-M1/M3/B4 (auditoria 04/10/2026): reserva pelos meses fechados, marca de mês
parcial, fingerprint da fatura sem categoria e resgate fora da receita."""
from datetime import date

from core.bank_statement_import import classify_bank_movement
from core.controle import _fingerprint_values, _invoice_fingerprint
from core.financeiro import calcular_meses_reserva
from views.dashboard_geral import _render_dashboard_header, mes_parcial_ate

HOJE = date(2026, 10, 4)


def _cf(*pares):
    return [{"month_year": m, "expenses": v} for m, v in pares]


def test_reserva_ignora_mes_corrente_parcial():
    cf = _cf((date(2026, 10, 1), 300.0), (date(2026, 9, 1), 5000.0),
             (date(2026, 8, 1), 3000.0), (date(2026, 7, 1), 4000.0))
    meses, base = calcular_meses_reserva(48000.0, cf, HOJE)
    assert base == "fechados"
    assert meses == 48000.0 / 4000.0  # média de set/ago/jul, não 300


def test_reserva_usa_no_maximo_seis_fechados():
    cf = _cf(*[(date(2026, 10 - i, 1), 1000.0 * i) for i in range(0, 10)][:])
    cf = [r for r in cf if r["month_year"].year == 2026]
    meses, base = calcular_meses_reserva(21000.0, cf, HOJE)
    # fechados: set..abr (despesas 1000..6000 de 9..4 -> pega os 6 mais recentes)
    recentes = sorted([r for r in cf if r["month_year"] < date(2026, 10, 1)],
                      key=lambda r: r["month_year"], reverse=True)[:6]
    media = sum(r["expenses"] for r in recentes) / 6
    assert base == "fechados" and meses == 21000.0 / media


def test_reserva_sem_mes_fechado_cai_no_parcial_e_avisa():
    meses, base = calcular_meses_reserva(900.0, _cf((date(2026, 10, 1), 300.0)), HOJE)
    assert (meses, base) == (3.0, "mes_parcial")


def test_reserva_sem_dado_nao_inventa():
    assert calcular_meses_reserva(900.0, [], HOJE) == (0.0, "sem_dado")


def test_marca_de_mes_parcial():
    assert mes_parcial_ate(True, HOJE) == HOJE
    assert mes_parcial_ate(False, HOJE) is None


def test_cabecalho_mostra_chip_de_mes_parcial(monkeypatch):
    html = []
    import views.dashboard_geral as dg
    monkeypatch.setattr(dg.st, "markdown", lambda t, **k: html.append(t))
    _render_dashboard_header("Out 2026", "Dados reais", "#fff", HOJE, parcial_ate=HOJE)
    assert "Mês parcial" in "".join(html) and "até 04/10" in "".join(html)
    html.clear()
    _render_dashboard_header("Set 2026", "Dados reais", "#fff", HOJE)
    assert "Mês parcial" not in "".join(html)


def test_fingerprint_da_fatura_independe_da_categoria():
    base = {"due_date": date(2026, 10, 10), "purchase_date": date(2026, 9, 20),
            "amount": 99.9, "description": "Mercado  Y", "installment_current": 1,
            "installment_total": 1}
    a = _invoice_fingerprint({**base, "category": "Outros"}, "acc")
    b = _invoice_fingerprint({**base, "category": "Mercado"}, "acc")
    assert a == b
    # E é a mesma chave que o lado do banco monta.
    assert a == _fingerprint_values("acc", base["due_date"], base["purchase_date"],
                                    99.9, "Mercado Y", 1, 1)


def test_fingerprint_continua_distinguindo_parcela():
    base = {"due_date": date(2026, 10, 10), "purchase_date": date(2026, 9, 20),
            "amount": 99.9, "description": "Loja", "category": "x",
            "installment_total": 3}
    assert (_invoice_fingerprint({**base, "installment_current": 1}, "a")
            != _invoice_fingerprint({**base, "installment_current": 2}, "a"))


_CATS = [
    {"id": "1", "nome": "Outros Rendimentos", "tipo": "income"},
    {"id": "2", "nome": "Resgate de Investimento", "tipo": "transfer"},
]


def _mov(desc, direcao="entrada"):
    return {"descricao_original": desc, "descricao_normalizada": desc.lower(),
            "tipo_original_banco": "", "direcao": direcao, "valor": 100.0}


def test_resgate_vai_para_categoria_propria_e_nao_para_receita():
    for desc in ("Resgate de CDB", "Resgate Tesouro Selic"):
        out = classify_bank_movement(_mov(desc), _CATS)
        assert out["categoria_nome"] == "Resgate de Investimento", desc


def test_resgate_sem_categoria_fica_pendente_nunca_em_outros_rendimentos():
    out = classify_bank_movement(_mov("Resgate de CDB"), _CATS[:1])
    assert out["categoria_id"] is None and out["status_classificacao"] == "pendente"


def test_iof_sobre_resgate_nao_vira_resgate():
    out = classify_bank_movement(_mov("IOF sobre resgate", "saida"), _CATS)
    assert out["categoria_nome"] != "Resgate de Investimento"


def test_script_de_resgates_so_move_resgate_puro():
    from scripts.reclassifica_resgates_importados import filtrar_alvos
    linhas = [{"descricao": "RESGATE DE CDB"}, {"descricao": "Rendimento resgate CDB"},
              {"descricao": "Pix recebido"}]
    assert [x["descricao"] for x in filtrar_alvos(linhas)] == ["RESGATE DE CDB"]
