# -*- coding: utf-8 -*-
"""A-153: dois rotulos iguais medindo coisas diferentes.

O componente "Metodologia validada" da secao EUA media a IDADE DA VITRINE. A
pergunta "foi gerada pelo ranqueador de hoje?" e legitima, mas nao e "a formula
foi verificada fora da amostra?". Sob o rotulo errado o EUA tirava 100 nesse
item enquanto a B3, que le `validation_readiness` de verdade, tirava 50: a
classe que TEM a medicao pontuava pior que a que nao tinha.
"""
from __future__ import annotations

import core.confianca_secao as cs
from core.validacao_motor import EstadoValidacao


def _comp(secao, nome):
    return next((c for c in secao.componentes if c.nome == nome), None)


def _us(monkeypatch, estado):
    monkeypatch.setattr("core.validacao_motor.validacao_us", lambda *a, **k: estado)
    return cs.confianca_us()


def test_as_duas_perguntas_viraram_dois_componentes():
    s = cs.confianca_us()
    assert _comp(s, "Vitrine na versao corrente") is not None
    assert _comp(s, "Metodologia validada") is not None


def test_o_peso_do_bloco_foi_repartido_e_nao_ampliado():
    s = cs.confianca_us()
    bloco = (_comp(s, "Vitrine na versao corrente").peso
             + _comp(s, "Metodologia validada").peso)
    assert abs(bloco - 0.25) < 1e-9


def test_a_soma_dos_pesos_continua_um():
    s = cs.confianca_us()
    assert abs(sum(c.peso for c in s.componentes) - 1.0) < 1e-9


def test_vitrine_fresca_nao_afirma_mais_metodologia_validada(monkeypatch):
    """Vitrine gerada hoje com PIT pendente: um componente alto, outro ZERO.

    Ate 27/08/2026 este teste exigia 50.0, herdado da formula
    ``100.0 if ok else 50.0``. O 50 nunca foi medicao: era credito concedido a
    uma validacao que nao aconteceu. Com um unico portao e ele reprovado, a
    resposta honesta e zero -- credito parcial exige que alguma condicao real
    tenha sido cumprida, e aqui nenhuma foi.
    """
    s = _us(monkeypatch, EstadoValidacao("Empresas Americanas", "0.5.0", False,
                                         ("sem score_vintages",)))
    assert _comp(s, "Metodologia validada").pct == 0.0


def test_pit_aprovado_vale_cem(monkeypatch):
    s = _us(monkeypatch, EstadoValidacao("Empresas Americanas", "0.5.0", True))
    assert _comp(s, "Metodologia validada").pct == 100.0


def test_nao_apurado_nao_vira_zero_nem_cem(monkeypatch):
    """`pct=None` sai da media ponderada e se declara -- e a regra do modulo."""
    s = _us(monkeypatch, EstadoValidacao("Empresas Americanas", "0.5.0", None,
                                         detalhe="nao apurado: RuntimeError"))
    c = _comp(s, "Metodologia validada")
    assert c.pct is None and c.medido is False


def test_b3_e_eua_medem_a_mesma_coisa_sob_o_mesmo_rotulo():
    """Era o defeito: mesmo nome, uma lia PIT e a outra lia idade de vitrine.

    O valor deixou de ser do conjunto {None, 50, 100}: agora e a fracao dos
    portoes vencidos, entao qualquer percentual e legitimo. O que este teste
    protege e a origem -- as duas secoes leem `core.validacao_motor`, e a
    evidencia diz quantos portoes foram apurados em vez de afirmar sem mostrar.
    """
    for secao in (cs.confianca_b3(), cs.confianca_us()):
        c = _comp(secao, "Metodologia validada")
        assert c is not None
        assert c.pct is None or 0.0 <= c.pct <= 100.0
        assert c.pct is None or "portoes" in c.evidencia


def test_credito_parcial_so_com_portao_de_fato_vencido(monkeypatch):
    """Dois portoes, um vencido: 50 -- agora medido, nao cravado."""
    from core.validacao_motor import Portao
    s = _us(monkeypatch, EstadoValidacao(
        "Empresas Americanas", "0.5.0", False, ("x",),
        portoes=(Portao("Painel PIT", True), Portao("Universo de deslistadas", False))))
    c = _comp(s, "Metodologia validada")
    assert c.pct == 50.0
    assert "1/2 portoes" in c.evidencia


def test_portao_nao_apurado_sai_da_conta_e_se_declara(monkeypatch):
    """Nao apurado nunca vira reprovado: some da fracao e e nomeado."""
    from core.validacao_motor import Portao
    s = _us(monkeypatch, EstadoValidacao(
        "Empresas Americanas", "0.5.0", False, ("x",),
        portoes=(Portao("Painel PIT", False),
                 Portao("Universo de deslistadas", None, "fonte inalcancavel"))))
    c = _comp(s, "Metodologia validada")
    assert c.pct == 0.0
    assert "0/1 portoes" in c.evidencia
    assert "nao apurado: Universo de deslistadas" in c.evidencia


def test_eua_tem_o_portao_de_sobrevivencia_que_so_a_b3_tinha():
    """A-153 outra vez, em outro eixo: quem media a limitacao pontuava pior.

    O motor americano tinha UM portao ("existe painel PIT") enquanto a B3 tinha
    dois, e o segundo da B3 era sobrevivencia. O EUA nao estava melhor -- estava
    sem regua, num universo onde `delisted_date` e NULL em todos os registros.
    """
    from core.validacao_motor import validacao_us
    nomes = [p.nome for p in validacao_us().portoes]
    assert "Universo de deslistadas" in nomes


# --- A-163: a secao que perdia 60% do proprio peso sem o numero se mexer ----


class _EngineMorta:
    """Banco fora do ar: qualquer uso levanta, como no CI sem Postgres."""

    def connect(self):
        raise RuntimeError("sem banco")

    def begin(self):
        raise RuntimeError("sem banco")


def test_pesos_continuam_somando_um_com_o_banco_fora(monkeypatch):
    """Componente sem medicao tem de APARECER declarando que saiu.

    Ate 28/08/2026 o bloco de Integridade e Frescor vivia dentro de um `try`
    que, ao falhar, so escrevia uma nota. Os dois componentes sumiam da lista e
    a media se renormalizava sobre os 40% restantes: a secao passava a ser
    medida por menos da metade do proprio peso e a porcentagem exibida nao se
    movia. Foi o CI, sem banco, que mostrou isso -- localmente a soma sempre
    dava 1,0 porque o banco sempre respondia.
    """
    for secao in (cs.confianca_b3, cs.confianca_fii, cs.confianca_us):
        s = secao(engine=_EngineMorta())
        assert abs(sum(c.peso for c in s.componentes) - 1.0) < 1e-9, s.nome
        faltantes = {c.nome for c in s.componentes if c.pct is None}
        assert {"Integridade", "Frescor"} <= faltantes, s.nome
