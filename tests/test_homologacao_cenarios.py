"""Os 17 cenários de homologação do Prompt 5, executáveis.

Cada cenário é um teste, e cada teste nomeia o que aconteceria em produção se
ele falhasse. Rodar isto é a homologação: não há um relatório à parte que
"declare" o sistema pronto -- o relatório em ``docs/homologacao_app4.md``
transcreve o que estes testes mediram.

Nenhum cenário toca rede, banco ou provedor de LLM.

Para ver a evidência em vez de só o ponto verde::

    python -m pytest tests/test_homologacao_cenarios.py -q -s
"""
from __future__ import annotations

import datetime as dt

import pytest

from core.auditoria import trilha
from core.homologacao import criterios as C
from core.homologacao import flags as F
from core.inteligencia import llm as L
from core.seguranca import limites, travas
from tests.test_seguranca import HOSTIS, painel

AGORA = dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)


def leitor_de(mapa):
    return lambda nome, padrao="": mapa.get(nome, padrao)


def evidencia(n: int, titulo: str, texto: str) -> None:
    print(f"\n[C{n:02d}] {titulo}\n      {texto}")


# ── Grupo A: a instalação e a configuração ───────────────────────────────────
def test_c01_instalacao_nova_nao_libera_nada_que_afirme():
    """Se falhasse: um deploy novo já sairia recomendando."""
    est = F.carregar(leitor=leitor_de({}))
    evidencia(1, "instalação sem configuração",
              f"fase={est.fase} ligadas={est.ligadas}")
    assert est.fase == F.OBSERVACAO
    assert set(est.ligadas) == {F.COLETA, F.CLASSIFICACAO}


def test_c02_erro_de_digitacao_na_fase_cai_para_o_lado_seguro():
    """Se falhasse: `APP4_FASE=quatro` poderia virar Fase 4 por acidente."""
    est = F.carregar(leitor=leitor_de({"APP4_FASE": "quatro"}))
    evidencia(2, "APP4_FASE='quatro'", f"fase resultante={est.fase}")
    assert est.fase == F.OBSERVACAO


def test_c03_flag_acima_da_fase_nao_liga_e_a_tela_sabe_dizer_por_que():
    """Se falhasse: Modo Crise ligado numa instalação em Fase 2."""
    est = F.carregar(leitor=leitor_de(
        {"APP4_FASE": "2", "APP4_FLAG_MODO_CRISE": "true"}))
    evidencia(3, "Modo Crise ligado na Fase 2",
              f"ativo={est.ativo(F.MODO_CRISE)} motivo={est.motivo(F.MODO_CRISE)!r}")
    assert not est.ativo(F.MODO_CRISE)
    assert "fase" in est.motivo(F.MODO_CRISE)
    assert est.barradas_pela_fase == (F.MODO_CRISE,)


def test_c04_desligar_uma_funcionalidade_nao_desliga_as_outras():
    """Se falhasse: desligar a LLM apagaria a coleta junto."""
    mapa = {c.variavel: "true" for c in F.CHAVES.values()}
    mapa["APP4_FASE"] = "4"
    mapa["APP4_FLAG_LLM"] = "false"
    est = F.carregar(leitor=leitor_de(mapa))
    evidencia(4, "LLM desligada, resto ligado",
              f"ligadas={len(est.ligadas)} de {len(F.CHAVES)}; llm={est.ativo(F.LLM)}")
    assert not est.ativo(F.LLM)
    assert set(est.ligadas) == set(F.CHAVES) - {F.LLM}


# ── Grupo B: o avanço de fase ────────────────────────────────────────────────
def test_c05_ninguem_mediu_nada_a_fase_nao_avanca_e_ninguem_e_reprovado():
    """Se falhasse: ausência de medição viraria aprovação silenciosa."""
    est = F.Estado(fase=F.OBSERVACAO, valores=dict(F.PADRAO))
    novo, av = C.avancar(est, {})
    evidencia(5, "avanço 1→2 sem medida",
              f"fase={novo.fase} nao_medidos={av.nao_medidos} reprovados={av.reprovados}")
    assert novo.fase == F.OBSERVACAO
    assert av.nao_medidos and not av.reprovados


def test_c06_criterio_reprovado_barra_a_fase_com_o_numero_a_vista():
    """Se falhasse: a fase avançaria com 40% de cobertura de frescor."""
    est = F.Estado(fase=F.OBSERVACAO, valores=dict(F.PADRAO))
    novo, av = C.avancar(est, {"cobertura_de_frescor": 0.40,
                               "itens_sem_fonte": 0.0,
                               "taxa_de_erro_da_coleta": 0.01})
    evidencia(6, "cobertura de frescor 0,40", av.texto().splitlines()[1])
    assert novo.fase == F.OBSERVACAO
    assert av.reprovados == ("cobertura_de_frescor",)


def test_c07_com_tudo_medido_e_atendido_a_fase_avanca_sem_perder_configuracao():
    """Se falhasse: avançar exigiria reconfigurar as nove chaves."""
    valores = dict(F.PADRAO, **{F.LLM: True, F.MODO_CRISE: True})
    est = F.Estado(fase=F.OBSERVACAO, valores=valores)
    novo, av = C.avancar(est, {"cobertura_de_frescor": 0.99,
                               "itens_sem_fonte": 0.0,
                               "taxa_de_erro_da_coleta": 0.0})
    evidencia(7, "avanço 1→2 com tudo atendido",
              f"fase={novo.fase} llm={novo.ativo(F.LLM)} crise={novo.ativo(F.MODO_CRISE)}")
    assert av.pode_avancar and novo.fase == F.PAINEL
    assert novo.valores == valores
    assert novo.ativo(F.LLM) and not novo.ativo(F.MODO_CRISE)


# ── Grupo C: o rollback ──────────────────────────────────────────────────────
def test_c08_rollback_de_uma_fase_desliga_so_o_que_a_fase_menor_nao_alcanca():
    """Se falhasse: voltar de fase deixaria o Modo Crise no ar."""
    est = F.Estado(fase=F.CRISE, valores={n: True for n in F.CHAVES})
    volta = C.rollback(est)
    saiu = tuple(n for n in est.ligadas if not volta.ativo(n))
    evidencia(8, "rollback 4→3", f"saíram do ar: {saiu}")
    assert volta.fase == F.RECOMENDACAO
    assert set(saiu) == {F.MODO_CRISE, F.ALERTAS_EXTERNOS,
                         F.RECOMENDACAO_EMERGENCIAL}
    assert volta.valores == est.valores  # a configuração sobrevive


def test_c09_rollback_de_emergencia_ate_a_fase_1_desliga_tudo_que_afirma():
    """Se falhasse: o rollback mais forte deixaria recomendação viva."""
    est = F.Estado(fase=F.CRISE, valores={n: True for n in F.CHAVES})
    volta = C.rollback(est, para=F.OBSERVACAO)
    evidencia(9, "rollback 4→1", f"restaram ligadas: {volta.ligadas}")
    assert set(volta.ligadas) == {F.COLETA, F.CLASSIFICACAO}


# ── Grupo D: as portas de entrada na tela ────────────────────────────────────
def test_c10_com_a_coleta_desligada_a_tela_recusa_e_diz_o_motivo():
    """Se falhasse: a flag seria decoração e a coleta rodaria assim mesmo."""
    from views import inteligencia_mercado as V

    est = F.Estado(fase=F.OBSERVACAO,
                   valores=dict(F.PADRAO, **{F.COLETA: False}))
    coleta, motivo = V.coletar_noticias(("PETR4",), est)
    evidencia(10, "coleta desligada", f"resultado={coleta} motivo={motivo!r}")
    assert coleta is None and "APP4_FLAG_COLETA" in motivo


def test_c11_com_a_llm_desligada_a_explicacao_do_backend_continua_aparecendo():
    """Se falhasse: desligar a LLM deixaria o usuário sem explicação alguma."""
    import inspect

    from views import inteligencia_mercado as V

    corpo = inspect.getsource(V.render_explicacao)
    evidencia(11, "LLM desligada",
              "a tela cai em explicacao_deterministica, não em tela vazia")
    assert "explicacao_deterministica" in corpo
    assert "estado.ativo(hom.LLM)" in corpo
    # e o backend realmente produz esse texto sem LLM nenhuma
    exp = L.explicacao_deterministica(painel("Empresa aprova plano de investimento"))
    assert exp.texto and not exp.gerada_por_llm


# ── Grupo E: conteúdo externo hostil ─────────────────────────────────────────
@pytest.mark.parametrize("categoria,manchete", HOSTIS)
def test_c12_noticia_que_tenta_instruir_e_cercada_e_denunciada(categoria, manchete):
    """Se falhasse: uma manchete poderia mandar na LLM."""
    pn = painel(manchete)
    seg = L.contexto_segregado(pn)
    assert seg.itens_hostis, manchete
    assert categoria in {t.categoria for t in seg.tentativas}
    # e o texto hostil continua legível para auditoria, apenas neutralizado
    assert seg.marcador not in manchete


def test_c13_numero_que_so_existe_na_manchete_nao_ancora_afirmacao_do_app():
    """Se falhasse (A-148): o atacante escolheria o número que o APP4 afirma."""
    pn = painel("Analista vê queda de 37,4% na PETR4 nos próximos dias")
    seg = L.contexto_segregado(pn)
    sem = L.validar("A queda esperada é de 37,4% segundo a análise do painel.",
                    pn, seg=seg)
    com = L.validar("A notícia relata uma queda de 37,4%; o painel não mediu "
                    "esse número.", pn, seg=seg)
    evidencia(13, "número só da manchete",
              f"sem atribuir: aprovada={sem.aprovada} inventados={sem.numeros_inventados}; "
              f"atribuindo: aprovada={com.aprovada} externos={com.numeros_de_conteudo_externo}")
    assert not sem.aprovada and sem.numeros_inventados == ("37,4",)
    assert com.aprovada and com.numeros_de_conteudo_externo == ("37,4",)


# ── Grupo F: as travas de circuito ───────────────────────────────────────────
def test_c14_dados_vencidos_impedem_recomendacao_de_emergencia():
    """Se falhasse: o APP4 mandaria agir em cima de dado velho."""
    est = travas.avaliar(dados_vencidos=True)
    evidencia(14, "dados vencidos", str(est.resumo_auditoria()))
    assert not est.permite(travas.RECOMENDACAO_EMERGENCIAL)
    assert est.motivos(travas.RECOMENDACAO_EMERGENCIAL)


def test_c15_provedores_divergentes_rebaixam_confianca_e_nao_bloqueiam():
    """Se falhasse: incerteza viraria portão, e a tela ficaria muda."""
    est = travas.avaliar(provedores_divergem=True)
    evidencia(15, "provedores divergem",
              f"bloqueios={est.bloqueios} confianca_rebaixada={est.confianca_rebaixada}")
    assert est.confianca_rebaixada and not est.bloqueios


def test_c16_auditoria_indisponivel_bloqueia_mudanca_estrategica():
    """Se falhasse: haveria mudança sem registro de por quê."""
    est = travas.avaliar(auditoria_falhou=True)
    evidencia(16, "auditoria indisponível", str(est.bloqueios))
    assert not est.permite(travas.MUDANCA_ESTRATEGICA)
    # e a trilha recusa alto em vez de engolir a falha
    assert issubclass(trilha.AuditoriaIndisponivel, RuntimeError)


def test_c17_limite_de_uso_nega_e_diz_quando_libera():
    """Se falhasse: um laço de tela esgotaria a cota do provedor sem aviso."""
    contador = limites.Contador()
    regra = limites.PADRAO[limites.NOTIFICACAO_EXTERNA]
    for _ in range(regra.maximo):
        assert contador.permitir(limites.NOTIFICACAO_EXTERNA, agora=AGORA).permitido
    v = contador.permitir(limites.NOTIFICACAO_EXTERNA, agora=AGORA)
    evidencia(17, "limite de notificação externa",
              f"permitido={v.permitido} espera={v.espera_s:.0f}s motivo={v.motivo!r}")
    assert not v.permitido and v.espera_s > 0 and v.motivo
