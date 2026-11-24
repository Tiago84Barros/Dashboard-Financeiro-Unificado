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
    assert [(lanc.categoria, lanc.valor) for lanc in saida] == [
        ("Alimentação", 100.0), (ip.SEM_CATEGORIA, 20.0)]


def test_pagamento_de_fatura_so_entra_sem_o_detalhe_do_cartao():
    setembro, outubro = somar_meses(FIM, -1), FIM
    lancs = [
        _gasto(setembro, 1000, "Pagamento de Cartão", "Pagamento fatura"),
        _gasto(outubro, 1200, "Pagamento de Cartão", "Pagamento fatura"),
        _gasto(outubro, 80, "Alimentação", "IFOOD", conta="credit_card", fonte="csv"),
    ]
    saida = ip.filtrar_consumo(lancs)
    assert [(lanc.data.month, lanc.categoria, lanc.valor) for lanc in saida] == [
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


# ── correções da revisão final ───────────────────────────────────────────────

@pytest.mark.parametrize("nome", ["Cartão de Crédito", "Cartão"])
def test_gemeos_do_pagamento_de_fatura_seguem_a_mesma_regra(nome):
    setembro, outubro = somar_meses(FIM, -1), FIM
    lancs = [
        _gasto(setembro, 1000, nome, "Pagamento fatura"),
        _gasto(outubro, 1200, nome, "Pagamento fatura"),
        _gasto(outubro, 80, "Alimentação", "IFOOD", conta="credit_card", fonte="csv"),
        # compra dentro do CSV do cartão, na mesma categoria: não é pagamento
        _gasto(outubro, 30, nome, "Anuidade", conta="credit_card", fonte="csv"),
    ]
    saida = ip.filtrar_consumo(lancs)
    assert [(lanc.data.month, lanc.categoria, lanc.valor) for lanc in saida] == [
        (9, ip.CARTAO_SEM_DETALHE, 1000.0),
        (10, "Alimentação", 80.0),
        (10, nome, 30.0),
    ]


def test_mes_vazio_no_meio_da_janela_vira_aviso_nomeado():
    meses = _meses()
    vazio = meses[5]
    lancs = [_gasto(m, 100, "Alimentação", _unico("feira", i))
             for i, m in enumerate(meses) if m != vazio]
    res = calcular_ipca_pessoal(lancs, _serie({ALIM: 1.0}), 2026, 10)
    assert res.ok
    aviso = next(a for a in res.avisos if a.startswith("Sem gastos registrados"))
    assert f"{vazio:%m/%Y}" in aviso


def test_historico_completo_nao_tem_aviso_de_mes_vazio():
    lancs, serie = _cesta_duas_categorias()
    res = calcular_ipca_pessoal(lancs, serie, 2026, 10)
    assert res.ok
    assert not any(a.startswith("Sem gastos registrados") for a in res.avisos)
