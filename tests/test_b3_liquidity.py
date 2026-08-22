"""Piso de negociabilidade por classe — casos medidos no universo real."""
from core.b3_liquidity import (
    LiquidityPolicy,
    aplicar_piso_de_liquidez,
    melhor_classe,
)

# Giro diário estimado (R$), medido em 30/07/2026 sobre a carteira real.
GIRO = {
    "BRAP3": 649_000.0, "BRAP4": 46_819_000.0,
    "EUCA4": 650_000.0, "EUCA3": 8_000.0,
    "SHUL4": 2_083_000.0, "SHUL3": 0.0,
    "PETR4": 1_046_866_000.0, "PETR3": 354_817_000.0,
}
IRMAS = {
    "BRAP3": ("BRAP3", "BRAP4"), "BRAP4": ("BRAP3", "BRAP4"),
    "EUCA4": ("EUCA3", "EUCA4"), "EUCA3": ("EUCA3", "EUCA4"),
    "SHUL4": ("SHUL3", "SHUL4"), "SHUL3": ("SHUL3", "SHUL4"),
    "PETR4": ("PETR3", "PETR4"), "PETR3": ("PETR3", "PETR4"),
}


def test_brap3_troca_por_brap4():
    """O caso real: 72x mais giro, mesma empresa."""
    assert melhor_classe("BRAP3", IRMAS["BRAP3"], GIRO) == "BRAP4"


def test_euca4_nao_troca_porque_a_irma_e_pior():
    """EUCA4 gira pouco, mas EUCA3 gira MENOS — trocar pioraria."""
    assert melhor_classe("EUCA4", IRMAS["EUCA4"], GIRO) is None


def test_ticker_ja_liquido_nao_troca():
    """PETR4 está acima do piso: nem se avalia a irmã."""
    assert melhor_classe("PETR4", IRMAS["PETR4"], GIRO) is None


def test_sem_medicao_nao_decide():
    """Ausência de giro é ausência, nunca veredito de iliquidez."""
    assert melhor_classe("XXXX4", ("XXXX3", "XXXX4"), {"XXXX3": 9e9}) is None


def test_vantagem_marginal_nao_justifica_troca():
    """A série é MENSAL: diferença pequena está dentro do erro da estimativa."""
    giro = {"AAAA3": 500_000.0, "AAAA4": 900_000.0}
    assert melhor_classe("AAAA3", ("AAAA3", "AAAA4"), giro) is None


def test_empate_de_giro_desempata_por_ticker():
    """Ordenação total: sem isso a carteira deixaria de ser reproduzível."""
    giro = {"ZZZZ3": 1_000.0, "ZZZZ5": 5_000_000.0, "ZZZZ6": 5_000_000.0}
    escolha = melhor_classe("ZZZZ3", ("ZZZZ3", "ZZZZ6", "ZZZZ5"), giro)
    assert escolha == "ZZZZ5"
    for _ in range(20):
        assert melhor_classe("ZZZZ3", ("ZZZZ5", "ZZZZ3", "ZZZZ6"), giro) == escolha


def test_troca_preserva_peso_e_nunca_remove():
    itens = [{"tk": "BRAP3", "peso": 0.11, "setor": "Materiais Básicos"},
             {"tk": "EUCA4", "peso": 0.09, "setor": "Materiais Básicos"}]
    novos, trocas, avisos = aplicar_piso_de_liquidez(itens, IRMAS, GIRO)

    assert [i["tk"] for i in novos] == ["BRAP4", "EUCA4"]
    assert novos[0]["peso"] == 0.11          # trocar de classe não muda o orçamento
    assert len(novos) == len(itens)          # nunca exclui
    assert trocas[0]["sai"] == "BRAP3" and trocas[0]["entra"] == "BRAP4"
    # EUCA4 fica, mas o usuário é avisado de que sair da posição é difícil.
    assert any("EUCA4" in a for a in avisos)


def test_veto_impede_troca_para_classe_reprovada():
    """A irmã mais líquida não entra se o piso de qualidade a reprovaria."""
    itens = [{"tk": "BRAP3", "peso": 0.11}]
    novos, trocas, avisos = aplicar_piso_de_liquidez(
        itens, IRMAS, GIRO, veto=lambda c: "payout insustentável",
    )
    assert [i["tk"] for i in novos] == ["BRAP3"]
    assert trocas == []
    assert any("payout insustentável" in a for a in avisos)


def test_nao_troca_se_as_duas_classes_ja_estao_na_carteira():
    """Trocar criaria posição duplicada no mesmo papel."""
    itens = [{"tk": "BRAP3", "peso": 0.05}, {"tk": "BRAP4", "peso": 0.05}]
    novos, trocas, _ = aplicar_piso_de_liquidez(itens, IRMAS, GIRO)
    assert sorted(i["tk"] for i in novos) == ["BRAP3", "BRAP4"]
    assert trocas == []


def test_piso_configuravel():
    """Piso baixo o bastante deixa BRAP3 passar sem troca."""
    politica = LiquidityPolicy(piso_diario=100_000.0)
    assert melhor_classe("BRAP3", IRMAS["BRAP3"], GIRO, politica) is None
