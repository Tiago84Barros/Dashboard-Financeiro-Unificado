"""
Convergencia por aporte: o dinheiro entra, nada sai.

O app so sabia convergir negociando -- `core.rebalancing` decide SE e dia de
mexer e `global_portfolio.advisor` emite "reduzir"/"vender". Quem esta
formando patrimonio nao opera assim: corrige o peso comprando o que falta.
`core.aporte` responde essa pergunta, e estes testes travam as propriedades
que fazem a resposta ser confiavel.

A mais importante e a identidade do modulo: com deficit medido contra o
patrimonio DEPOIS do aporte, `soma(deficits positivos) >= aporte` sempre. Se
essa desigualdade quebrar, o rateio proporcional passa a distribuir mais do
que existe -- e o plano devolve um numero que o extrato nao vai confirmar.
"""
import pytest

from core.aporte import (
    MOTIVO_ACIMA_DO_TETO,
    MOTIVO_LOTE_NAO_FECHA,
    com_convergencia,
    desvio_l1,
    meses_para_convergir,
    plano_de_aporte,
)

# Carteira desbalanceada de proposito: AAA acima do alvo, BBB e CCC abaixo.
CARTEIRA = {"AAA": 6000.0, "BBB": 3000.0, "CCC": 1000.0}
ALVOS = {"AAA": 0.30, "BBB": 0.30, "CCC": 0.40}


def _por_symbol(plano):
    return {a.symbol: a for a in plano.alocacoes}


# ──────────────────────────────────────────────────────────────────────
# A regra que da nome ao modulo
# ──────────────────────────────────────────────────────────────────────

def test_nunca_vende_mesmo_com_ativo_muito_acima_do_alvo():
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    aaa = _por_symbol(plano)["AAA"]

    assert aaa.deficit < 0, "AAA esta acima do alvo — o deficit precisa ser negativo"
    assert aaa.valor_aportado == 0.0
    assert all(a.valor_aportado >= 0 for a in plano.alocacoes), (
        "nenhuma alocacao pode ser negativa: isso seria uma venda"
    )


def test_ativo_acima_do_alvo_continua_no_plano():
    """Omitir a linha esconderia a informacao 'esta acima, por isso nao recebe'."""
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    assert "AAA" in _por_symbol(plano)


def test_ativo_acima_do_alvo_nao_e_marcado_como_bloqueado():
    """Nao receber por estar acima do alvo e nao receber por teto/lote sao
    coisas diferentes; so a segunda e bloqueio."""
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    assert _por_symbol(plano)["AAA"].motivo_bloqueio == ""
    assert plano.bloqueadas == ()


# ──────────────────────────────────────────────────────────────────────
# A identidade que sustenta o rateio
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("aporte", [1.0, 100.0, 2000.0, 50_000.0])
def test_soma_dos_deficits_positivos_nunca_e_menor_que_o_aporte(aporte):
    plano = plano_de_aporte(CARTEIRA, ALVOS, aporte)
    positivos = sum(a.deficit for a in plano.alocacoes if a.deficit > 0)
    assert positivos >= aporte - 1e-9, (
        "se a soma dos deficits ficar abaixo do aporte, o rateio proporcional "
        "distribui mais do que existe"
    )


@pytest.mark.parametrize("aporte", [1.0, 100.0, 2000.0, 50_000.0])
def test_soma_dos_deficits_de_todos_os_ativos_e_o_proprio_aporte(aporte):
    """Positivos e negativos se cancelam ate sobrar exatamente o aporte."""
    plano = plano_de_aporte(CARTEIRA, ALVOS, aporte)
    assert sum(a.deficit for a in plano.alocacoes) == pytest.approx(aporte)


def test_aporte_e_integralmente_alocado_quando_nao_ha_lote_nem_teto():
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    assert sum(a.valor_aportado for a in plano.alocacoes) == pytest.approx(2000.0)
    assert plano.sobra == pytest.approx(0.0)


def test_deficit_usa_patrimonio_depois_do_aporte():
    """Com P = 10.000 e A = 2.000, o alvo de CCC (40%) e 4.800, nao 4.000."""
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    ccc = _por_symbol(plano)["CCC"]
    assert ccc.deficit == pytest.approx(0.40 * 12_000.0 - 1000.0)


# ──────────────────────────────────────────────────────────────────────
# O aporte tem de melhorar a carteira
# ──────────────────────────────────────────────────────────────────────

def test_aporte_reduz_o_desvio():
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    assert plano.desvio_depois < plano.desvio_antes


def test_aporte_grande_o_bastante_zera_o_desvio_dos_subponderados():
    """Aporte suficiente leva todo mundo ao alvo sem tocar em quem esta acima."""
    plano = plano_de_aporte(CARTEIRA, ALVOS, 500_000.0)
    assert plano.desvio_depois < 0.01


def test_desvio_antes_ignora_o_aporte():
    """Diagnostico da carteira de hoje nao pode depender de quanto vai entrar."""
    a = plano_de_aporte(CARTEIRA, ALVOS, 0.0)
    b = plano_de_aporte(CARTEIRA, ALVOS, 99_999.0)
    assert a.desvio_antes == pytest.approx(b.desvio_antes)


# ──────────────────────────────────────────────────────────────────────
# Uniao de chaves — o defeito ja corrigido em core.rebalancing
# ──────────────────────────────────────────────────────────────────────

def test_ativo_novo_do_alvo_ainda_ausente_da_carteira_recebe_aporte():
    plano = plano_de_aporte({"AAA": 10_000.0}, {"AAA": 0.5, "NOVO": 0.5}, 2000.0)
    novo = _por_symbol(plano)["NOVO"]
    assert novo.valor_atual == 0.0
    assert novo.valor_aportado > 0, "ativo que o alvo quer e a carteira nao tem precisa entrar"


def test_ativo_que_o_alvo_nao_quer_mais_aparece_com_alvo_zero():
    """Ele nao recebe aporte, mas some do plano seria apagar o maior desvio."""
    plano = plano_de_aporte({"AAA": 3000.0, "SAIU": 7000.0}, {"AAA": 1.0}, 1000.0)
    saiu = _por_symbol(plano)["SAIU"]
    assert saiu.peso_alvo == 0.0
    assert saiu.valor_aportado == 0.0
    assert plano.desvio_antes == pytest.approx(0.7)


def test_desvio_l1_enxerga_posicao_fora_do_alvo():
    assert desvio_l1({"A": 0.5, "B": 0.5}, {"A": 1.0}) == pytest.approx(0.5)


# ──────────────────────────────────────────────────────────────────────
# Teto de preco por ativo
# ──────────────────────────────────────────────────────────────────────

def test_ativo_acima_do_teto_de_preco_e_bloqueado_e_seu_deficit_redistribuido():
    plano = plano_de_aporte(
        CARTEIRA, ALVOS, 2000.0,
        precos={"BBB": 50.0, "CCC": 10.0},
        tetos_preco={"BBB": 40.0},   # BBB cotado a 50 > teto 40
    )
    bbb = _por_symbol(plano)["BBB"]
    ccc = _por_symbol(plano)["CCC"]

    assert bbb.motivo_bloqueio == MOTIVO_ACIMA_DO_TETO
    assert bbb.valor_aportado == 0.0
    assert ccc.valor_aportado == pytest.approx(2000.0), (
        "o deficit do bloqueado vai para quem sobrou, nao vira sobra"
    )
    assert plano.sobra == pytest.approx(0.0)


def test_preco_abaixo_do_teto_nao_bloqueia():
    plano = plano_de_aporte(
        CARTEIRA, ALVOS, 2000.0,
        precos={"BBB": 30.0, "CCC": 10.0}, tetos_preco={"BBB": 40.0},
    )
    assert _por_symbol(plano)["BBB"].motivo_bloqueio == ""


def test_ativo_sem_teto_declarado_nunca_e_bloqueado_por_teto():
    """Preco alto sozinho nao bloqueia — so bloqueia contra um teto declarado.

    O preco de BBB (R$ 100) precisa caber na fatia dele do aporte (R$ 272), senao
    o bloqueio que aparece e o de lote e o teste mediria outra coisa.
    """
    plano = plano_de_aporte(
        CARTEIRA, ALVOS, 2000.0,
        precos={"BBB": 100.0, "CCC": 10.0}, tetos_preco={},
    )
    assert _por_symbol(plano)["BBB"].motivo_bloqueio == ""


def test_todos_bloqueados_devolve_o_aporte_como_sobra_declarada():
    """O pior caso precisa ser barulhento, nao silencioso."""
    plano = plano_de_aporte(
        CARTEIRA, ALVOS, 2000.0,
        precos={"BBB": 50.0, "CCC": 90.0},
        tetos_preco={"BBB": 10.0, "CCC": 10.0},
    )
    assert plano.sobra == pytest.approx(2000.0)
    assert {a.symbol for a in plano.bloqueadas} == {"BBB", "CCC"}


# ──────────────────────────────────────────────────────────────────────
# Lote e conversao em cotas
# ──────────────────────────────────────────────────────────────────────

def test_cotas_sao_inteiras_e_a_sobra_do_arredondamento_e_declarada():
    plano = plano_de_aporte(
        {"AAA": 1000.0}, {"AAA": 0.5, "BBB": 0.5}, 1000.0,
        precos={"AAA": 33.0, "BBB": 33.0},
    )
    total = sum(a.valor_aportado for a in plano.alocacoes)
    assert all(a.cotas is None or a.cotas == int(a.cotas) for a in plano.alocacoes)
    assert total + plano.sobra == pytest.approx(1000.0), (
        "aporte = alocado + sobra, sempre; o que nao fecha em lote precisa aparecer"
    )
    assert plano.sobra > 0


def test_lote_padrao_maior_que_um_e_respeitado():
    plano = plano_de_aporte(
        {}, {"AAA": 1.0}, 1000.0, precos={"AAA": 30.0}, lote=100,
    )
    aaa = _por_symbol(plano)["AAA"]
    assert aaa.cotas == 0, "1000/30 = 33 cotas, e 33 nao fecha um lote de 100"
    assert aaa.motivo_bloqueio == MOTIVO_LOTE_NAO_FECHA
    assert plano.sobra == pytest.approx(1000.0)


def test_lote_por_ticker():
    plano = plano_de_aporte(
        {}, {"AAA": 0.5, "BBB": 0.5}, 10_000.0,
        precos={"AAA": 10.0, "BBB": 10.0}, lote={"AAA": 100, "BBB": 1},
    )
    d = _por_symbol(plano)
    assert d["AAA"].cotas % 100 == 0
    assert d["BBB"].cotas == 500


def test_ativo_sem_preco_recebe_em_reais_e_nao_e_bloqueio():
    """Renda fixa, fundo e Tesouro nao tem cota negociada em lote."""
    plano = plano_de_aporte(
        {"RF": 1000.0}, {"RF": 0.5, "AAA": 0.5}, 2000.0, precos={"AAA": 10.0},
    )
    rf = _por_symbol(plano)["RF"]
    assert rf.cotas is None
    assert rf.motivo_bloqueio == ""
    assert rf.valor_aportado > 0


# ──────────────────────────────────────────────────────────────────────
# Bordas
# ──────────────────────────────────────────────────────────────────────

def test_aporte_zero_ainda_diagnostica_o_desvio():
    plano = plano_de_aporte(CARTEIRA, ALVOS, 0.0)
    assert plano.desvio_antes > 0
    assert plano.desvio_depois == pytest.approx(plano.desvio_antes)
    assert all(a.valor_aportado == 0.0 for a in plano.alocacoes)


def test_aporte_negativo_e_tratado_como_zero_nunca_como_resgate():
    plano = plano_de_aporte(CARTEIRA, ALVOS, -5000.0)
    assert plano.aporte == 0.0
    assert all(a.valor_aportado == 0.0 for a in plano.alocacoes)


def test_carteira_vazia_com_aporte_monta_a_posicao_inicial():
    plano = plano_de_aporte({}, ALVOS, 10_000.0)
    d = _por_symbol(plano)
    assert d["CCC"].valor_aportado == pytest.approx(4000.0)
    assert plano.desvio_depois == pytest.approx(0.0)


def test_alvos_em_percentual_sao_normalizados():
    frac = plano_de_aporte(CARTEIRA, {"AAA": 0.3, "BBB": 0.3, "CCC": 0.4}, 2000.0)
    pct = plano_de_aporte(CARTEIRA, {"AAA": 30, "BBB": 30, "CCC": 40}, 2000.0)
    assert [a.valor_aportado for a in frac.alocacoes] == pytest.approx(
        [a.valor_aportado for a in pct.alocacoes]
    )


def test_alvos_vazios_devolvem_plano_sem_alocacao_em_vez_de_estourar():
    plano = plano_de_aporte(CARTEIRA, {}, 2000.0)
    assert plano.sobra == pytest.approx(2000.0)


def test_valores_nao_numericos_nao_derrubam_o_plano():
    plano = plano_de_aporte({"AAA": None, "BBB": "x", "CCC": 1000.0}, ALVOS, 500.0)
    assert plano.patrimonio_antes == pytest.approx(1000.0)


def test_ordem_de_entrada_nao_muda_o_resultado():
    """Determinismo — `memoria: determinismo-carteira-b3`."""
    direta = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    invertida = plano_de_aporte(
        dict(reversed(list(CARTEIRA.items()))),
        dict(reversed(list(ALVOS.items()))),
        2000.0,
    )
    assert [(a.symbol, a.valor_aportado) for a in direta.alocacoes] == [
        (a.symbol, a.valor_aportado) for a in invertida.alocacoes
    ]


# ──────────────────────────────────────────────────────────────────────
# Convergencia
# ──────────────────────────────────────────────────────────────────────

def test_meses_para_convergir_e_finito_com_aporte_relevante():
    meses = meses_para_convergir(CARTEIRA, ALVOS, 2000.0)
    assert meses is not None and meses >= 1


def test_aporte_maior_converge_em_menos_meses():
    poucos = meses_para_convergir(CARTEIRA, ALVOS, 5000.0)
    muitos = meses_para_convergir(CARTEIRA, ALVOS, 500.0)
    assert poucos is not None and muitos is not None
    assert poucos < muitos


def test_carteira_ja_no_alvo_converge_em_zero_meses():
    assert meses_para_convergir({"AAA": 5000.0, "BBB": 5000.0},
                                {"AAA": 0.5, "BBB": 0.5}, 1000.0) == 0


def test_aporte_zero_nao_converge_e_devolve_none():
    assert meses_para_convergir(CARTEIRA, ALVOS, 0.0) is None


def test_horizonte_curto_devolve_none_em_vez_de_numero_inventado():
    assert meses_para_convergir(CARTEIRA, ALVOS, 1.0, horizonte=3) is None


def test_tudo_bloqueado_devolve_none_sem_iterar_ate_o_horizonte():
    meses = meses_para_convergir(
        CARTEIRA, ALVOS, 1000.0,
        precos={"BBB": 50.0, "CCC": 90.0},
        tetos_preco={"BBB": 1.0, "CCC": 1.0},
    )
    assert meses is None


def test_com_convergencia_distingue_nao_converge_de_nao_calculado():
    plano = plano_de_aporte(CARTEIRA, ALVOS, 0.0)
    assert plano.convergencia_avaliada is False
    assert plano.meses_para_convergir is None

    avaliado = com_convergencia(plano, CARTEIRA, ALVOS, 0.0)
    assert avaliado.convergencia_avaliada is True
    assert avaliado.meses_para_convergir is None


def test_com_convergencia_preserva_o_plano_original():
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    avaliado = com_convergencia(plano, CARTEIRA, ALVOS, 2000.0)
    assert avaliado.alocacoes == plano.alocacoes
    assert avaliado.sobra == plano.sobra


def test_recebem_lista_so_quem_recebeu_ordenado_por_valor():
    plano = plano_de_aporte(CARTEIRA, ALVOS, 2000.0)
    recebem = plano.recebem
    assert all(a.valor_aportado > 0 for a in recebem)
    assert [a.valor_aportado for a in recebem] == sorted(
        (a.valor_aportado for a in recebem), reverse=True
    )
