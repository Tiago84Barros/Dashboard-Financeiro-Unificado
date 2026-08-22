"""Piso de negociabilidade do módulo EUA."""
from datetime import datetime, timezone

import pytest

from core.us_liquidity import (
    PISO_PADRAO_USD,
    EstadoLiquidez,
    LiquidityPolicy,
    aplicar_piso,
    classificar,
    formata_usd,
)

# Giro diário medido em 03/08/2026 (mediana de 180 dias, US$).
GIRO = {"AAPL": 9_800_000_000.0, "MSFT": 7_100_000_000.0,
        "MEDIO": 4_200_000.0, "NOLIMIAR": 999_000.0, "MICRO": 12_000.0}
AGORA = datetime(2026, 8, 17, tzinfo=timezone.utc)
MEDIDO_AGORA = {symbol: AGORA for symbol in GIRO}


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
    triagem = aplicar_piso(["AAPL", "MEDIO", "NOLIMIAR", "MICRO"], GIRO,
                           timestamps=MEDIDO_AGORA, now=AGORA)
    assert triagem.aprovados == ["AAPL", "MEDIO"]
    assert {r["symbol"] for r in triagem.removidos} == {"NOLIMIAR", "MICRO"}
    assert triagem.nao_verificados == []


def test_sem_medicao_e_um_terceiro_estado_e_nao_um_aprovado():
    """INVERTE `test_sem_medicao_nao_e_removido` (achado A-004, ALTA).

    O teste antigo exigia que o símbolo sem série de volume estivesse em
    `aprovados`. Isso cristalizava o defeito: quem lia a primeira posição da
    tupla — o motor de carteira lia — montava posição em papel cuja
    negociabilidade nunca foi medida.

    O argumento que sustentava o comportamento antigo continua válido no que ele
    de fato prova: ausência de medição não é prova de iliquidez, e cortar os
    1.007 ativos sem série (de 3.759) perderia empresa boa por lacuna de coleta.
    Por isso eles não vão para `removidos` nem somem — ganham conjunto próprio.
    O que não vale é chamá-los de aprovados.
    """
    triagem = aplicar_piso(["AAPL", "DESCONHECIDA"], GIRO,
                           timestamps=MEDIDO_AGORA, now=AGORA)
    assert triagem.aprovados == ["AAPL"]
    assert triagem.nao_verificados == ["DESCONHECIDA"]
    assert not any(r["symbol"] == "DESCONHECIDA" for r in triagem.removidos)
    assert triagem.elegiveis == ["AAPL"]
    assert any("não verificada" in a for a in triagem.avisos)


def test_piso_configuravel():
    triagem = aplicar_piso(
        ["MEDIO"], GIRO, timestamps=MEDIDO_AGORA, now=AGORA,
        policy=LiquidityPolicy(piso_diario_usd=5_000_000.0))
    assert triagem.aprovados == []
    assert {r["symbol"] for r in triagem.removidos} == {"MEDIO"}


# ── Tri-estado, fail-closed e valores-lixo ───────────────────────────────────

@pytest.mark.parametrize("valor", [
    None, float("nan"), "", "   ", "n/d", "abc",
    float("inf"), float("-inf"), object(),
])
def test_valor_sem_medicao_valida_nunca_e_aprovado(valor):
    """Infinito é o caso perigoso: `float('inf') >= piso` é True.

    A versão 1.0.0 devolvia `inf` de `_num` e ele passava pelo piso como se
    fosse o ativo mais líquido do mercado. Infinito não é medição — é divisão
    por zero ou overflow a montante.
    """
    assert classificar(valor, 1_000_000.0) is EstadoLiquidez.NAO_VERIFICADA
    assert classificar(valor, 0.0) is EstadoLiquidez.NAO_VERIFICADA


@pytest.mark.parametrize("valor,esperado", [
    (0, EstadoLiquidez.MEDIDA_REPROVADA),        # mediu e não negociou
    (0.0, EstadoLiquidez.MEDIDA_REPROVADA),
    (-5.0, EstadoLiquidez.MEDIDA_REPROVADA),
    (999_999.99, EstadoLiquidez.MEDIDA_REPROVADA),
    (1_000_000.0, EstadoLiquidez.MEDIDA_APROVADA),   # o piso é inclusivo
    ("2500000", EstadoLiquidez.MEDIDA_APROVADA),     # texto numérico é medição
    (9.8e9, EstadoLiquidez.MEDIDA_APROVADA),
])
def test_classificacao_com_piso_de_um_milhao(valor, esperado):
    assert classificar(valor, 1_000_000.0, AGORA, now=AGORA) is esperado


def test_piso_zero_nao_transforma_lixo_em_medicao():
    """Piso zerado libera o universo, não a qualidade do dado.

    Zero passa (`0 >= 0`), porque zero foi MEDIDO; ausência continua ausência.
    """
    assert classificar(0.0, 0.0, AGORA, now=AGORA) is EstadoLiquidez.MEDIDA_APROVADA
    assert classificar(None, 0.0) is EstadoLiquidez.NAO_VERIFICADA
    assert classificar(float("inf"), 0.0) is EstadoLiquidez.NAO_VERIFICADA


def test_lote_com_os_tres_estados_misturados():
    """Um único lote com aprovado, reprovado e não verificado.

    Os três conjuntos são disjuntos e reconciliam com a entrada — sem isso um
    ativo poderia sumir da contagem sem ninguém notar.
    """
    giro = {"AAPL": 9.8e9, "MICRO": 12_000.0, "LIXO": float("inf"),
            "VAZIO": "", "ZERO": 0.0}
    simbolos = ["AAPL", "MICRO", "LIXO", "VAZIO", "ZERO", "AUSENTE"]
    t = aplicar_piso(simbolos, giro,
                     timestamps={s: AGORA for s in giro}, now=AGORA)

    assert t.aprovados == ["AAPL"]
    assert {r["symbol"] for r in t.removidos} == {"MICRO", "ZERO"}
    assert set(t.nao_verificados) == {"LIXO", "VAZIO", "AUSENTE"}
    assert len(t.aprovados) + len(t.removidos) + len(t.nao_verificados) == len(simbolos)
    assert t.elegiveis == ["AAPL"]
    assert t.estados["LIXO"] is EstadoLiquidez.NAO_VERIFICADA


def test_modo_exploratorio_so_com_piso_explicitamente_zerado():
    """Piso zero é a ÚNICA porta para o ativo não verificado aparecer."""
    simbolos = ["AAPL", "MICRO", "AUSENTE"]
    livre = aplicar_piso(simbolos, GIRO, timestamps=MEDIDO_AGORA, now=AGORA,
                         policy=LiquidityPolicy(piso_diario_usd=0.0))
    assert livre.exploratorio is True
    assert set(livre.elegiveis) == {"AAPL", "MICRO", "AUSENTE"}
    assert livre.nao_verificados == ["AUSENTE"]      # continua declarado
    assert any("exploratório" in a for a in livre.avisos)

    fechado = aplicar_piso(simbolos, GIRO, timestamps=MEDIDO_AGORA, now=AGORA)
    assert fechado.exploratorio is False
    assert "AUSENTE" not in fechado.elegiveis


@pytest.mark.parametrize("piso_invalido", [float("nan"), -1.0,
                                            float("inf"), float("-inf")])
def test_piso_invalido_nunca_abre_o_modo_exploratorio(piso_invalido):
    """Somente zero finito é a escolha explícita de explorar sem o gate."""
    triagem = aplicar_piso(
        ["AAPL", "AUSENTE"], GIRO, timestamps=MEDIDO_AGORA, now=AGORA,
        policy=LiquidityPolicy(piso_diario_usd=piso_invalido),
    )

    assert triagem.exploratorio is False
    assert triagem.elegiveis == []
    assert triagem.nao_verificados == ["AAPL", "AUSENTE"]
    assert triagem.avisos == [
        "Piso de negociabilidade inválido: informe um valor finito maior ou igual a zero."
    ]


def test_giro_vazio_com_piso_ligado_nao_aprova_ninguem():
    """Universo inteiro sem medição = nenhum investível, não 'todos passam'."""
    t = aplicar_piso(["AAPL", "MSFT"], {})
    assert t.aprovados == []
    assert t.elegiveis == []
    assert set(t.nao_verificados) == {"AAPL", "MSFT"}


def test_versao_da_metodologia_acompanha_a_mudanca_de_elegibilidade():
    """Carteira salva precisa dizer sob qual regra de liquidez nasceu."""
    from core.us_liquidity import VERSION

    assert VERSION.startswith("us-liquidity-2."), VERSION
    assert aplicar_piso(["AAPL"], GIRO, timestamps=MEDIDO_AGORA, now=AGORA).versao == VERSION


@pytest.mark.parametrize("timestamp", [None, "", "data inválida", "2026-08-09T23:59:59Z"])
def test_giro_alto_sem_timestamp_fresco_nao_e_investivel(timestamp):
    """A mediana de 180 pregões não vale sem saber até quando a série chegou."""
    assert classificar(9.8e9, 1_000_000.0, timestamp, now=AGORA) is EstadoLiquidez.NAO_VERIFICADA


def test_timestamp_no_limite_de_tolerancia_ainda_e_atual():
    from datetime import timedelta

    from core.us_liquidity import LIQUIDITY_MAX_AGE_DAYS
    limite = AGORA - timedelta(days=LIQUIDITY_MAX_AGE_DAYS)
    assert classificar(9.8e9, 1_000_000.0, limite, now=AGORA) is EstadoLiquidez.MEDIDA_APROVADA


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


def test_ordem_de_grandeza_usa_virgula_decimal():
    """A interface é em português e o valor é em dólar.

    "US$ 1,000,000" mistura as convenções: um leitor brasileiro pode ler "1
    vírgula zero". E o decimal precisa sair com VÍRGULA — a primeira versão
    desta função escrevia "2.5 bilhões", trocando o separador errado.
    """
    from core.us_liquidity import formata_usd_curto as f

    assert f(1e6) == "1 milhão"
    assert f(5e6) == "5 milhões"
    assert f(20e6) == "20 milhões"
    assert f(1.5e6) == "1,5 milhões"
    assert f(2.5e9) == "2,5 bilhões"
    assert f(1e9) == "1 bilhão"
    assert f(750e3) == "750 mil"
    # Nenhuma saída pode conter ponto: em pt-BR ele é separador de MILHAR e
    # inverteria a leitura de qualquer valor decimal.
    for v in (1e6, 1.5e6, 2.5e9, 750e3, 20e6):
        assert "." not in f(v), f(v)
