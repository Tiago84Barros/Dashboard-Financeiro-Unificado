# -*- coding: utf-8 -*-
"""Política de universo de decisão: descartar dado ruim sem mentir sobre o preço.

O que estes testes travam é a diferença entre os dois tipos de descarte. Casca
de cadastro (ticker que nunca negociou) sai de graça; ativo real sem dado sai
com custo, e o custo tem de aparecer.
"""
from core.universo_decisao import (
    MARGEM_CONFORTAVEL,
    MINIMO_ABSOLUTO,
    MODO_DESCARTAR,
    MODO_INSUFICIENTE,
    MODO_RESSALVA,
    Universo,
)


def test_casca_nao_conta_como_perda():
    """1000 linhas de cadastro, 100 com preço, 100 aptas: share é 100%.

    Medir contra o cadastro daria 10% e faria o app parecer pior justamente
    por ter registro mais completo — o incentivo invertido que o módulo evita.
    """
    u = Universo("X", nominal=1000, investivel=100, apto=100)
    assert u.casca == 900
    assert u.sem_dado == 0
    assert u.share_apto == 1.0
    assert u.share_nominal == 0.1
    assert u.modo == MODO_DESCARTAR


def test_ativo_real_sem_dado_entra_na_conta():
    u = Universo("X", nominal=100, investivel=100, apto=70)
    assert u.casca == 0
    assert u.sem_dado == 30
    assert u.share_apto == 0.7


def test_abundancia_absoluta_vence_share_baixo():
    """1.111 nomes em 3.052 é 36% e ainda assim sustenta carteira.

    O gate primário é o piso absoluto; o percentual é o preço pago, não o
    critério. Por isso o modo é ressalva, não insuficiente.
    """
    u = Universo("EUA", nominal=3052, investivel=3052, apto=1111)
    assert u.share_apto < MARGEM_CONFORTAVEL
    assert u.modo == MODO_RESSALVA
    assert u.descarta is True


def test_universo_limpo_mas_pequeno_nao_sustenta_carteira():
    """100% apto não salva um universo de 12 nomes."""
    u = Universo("Y", nominal=12, investivel=12, apto=12)
    assert u.share_apto == 1.0
    assert u.modo == MODO_INSUFICIENTE
    assert u.descarta is False
    assert str(MINIMO_ABSOLUTO) in u.resumo()


def test_modo_descartar_exige_piso_e_margem():
    assert Universo("Z", 500, 500, 400).modo == MODO_DESCARTAR
    assert Universo("Z", 500, 500, 200).modo == MODO_RESSALVA


def test_resumo_declara_o_preco_do_descarte():
    r = Universo("X", nominal=1065, investivel=428, apto=305).resumo()
    assert "305 de 428" in r
    assert "123" in r          # descartados por dado insuficiente
    assert "637" in r          # cascas ignoradas


# --- Abrangencia FII: o gate lia a fonte errada (A-134) --------------------
# A tela de FIIs consome `market.fii_selection_inputs` -- a vitrine, com a
# liquidez ja arbitrada contra a fita oficial da B3 (A-133). O gate contava
# `market.fiis` cru: 306 de 432 (70,8%) contra 349 (80,8%) no dado que a
# decisao le. Motor que ninguem consulta e decoracao.
#
# A fita chegou a parecer a fonte certa e nao e: daria 84,2% contando 36
# investiveis que a selecao nunca ve. Fundo fora da vitrine nao e decidivel.

def test_gate_fii_mede_a_vitrine_que_a_tela_le():
    from core.universo_decisao import _sql_apto_fii
    sql = _sql_apto_fii(com_vitrine=True)
    assert "fii_selection_inputs" in sql
    assert "liquidez_diaria" in sql and "dy_12m" in sql and "pvp" in sql


def test_gate_fii_degrada_sem_referenciar_vitrine_ausente():
    """Vitrine ausente ou vazia derruba a tela se a query a referenciar."""
    from core.universo_decisao import _sql_apto_fii
    sql = _sql_apto_fii(com_vitrine=False)
    assert "fii_selection_inputs" not in sql
    assert "liquidez_diaria IS NOT NULL" in sql


def test_a_nota_diz_de_onde_veio_a_aptidao():
    """Sem isso o mesmo percentual significa coisas diferentes por ambiente."""
    from core.universo_decisao import _nota_gate_fii
    assert "vitrine" in _nota_gate_fii(com_vitrine=True).lower()
    assert "cadastro" in _nota_gate_fii(com_vitrine=False).lower()


def test_exemplos_descartados_seguem_o_mesmo_criterio_do_gate():
    """Listar como descartado quem o gate aprovou seria contradicao na tela."""
    from core.universo_decisao import _sql_ruins_fii
    assert "fii_selection_inputs" in _sql_ruins_fii(com_vitrine=True)
    assert "NOT EXISTS" in _sql_ruins_fii(com_vitrine=True)
    assert "fii_selection_inputs" not in _sql_ruins_fii(com_vitrine=False)
