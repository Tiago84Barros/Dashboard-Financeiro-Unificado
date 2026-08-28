# -*- coding: utf-8 -*-
"""A-162: os três motores respondem à MESMA lista de perguntas.

Antes, cada motor declarava só os portões que lhe convinham, e
`fracao_aprovada` dividia pelo que cada um havia declarado. O motor de FIIs
marcava 100% em "metodologia validada" com um portão; a B3, 50% com dois; os
EUA, 0% com dois. Medir menos rendia nota maior — a omissão pontuava como
virtude.
"""
from core.validacao_motor import (
    DIM_PIT,
    DIM_SAIDAS,
    DIM_VANTAGEM,
    DIMENSOES,
    EstadoValidacao,
    Portao,
    _saidas_fii,
    _vantagem_fii,
    comparacao_de_rigor,
)


def _certificado(low, high):
    return {"backtest": {"excess_bootstrap": {"lower": low, "upper": high}}}


def test_excesso_que_atravessa_o_zero_nao_e_vantagem():
    """O gate do FII exige que o IC exista, nunca que ele exclua o zero."""
    p = _vantagem_fii(_certificado(-0.0013, 0.0045))
    assert p.ok is False
    assert "atravessa o zero" in p.detalhe


def test_excesso_inteiramente_positivo_vence_o_portao():
    assert _vantagem_fii(_certificado(0.0012, 0.0045)).ok is True


def test_sem_intervalo_e_nao_apurado_e_nao_reprovado():
    """Não medido não pode virar reprovado — some da fração e fica escrito."""
    p = _vantagem_fii({"backtest": {}})
    assert p.ok is None and p.dimensao == DIM_VANTAGEM


def test_nan_no_intervalo_tambem_e_nao_apurado():
    assert _vantagem_fii(_certificado(float("nan"), float("nan"))).ok is None


def test_fonte_inalcancavel_nao_reprova_o_universo_de_saidas(monkeypatch):
    """Base publicada sem a tabela é falta de medição, não painel sobrevivente."""
    import core.validacao_motor as vm

    class _Engine:
        def connect(self):
            raise RuntimeError("sem tabela")

    monkeypatch.setattr("core.database.get_engine", lambda: _Engine())
    p = _saidas_fii()
    assert p.ok is None and p.dimensao == DIM_SAIDAS
    assert vm  # o módulo é o alvo do teste, não o import solto


def test_toda_dimensao_aparece_para_todo_motor():
    """A pergunta não some para quem não a responde — é o defeito corrigido."""
    estados = (
        EstadoValidacao("A", "1", True,
                        portoes=(Portao("x", True, dimensao=DIM_PIT),)),
        EstadoValidacao("B", "1", False,
                        portoes=(Portao("y", False, dimensao=DIM_SAIDAS),
                                 Portao("z", None, dimensao=DIM_VANTAGEM))),
    )
    comp = comparacao_de_rigor(estados)
    assert set(comp) == {"A", "B"}
    for dims in comp.values():
        assert tuple(dims) == DIMENSOES
    assert comp["A"][DIM_SAIDAS] is None, "não declarada != declarada e reprovada"
    assert comp["B"][DIM_VANTAGEM].ok is None


def test_motor_com_uma_pergunta_nao_ganha_da_quem_declara_tres():
    """O caso concreto: 1/1 = 100% contra 1/2 = 50% sem ser mais rigoroso."""
    poucas = EstadoValidacao("Poucas", "1", True,
                             portoes=(Portao("pit", True, dimensao=DIM_PIT),))
    muitas = EstadoValidacao(
        "Muitas", "1", False,
        portoes=(Portao("pit", True, dimensao=DIM_PIT),
                 Portao("saidas", False, dimensao=DIM_SAIDAS),
                 Portao("vantagem", False, dimensao=DIM_VANTAGEM)))
    assert poucas.fracao_aprovada == 1.0
    assert muitas.fracao_aprovada < poucas.fracao_aprovada
    # ...e a comparação é o que torna a diferença visível ao usuário.
    comp = comparacao_de_rigor((poucas, muitas))
    assert comp["Poucas"][DIM_SAIDAS] is None
