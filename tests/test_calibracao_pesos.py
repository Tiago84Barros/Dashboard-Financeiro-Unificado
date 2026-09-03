"""Pesos versionados: portões de promoção e volta atrás.

A instrução lista seis condições em que **não** se coloca em produção. O que
estes testes verificam é que cada uma delas bloqueia sozinha, e que "não medido"
bloqueia dizendo que não mediu -- porque reprovar manda ajustar o modelo e não
medir manda arrumar a medição.

Há um sétimo portão, e ele nasceu de uma medição: com só os seis, um motor que
apontou 2 de 2.146 eventos reais **passava** no portão de alarme excessivo.
Quem não fala nunca dá alarme falso.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from core.calibracao import metricas as m
from core.calibracao import pesos as p
from core.noticias import relevancia as rel


def _portoes_todos_bons() -> tuple[p.Portao, ...]:
    confusao = m.avaliar_deteccao(
        [(True, True)] * 10 + [(True, False)] * 2
        + [(False, True)] * 3 + [(False, False)] * 85)
    calibracao = m.avaliar_probabilidade(
        [(0.7, True)] * 70 + [(0.7, False)] * 30
        + [(0.1, True)] * 3 + [(0.1, False)] * 27)
    comparacao = m.comparar(
        m.avaliar_politica([100.0, 112.0], turnover=0.2, custo_por_giro=0.01),
        m.avaliar_politica([100.0, 105.0]))
    estabilidade = m.Estabilidade({"2019": 0.04, "2020": 0.06, "2021": 0.02})
    return p.avaliar_portoes(confusao=confusao, calibracao=calibracao,
                             comparacao=comparacao,
                             variaveis={"preco_d1": True, "volatilidade": True},
                             estabilidade=estabilidade)


def test_o_prior_nasce_declarado_como_nao_calibrado():
    """Peso escrito com o motivo ao lado ainda é hipótese, não medida."""
    assert p.PRIOR.calibrado is False
    assert p.PRIOR.origem == "prior_declarado"
    assert p.PRIOR.validar() == []
    assert any("nunca medidos" in lim for lim in p.PRIOR.limitacoes)


def test_prior_reproduz_exatamente_os_pesos_de_relevancia_em_uso():
    assert p.PRIOR.como_pesos() == rel.PESOS_PADRAO
    assert set(p.PRIOR.notas_tipo) == set(
        __import__("core.noticias.taxonomia", fromlist=["x"]).POR_CHAVE)


def test_teto_de_materialidade_impede_noticia_do_dia_de_virar_tese():
    """O limite que a instrução pede contra notícia dominar o estrutural."""
    exagerado = replace(
        p.PRIOR, versao="teste-materialidade",
        pesos_relevancia={**p.PRIOR.pesos_relevancia,
                          rel.MATERIALIDADE: 0.50,
                          rel.RELACAO_ATIVO: 0.05,
                          rel.CONFIABILIDADE: 0.10})
    avisos = exagerado.validar()
    assert any("acima do teto" in a for a in avisos)


def test_nota_de_tipo_fora_da_faixa_e_tipo_desconhecido_saem_como_aviso():
    torto = replace(p.PRIOR, versao="teste-notas",
                    notas_tipo={"tipo_inventado": (1.5, 0.5)})
    avisos = torto.validar()
    assert any("tipo desconhecido" in a for a in avisos)
    assert any("fora de [0,1]" in a for a in avisos)


# ── Portões ─────────────────────────────────────────────────────────────────
def test_conjunto_com_todos_os_portoes_bons_e_promovido():
    portoes = _portoes_todos_bons()
    assert all(g.ok is True for g in portoes), [g.descrever() for g in portoes]

    registro = p.Registro()
    novo = replace(p.PRIOR, versao="1.1.0", calibrado=True, origem="medido")
    assert registro.promover(novo, portoes)["promovido"] is True
    assert registro.ativo().versao == "1.1.0"


def test_nada_medido_bloqueia_e_diz_que_nao_mediu():
    """Portões em ``None`` não são reprovações."""
    portoes = p.avaliar_portoes()
    assert all(g.ok is None for g in portoes)
    pode, impedimentos = p.pode_promover(portoes)
    assert pode is False
    assert len(impedimentos) == len(portoes)
    assert all(i.startswith("nao medido:") for i in impedimentos)
    assert not any(i.startswith("reprovou:") for i in impedimentos)


@pytest.mark.parametrize("quebrar,esperado", [
    ("alarme", "alarmes_excessivos"),
    ("mudo", "deteccao_util"),
    ("calibracao", "probabilidade_calibrada"),
    ("turnover", "turnover"),
    ("risco", "risco"),
    ("tempo_real", "disponivel_em_tempo_real"),
    ("estabilidade", "estabilidade"),
])
def test_cada_condicao_da_instrucao_bloqueia_sozinha(quebrar, esperado):
    base = dict(
        confusao=m.avaliar_deteccao([(True, True)] * 10 + [(True, False)] * 2
                                    + [(False, True)] * 3
                                    + [(False, False)] * 85),
        calibracao=m.avaliar_probabilidade([(0.7, True)] * 70
                                           + [(0.7, False)] * 30),
        comparacao=m.comparar(
            m.avaliar_politica([100.0, 112.0], turnover=0.2,
                               custo_por_giro=0.01),
            m.avaliar_politica([100.0, 105.0])),
        variaveis={"preco_d1": True},
        estabilidade=m.Estabilidade({"2019": 0.04, "2020": 0.06}),
    )
    if quebrar == "mudo":
        # O caso medido na primeira rodada real: 2 disparos em 2.146 eventos.
        base["confusao"] = m.avaliar_deteccao([(True, True)] * 1
                                              + [(True, False)] * 1
                                              + [(False, True)] * 283
                                              + [(False, False)] * 1861)
    elif quebrar == "alarme":
        base["confusao"] = m.avaliar_deteccao([(True, False)] * 40
                                              + [(False, False)] * 60
                                              + [(True, True)] * 5)
    elif quebrar == "calibracao":
        base["calibracao"] = m.avaliar_probabilidade([(0.7, True)] * 20
                                                     + [(0.7, False)] * 80)
    elif quebrar == "turnover":
        base["comparacao"] = m.comparar(
            m.avaliar_politica([100.0, 112.0], turnover=3.0,
                               custo_por_giro=0.001),
            m.avaliar_politica([100.0, 105.0]))
    elif quebrar == "risco":
        base["comparacao"] = m.comparar(
            m.avaliar_politica([100.0, 60.0, 112.0]),
            m.avaliar_politica([100.0, 95.0, 105.0]))
    elif quebrar == "tempo_real":
        base["variaveis"] = {"preco_d1": True, "lucro_do_trimestre": False}
    elif quebrar == "estabilidade":
        base["estabilidade"] = m.Estabilidade({"2019": 0.40, "2020": -0.05})

    portoes = p.avaliar_portoes(**base)
    reprovados = [g.nome for g in portoes if g.ok is False]
    assert esperado in reprovados

    registro = p.Registro()
    resultado = registro.promover(replace(p.PRIOR, versao="9.9.9"), portoes)
    assert resultado["promovido"] is False
    assert registro.ativo().versao == p.PRIOR.versao   # o ativo não se mexeu


def test_motor_mudo_passa_no_alarme_e_e_barrado_pela_deteccao():
    """Os seis portões da instrução, sozinhos, promoveriam um motor mudo.

    Números da primeira rodada contra o armazém local (FII, horizonte 5): 2.146
    eventos avaliados, 2 apontados, 283 movimentos relevantes sem aviso. Falso
    alarme de 0,05% -- quem não fala nunca erra o alarme.
    """
    confusao = m.avaliar_deteccao([(True, True)] * 1 + [(True, False)] * 1
                                  + [(False, True)] * 283
                                  + [(False, False)] * 1861)
    portoes = {g.nome: g for g in p.avaliar_portoes(confusao=confusao)}
    assert portoes["alarmes_excessivos"].ok is True
    assert portoes["deteccao_util"].ok is False
    assert "passaram sem aviso" in portoes["deteccao_util"].motivo


def test_variavel_indisponivel_no_instante_da_decisao_e_nomeada():
    portao = {g.nome: g for g in p.avaliar_portoes(
        variaveis={"preco_d1": True,
                   "lucro_do_trimestre": False})}["disponivel_em_tempo_real"]
    assert portao.ok is False
    assert "lucro_do_trimestre" in portao.motivo


# ── Registro e rollback ─────────────────────────────────────────────────────
def test_reverter_volta_ao_anterior_e_o_piso_e_sempre_o_prior():
    registro = p.Registro()
    portoes = _portoes_todos_bons()
    registro.promover(replace(p.PRIOR, versao="1.1.0", calibrado=True), portoes)
    registro.promover(replace(p.PRIOR, versao="1.2.0", calibrado=True), portoes)
    assert registro.ativo().versao == "1.2.0"

    assert registro.reverter()["versao_ativa"] == "1.1.0"
    assert registro.reverter()["versao_ativa"] == p.PRIOR.versao
    # E daqui não se cai mais: o prior existe sempre.
    voltou = registro.reverter()
    assert voltou["versao_ativa"] == p.PRIOR.versao
    assert voltou["calibrado"] is False


def test_registrar_nao_ativa():
    registro = p.Registro()
    registro.registrar(replace(p.PRIOR, versao="1.5.0", calibrado=True))
    assert "1.5.0" in registro.versoes()
    assert registro.ativo().versao == p.PRIOR.versao


def test_serializacao_carimba_as_duas_versoes_de_metodologia():
    """Peso sem versão de taxonomia ao lado não é reproduzível."""
    from core.calibracao import CALIBRACAO_VERSAO
    from core.noticias import taxonomia as tax
    dados = p.PRIOR.como_dict()
    assert dados["calibracao_versao"] == CALIBRACAO_VERSAO
    assert dados["taxonomia_versao"] == tax.TAXONOMIA_VERSAO
    assert dados["calibrado"] is False
