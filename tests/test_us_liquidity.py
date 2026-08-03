"""Piso de negociabilidade do módulo EUA."""
from core.us_liquidity import (
    PISO_PADRAO_USD, LiquidityPolicy, aplicar_piso, formata_usd,
)

# Giro diário medido em 03/08/2026 (mediana de 180 dias, US$).
GIRO = {"AAPL": 9_800_000_000.0, "MSFT": 7_100_000_000.0,
        "MEDIO": 4_200_000.0, "NOLIMIAR": 999_000.0, "MICRO": 12_000.0}


def test_piso_padrao_e_um_milhao_de_dolares():
    """Escolhido com o usuário; a mediana americana é US$ 7,05 mi/dia."""
    assert PISO_PADRAO_USD == 1_000_000.0


def test_piso_filtra_de_fato_a_cauda_ilíquida_americana():
    """Comparar o número com o do B3 seria inválido — lá é BRL, aqui é USD.

    O que importa é se o piso morde a distribuição REAL: medido em 03/08/2026,
    o p25 americano é US$ 0,42 mi/dia e a mediana US$ 7,05 mi. Um piso abaixo do
    p25 não filtraria nada; acima da mediana cortaria metade do mercado.
    """
    P25_USD, MEDIANA_USD = 420_000.0, 7_050_000.0
    assert P25_USD < PISO_PADRAO_USD < MEDIANA_USD


def test_remove_abaixo_do_piso_e_mantem_acima():
    aprovados, removidos, _ = aplicar_piso(
        ["AAPL", "MEDIO", "NOLIMIAR", "MICRO"], GIRO)
    assert aprovados == ["AAPL", "MEDIO"]
    assert {r["symbol"] for r in removidos} == {"NOLIMIAR", "MICRO"}


def test_sem_medicao_nao_e_removido():
    """3.759 ativos contra 2.752 com série de volume: remover os 1.007 sem dado
    cortaria empresa boa por lacuna de coleta."""
    aprovados, removidos, avisos = aplicar_piso(["AAPL", "DESCONHECIDA"], GIRO)
    assert "DESCONHECIDA" in aprovados
    assert not any(r["symbol"] == "DESCONHECIDA" for r in removidos)
    assert any("não foram verificadas" in a for a in avisos)


def test_piso_configuravel():
    aprovados, _, _ = aplicar_piso(
        ["MEDIO"], GIRO, policy=LiquidityPolicy(piso_diario_usd=5_000_000.0))
    assert aprovados == []


def test_nao_existe_troca_de_classe_neste_modulo():
    """Trava de projeto: o vínculo company_id americano agrupa warrant, ETF e
    baby bond junto da ação (JPM com VYLD, ACON com ACONW a US$ 0,02).

    Trocar ação por warrant causaria dano maior que a iliquidez que a troca
    pretendia corrigir. Se alguém trouxer a lógica do B3 para cá, este teste cai.
    """
    import core.us_liquidity as mod
    proibidos = [n for n in dir(mod)
                 if any(t in n.lower() for t in ("irma", "classe", "sibling", "swap"))]
    assert not proibidos, f"lógica de troca de classe vazou: {proibidos}"


def test_formata_usd_usa_padrao_americano():
    assert formata_usd(1_000_000.0) == "1,000,000"
