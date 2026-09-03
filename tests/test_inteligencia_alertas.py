"""Política de alerta: canal por nível, dedup e o que sai de casa.

O teste que mais importa aqui é
``test_mensagem_externa_nao_carrega_dado_sensivel``: ele é a única prova de que
o requisito "nunca expor informações sensíveis em notificações externas" é
estrutural, e não uma promessa no texto.
"""
from __future__ import annotations

import datetime as dt

import pytest

from core.eventos_extremos import evidencias as ev
from core.eventos_extremos import niveis
from core.eventos_extremos import transicao as tr
from core.inteligencia import alertas as al

AGORA = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


def conjunto(*, oficial=True, fontes=3, queda=0.09, exposicao=0.7):
    return ev.Conjunto(
        informacional=ev.informacional(
            fonte_oficial=oficial, n_fontes_independentes=fontes,
            confiabilidade_maxima=0.95, concordancia=0.9,
            horas_desde_publicacao=1.0, materialidade=0.9,
            abrangencia=niveis.ABRANGENCIA_PAIS),
        mercado=ev.mercado({"indices": queda, "volatilidade": 2.2,
                            "correlacao": 0.25},
                           fontes={"indices": "b3", "volatilidade": "b3",
                                   "correlacao": "b3"}),
        carteira=ev.carteira(exposicao_direta=exposicao, exposicao_indireta=0.3,
                             liquidez_disponivel=0.2, perda_simulada=-0.22))


def veredito(**kw):
    return tr.avaliar(conjunto(**kw), abrangencia=niveis.ABRANGENCIA_PAIS,
                      evento_id="ev1", agora=AGORA)


def calmo():
    """Um conjunto que não sustenta nível alto."""
    return ev.Conjunto(
        informacional=ev.informacional(
            fonte_oficial=False, n_fontes_independentes=1,
            confiabilidade_maxima=0.4, concordancia=0.5,
            horas_desde_publicacao=20.0, materialidade=0.2,
            abrangencia=niveis.ABRANGENCIA_ATIVO),
        mercado=ev.mercado({"indices": 0.005, "volatilidade": 1.0},
                           fontes={"indices": "b3", "volatilidade": "b3"}),
        carteira=ev.carteira(exposicao_direta=0.02, liquidez_disponivel=0.6))


# ── Canal por nível ──────────────────────────────────────────────────────────
def test_nivel_1_fica_no_painel():
    canal, motivo = al.canal_para(niveis.NIVEL_ATENCAO, afeta_carteira=True,
                                  prefs=al.Preferencias(), infraestrutura=True)
    assert canal == al.CANAL_PAINEL and "painel" in motivo.lower()


def test_nivel_2_sem_exposicao_nao_notifica():
    canal, motivo = al.canal_para(niveis.NIVEL_VIGILANCIA, afeta_carteira=False,
                                  prefs=al.Preferencias(), infraestrutura=True)
    assert canal == al.CANAL_PAINEL and "carteira" in motivo


def test_nivel_2_com_exposicao_notifica_no_painel_destacado():
    canal, _ = al.canal_para(niveis.NIVEL_VIGILANCIA, afeta_carteira=True,
                             prefs=al.Preferencias(), infraestrutura=True)
    assert canal == al.CANAL_DESTAQUE


def test_nivel_3_sem_infraestrutura_nao_inventa_canal():
    canal, motivo = al.canal_para(
        niveis.NIVEL_CRISE, afeta_carteira=True, infraestrutura=False,
        prefs=al.Preferencias(autorizou_externo=True, canais_externos=("x",)))
    assert canal == al.CANAL_DESTAQUE and "configurado" in motivo


def test_nivel_4_sem_autorizacao_nao_sai_por_gravidade():
    """Gravidade não substitui consentimento."""
    canal, motivo = al.canal_para(
        niveis.NIVEL_SISTEMICO, afeta_carteira=True, infraestrutura=True,
        prefs=al.Preferencias(autorizou_externo=False, canais_externos=("x",)))
    assert canal == al.CANAL_DESTAQUE and "autorização" in motivo


def test_nivel_4_com_infra_e_autorizacao_usa_canal_externo():
    canal, _ = al.canal_para(
        niveis.NIVEL_SISTEMICO, afeta_carteira=True, infraestrutura=True,
        prefs=al.Preferencias(autorizou_externo=True, canais_externos=("tg",)))
    assert canal == al.CANAL_EXTERNO


def test_todo_canal_tem_icone_e_rotulo():
    for canal in al.CANAIS:
        ap = al.APARENCIA_CANAL[canal]
        assert ap["icone"].strip() and ap["rotulo"].strip()


# ── Redação externa: falha fechada ───────────────────────────────────────────
def test_mensagem_externa_nao_carrega_dado_sensivel():
    """O corpo interno cita a carteira; o texto externo não pode citá-la."""
    a = al.montar(veredito(), tipo_evento="quebra_de_banco", evento_id="ev1",
                  afeta_carteira=True, infraestrutura=True, agora=AGORA,
                  prefs=al.Preferencias(autorizou_externo=True,
                                        canais_externos=("tg",)),
                  resumo="PETR4 concentra 18% da carteira; perda simulada de "
                         "R$ 42.310,00 e prioridade de aporte 0,42.")
    externo = a.texto_externo()
    for proibido in ("PETR4", "42.310", "18%", "0,42", "R$", "carteira;"):
        assert proibido not in externo, f"{proibido!r} vazou para fora"
    assert "Nível" in externo and "quebra de banco" in externo


def test_texto_externo_e_reconstruido_e_nao_filtrado():
    """Campo novo no alerta não escapa por acidente para a mensagem externa."""
    a = al.montar(veredito(), tipo_evento="pandemia", evento_id="ev1",
                  afeta_carteira=True, infraestrutura=True, agora=AGORA,
                  prefs=al.Preferencias(autorizou_externo=True,
                                        canais_externos=("tg",)))
    assert a.corpo not in a.texto_externo()


# ── Montagem ─────────────────────────────────────────────────────────────────
def test_nivel_alto_gera_alerta_com_titulo_factual():
    a = al.montar(veredito(), tipo_evento="alta_de_juros", evento_id="ev1",
                  afeta_carteira=True, agora=AGORA)
    assert a is not None
    assert "!" not in a.titulo
    assert a.titulo.startswith(f"Nível {a.nivel_codigo}")


def test_nivel_baixo_sem_notificacao_nao_gera_alerta():
    v = tr.avaliar(calmo(), abrangencia=niveis.ABRANGENCIA_ATIVO,
                   evento_id="calmo", agora=AGORA)
    assert v.nivel.codigo <= niveis.NIVEL_ATENCAO
    assert al.montar(v, tipo_evento="ruido", evento_id="calmo",
                     agora=AGORA) is None


def test_alerta_registra_por_que_nao_saiu():
    a = al.montar(veredito(), tipo_evento="x", evento_id="ev1",
                  afeta_carteira=True, infraestrutura=False, agora=AGORA)
    assert a.motivo_canal and a.historico


def test_teto_aplicado_aparece_no_corpo():
    v = tr.avaliar(conjunto(), abrangencia=niveis.ABRANGENCIA_ATIVO,
                   evento_id="ev1", agora=AGORA)
    if not v.teto_aplicado:
        pytest.skip("este conjunto não acionou teto")
    a = al.montar(v, tipo_evento="x", evento_id="ev1", afeta_carteira=True,
                  agora=AGORA)
    assert "barrada por regra de contenção" in a.corpo


def test_severidade_diferente_gera_id_diferente():
    """Agravamento não pode se confundir com repetição."""
    a = al.montar(veredito(queda=0.06), tipo_evento="x", evento_id="ev1",
                  afeta_carteira=True, agora=AGORA)
    b = al.montar(veredito(queda=0.30), tipo_evento="x", evento_id="ev1",
                  afeta_carteira=True, agora=AGORA)
    assert a.severidade != b.severidade
    assert a.id != b.id


def test_dedup_material_mora_na_transicao_e_nao_e_duplicado_aqui():
    """Mesma avaliação repetida: `notificar` cai e o alerta diz por quê."""
    v1 = tr.avaliar(conjunto(), abrangencia=niveis.ABRANGENCIA_PAIS,
                    evento_id="ev1", agora=AGORA)
    v2 = tr.avaliar(conjunto(), abrangencia=niveis.ABRANGENCIA_PAIS,
                    anterior=v1.estado, evento_id="ev1",
                    agora=AGORA + dt.timedelta(minutes=30))
    assert v1.notificar and not v2.notificar
    a = al.montar(v2, tipo_evento="x", evento_id="ev1", afeta_carteira=True,
                  agora=AGORA + dt.timedelta(minutes=30))
    assert "Sem mudança material" in a.motivo_canal


# ── Entrega, leitura e atualização ───────────────────────────────────────────
def _externo():
    return al.montar(veredito(), tipo_evento="x", evento_id="ev1",
                     afeta_carteira=True, infraestrutura=True, agora=AGORA,
                     prefs=al.Preferencias(autorizou_externo=True,
                                           canais_externos=("tg",)))


def test_entrega_bem_sucedida_e_registrada():
    enviados = []
    a = al.enviar(_externo(), infraestrutura=True,
                  prefs=al.Preferencias(autorizou_externo=True,
                                        canais_externos=("tg",)),
                  transportar=lambda c, t: enviados.append((c, t)),
                  agora=AGORA)
    assert a.estado_entrega == al.ENTREGUE and a.entregue_em == AGORA
    assert enviados and enviados[0][0] == "tg"
    assert any("entregue" in h for h in a.historico)


def test_sem_autorizacao_nao_envia_mesmo_com_transporte():
    enviados = []
    a = al.enviar(_externo(), infraestrutura=True,
                  prefs=al.Preferencias(autorizou_externo=False,
                                        canais_externos=("tg",)),
                  transportar=lambda c, t: enviados.append(c), agora=AGORA)
    assert a.estado_entrega == al.BLOQUEADO_SEM_AUTORIZACAO and not enviados


def test_sem_transporte_nao_finge_entrega():
    a = al.enviar(_externo(), infraestrutura=True,
                  prefs=al.Preferencias(autorizou_externo=True,
                                        canais_externos=("tg",)), agora=AGORA)
    assert a.estado_entrega == al.BLOQUEADO_SEM_INFRA
    assert a.entregue_em is None


def test_falha_do_transporte_vira_registro_e_nao_excecao():
    def explode(_c, _t):
        raise RuntimeError("token inválido")

    a = al.enviar(_externo(), infraestrutura=True,
                  prefs=al.Preferencias(autorizou_externo=True,
                                        canais_externos=("tg",)),
                  transportar=explode, agora=AGORA)
    assert a.estado_entrega == al.FALHOU and "token inválido" in a.detalhe_entrega


def test_severidade_abaixo_da_escolhida_e_suprimida():
    a = al.enviar(_externo(), infraestrutura=True,
                  prefs=al.Preferencias(autorizou_externo=True,
                                        canais_externos=("tg",),
                                        severidade_minima=niveis.NIVEL_SISTEMICO
                                        + 1),
                  transportar=lambda c, t: None, agora=AGORA)
    assert a.estado_entrega == al.SUPRIMIDO_ABAIXO_DA_SEVERIDADE


def test_leitura_e_registrada_uma_vez_so():
    a = al.marcar_lido(_externo(), agora=AGORA)
    b = al.marcar_lido(a, agora=AGORA + dt.timedelta(hours=1))
    assert a.lido_em == AGORA and b.lido_em == AGORA
    assert len(b.historico) == len(a.historico)


def test_atualizacao_preserva_o_historico():
    a = _externo()
    b = al.atualizar(a, corpo="situação agravada", motivo="nova evidência",
                     agora=AGORA + dt.timedelta(hours=2))
    assert b.corpo == "situação agravada"
    assert len(b.historico) == len(a.historico) + 1
    assert any("nova evidência" in h for h in b.historico)
