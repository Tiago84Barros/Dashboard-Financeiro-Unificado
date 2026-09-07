"""Os onze cenários pedidos, mais as invariantes que eles não cobrem.

Cada cenário monta as três classes de evidência como elas chegariam num dia real
e afirma o nível **e a regra** que o produziu. Afirmar só o nível deixaria passar
o pior tipo de acerto: o número certo pela razão errada, que continua certo até o
dia em que a razão errada encontra outro caso.
"""
from __future__ import annotations

import datetime as dt

import pytest

from core.eventos_extremos import evidencias as ev
from core.eventos_extremos import niveis
from core.eventos_extremos import transicao as tr

AGORA = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


def chaves(veredito: tr.Veredito) -> set[str]:
    return {r.chave for r in veredito.regras}


def carteira_exposta(**extra) -> ev.Evidencia:
    base = dict(exposicao_direta=0.30, exposicao_indireta=0.45,
                concentracao_hhi=0.22, liquidez_disponivel=0.10,
                perda_simulada=0.28)
    base.update(extra)
    return ev.carteira(**base)


def carteira_ilesa() -> ev.Evidencia:
    return ev.carteira(exposicao_direta=0.0, exposicao_indireta=0.0,
                       concentracao_hhi=0.18, liquidez_disponivel=0.25,
                       perda_simulada=0.02)


# ── 1. Pandemia ───────────────────────────────────────────────────────────────
def test_pandemia_global_confirmada_chega_a_sistemico():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=4,
                         confiabilidade_maxima=1.0, concordancia=0.95,
                         horas_desde_publicacao=3.0, materialidade=0.95,
                         abrangencia=niveis.ABRANGENCIA_GLOBAL),
        ev.mercado({"volatilidade": 3.4, "indices": 0.22, "liquidez": 0.60,
                    "correlacao": 0.32}),
        carteira_exposta(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_SISTEMICO
    assert v.suspende_recomendacao
    assert v.notificar
    assert tr.R_LOCALIZADA not in chaves(v)


# ── 2. Guerra ─────────────────────────────────────────────────────────────────
def test_guerra_regional_confirmada_com_mercado_moderado_fica_em_crise():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, concordancia=0.85,
                         horas_desde_publicacao=5.0, materialidade=0.85,
                         abrangencia=niveis.ABRANGENCIA_REGIONAL),
        ev.mercado({"volatilidade": 1.9, "indices": 0.07, "liquidez": 0.35,
                    "correlacao": 0.14, "petroleo": 0.14}),
        carteira_exposta(exposicao_direta=0.12, exposicao_indireta=0.25,
                         perda_simulada=0.14),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_REGIONAL, agora=AGORA)
    assert niveis.NIVEL_VIGILANCIA <= v.nivel.codigo <= niveis.NIVEL_CRISE
    # Regional autoriza sistêmico; quem segurou aqui foi a severidade, não teto.
    assert tr.R_LOCALIZADA not in chaves(v)


# ── 3. Quebra de banco isolado ────────────────────────────────────────────────
def test_banco_isolado_com_carteira_muito_exposta_chega_a_crise_mas_nunca_a_sistemico():
    """A crise é real -- dele. Sistêmica ela não é, e o motor tem de dizer as duas."""
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=2.0, materialidade=0.98,
                         abrangencia=niveis.ABRANGENCIA_ATIVO),
        ev.mercado({"volatilidade": 2.8, "indices": 0.05, "liquidez": 0.75,
                    "correlacao": 0.08}),
        carteira_exposta(exposicao_direta=0.40, exposicao_indireta=0.10,
                         risco_credito=0.45, perda_simulada=0.33),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_ATIVO, agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_CRISE
    assert tr.R_LOCALIZADA in chaves(v)
    assert v.teto_aplicado == niveis.NIVEL_CRISE
    assert tr.R_SEM_EXPOSICAO not in chaves(v)


# ── 4. Risco de contágio bancário ─────────────────────────────────────────────
def test_contagio_bancario_setorial_nao_e_promovido_a_sistemico_sozinho():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=4,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=4.0, materialidade=0.90,
                         abrangencia=niveis.ABRANGENCIA_SETOR),
        ev.mercado({"volatilidade": 2.6, "indices": 0.11, "spread_credito": 220.0,
                    "correlacao": 0.28, "liquidez": 0.55}),
        carteira_exposta(risco_credito=0.30),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_SETOR, agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_CRISE
    assert v.nivel_bruto == niveis.NIVEL_SISTEMICO
    assert tr.R_LOCALIZADA in chaves(v)


# ── 5. Evento climático localizado ────────────────────────────────────────────
def test_evento_climatico_que_nao_alcanca_a_carteira_para_em_atencao():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=6.0, materialidade=0.55,
                         abrangencia=niveis.ABRANGENCIA_SETOR),
        ev.mercado({"volatilidade": 1.2, "indices": 0.02, "liquidez": 0.10,
                    "correlacao": 0.03}),
        carteira_ilesa(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_SETOR, agora=AGORA)
    assert v.nivel.codigo <= niveis.NIVEL_ATENCAO
    assert tr.R_SEM_EXPOSICAO in chaves(v)
    assert not v.suspende_recomendacao


# ── 6. Crise sistêmica ────────────────────────────────────────────────────────
def test_crise_sistemica_global_com_tudo_medido():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=5,
                         confiabilidade_maxima=1.0, concordancia=0.95,
                         horas_desde_publicacao=1.0, materialidade=1.0,
                         abrangencia=niveis.ABRANGENCIA_GLOBAL),
        ev.mercado({"volatilidade": 4.0, "indices": 0.30, "cambio": 0.15,
                    "spread_credito": 400.0, "liquidez": 0.80,
                    "correlacao": 0.40, "ouro": 0.12}),
        carteira_exposta(risco_credito=0.40, risco_cambial=0.55,
                         dependencia_geografica=0.90, perda_simulada=0.40),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_SISTEMICO
    assert v.nivel.autoriza(niveis.ACAO_REAVALIAR_TUDO)
    assert v.confianca > tr.COBERTURA_MINIMA_PARA_CRISE


# ── 7. Notícia falsa ──────────────────────────────────────────────────────────
def test_noticia_falsa_de_fonte_unica_fraca_nao_ativa_crise():
    """Blog anônimo com manchete de fim do mundo: monitorar, e só."""
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=False, n_fontes_independentes=1,
                         confiabilidade_maxima=0.25, concordancia=0.0,
                         horas_desde_publicacao=1.0, materialidade=0.98,
                         abrangencia=niveis.ABRANGENCIA_GLOBAL),
        ev.mercado({"volatilidade": 1.0, "indices": 0.0, "liquidez": 0.05,
                    "correlacao": 0.01}),
        carteira_exposta(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    assert v.nivel.codigo <= niveis.NIVEL_ATENCAO
    assert tr.R_FONTE_FRACA in chaves(v)
    assert not v.notificar
    assert not v.suspende_recomendacao


# ── 8. Informação ainda não confirmada ────────────────────────────────────────
def test_agencia_confiavel_sozinha_monitora_mas_nao_declara_crise():
    """Fonte única *confiável* é diferente de fonte única fraca.

    Reuters sozinha não é blog anônimo -- a regra que barra o blog não pode
    barrá-la do mesmo jeito. O que segura aqui é a severidade de uma fonte só,
    não um teto.
    """
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=False, n_fontes_independentes=1,
                         confiabilidade_maxima=0.90, concordancia=None,
                         horas_desde_publicacao=1.0, materialidade=0.80,
                         abrangencia=niveis.ABRANGENCIA_PAIS),
        ev.mercado({"volatilidade": 1.4, "indices": 0.03, "liquidez": 0.20,
                    "correlacao": 0.05}),
        carteira_exposta(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_PAIS, agora=AGORA)
    assert v.nivel.codigo < niveis.NIVEL_CRISE
    assert tr.R_FONTE_FRACA not in chaves(v)


def test_duas_fontes_independentes_confiaveis_elevam_em_relacao_a_uma():
    def montar(n: int) -> ev.Conjunto:
        return ev.Conjunto(
            ev.informacional(fonte_oficial=False, n_fontes_independentes=n,
                             confiabilidade_maxima=0.90, concordancia=0.9,
                             horas_desde_publicacao=1.0, materialidade=0.85,
                             abrangencia=niveis.ABRANGENCIA_PAIS),
            ev.mercado({"volatilidade": 2.0, "indices": 0.08, "liquidez": 0.40,
                        "correlacao": 0.15}),
            carteira_exposta(),
        )

    uma = tr.avaliar(montar(1), abrangencia=niveis.ABRANGENCIA_PAIS, agora=AGORA)
    duas = tr.avaliar(montar(2), abrangencia=niveis.ABRANGENCIA_PAIS, agora=AGORA)
    assert duas.severidade > uma.severidade
    assert tr.R_DUAS_FONTES in chaves(duas)
    assert tr.R_DUAS_FONTES not in chaves(uma)


# ── 9. Fonte oficial com mercado fechado ──────────────────────────────────────
def test_fonte_oficial_com_mercado_fechado_alerta_mas_nao_chega_a_sistemico():
    """Mercado fechado é ausência de evidência, não evidência de calma.

    O motor precisa alertar (fonte oficial, materialidade alta) sem tratar o
    silêncio da bolsa como desmentido -- e sem escalar ao 4, que exige preço.
    """
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=1.0, materialidade=0.95,
                         abrangencia=niveis.ABRANGENCIA_GLOBAL),
        ev.mercado(None, limitacoes=("bolsa fechada: nenhum indicador medido",)),
        carteira_exposta(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    assert tr.R_SEM_MERCADO in chaves(v)
    assert tr.R_DIVERGENCIA not in chaves(v)
    assert niveis.NIVEL_VIGILANCIA <= v.nivel.codigo <= niveis.NIVEL_CRISE
    assert v.cobertura[ev.CLASSE_MERCADO] == 0.0
    assert any("fechada" in lim for lim in v.limitacoes)


def test_fonte_oficial_grave_nao_dorme_no_nivel_1_quando_o_preco_ainda_nao_reagiu():
    """O único caso em que o piso da R2 é mesmo o que amarra.

    Fonte oficial com materialidade alta já produz, sozinha, severidade de
    Nível 2 -- então na maior parte dos cenários a R2 é redundante. Ela morde
    aqui: os preços ainda não reagiram, a divergência puxa a severidade para
    baixo do Nível 2, e sem o piso um anúncio grave e confirmado ficaria em
    "monitorar" só porque o mercado ainda não acordou.
    """
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=2,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=0.5, materialidade=0.95,
                         abrangencia=niveis.ABRANGENCIA_ATIVO),
        ev.mercado({"volatilidade": 1.0, "indices": 0.0, "liquidez": 0.0,
                    "correlacao": 0.0, "relacionados": 0.0}),
        # Exposição pequena, mas real: com exposição nula quem barraria seria a
        # R8 ("não alcança a carteira"), e o piso nunca seria exercitado.
        ev.carteira(exposicao_direta=0.04, exposicao_indireta=0.06,
                    concentracao_hhi=0.10, liquidez_disponivel=0.70,
                    perda_simulada=0.03),
    )
    sem_piso = tr._nivel_da_severidade(
        tr._severidade_do_evento(conjunto) * tr._fator_carteira(
            conjunto.carteira.severidade))
    assert sem_piso < niveis.NIVEL_VIGILANCIA, (
        "cenário deixou de exercitar o piso: a severidade sozinha já alerta")

    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_ATIVO, agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_VIGILANCIA
    assert tr.R_FONTE_OFICIAL in chaves(v)
    assert tr.R_DIVERGENCIA in chaves(v)


# ── 10. Conflito entre notícia e preços ───────────────────────────────────────
def test_manchete_forte_contra_precos_calmos_reduz_confianca_e_segura_o_nivel():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=2.0, materialidade=0.95,
                         abrangencia=niveis.ABRANGENCIA_GLOBAL),
        ev.mercado({"volatilidade": 1.05, "indices": 0.004, "liquidez": 0.02,
                    "correlacao": 0.0, "relacionados": 0.0}),
        carteira_exposta(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    assert tr.R_DIVERGENCIA in chaves(v)
    assert v.nivel.codigo <= niveis.NIVEL_VIGILANCIA
    assert not v.suspende_recomendacao

    sem_divergencia = tr.avaliar(
        ev.Conjunto(conjunto.informacional,
                    ev.mercado({"volatilidade": 3.0, "indices": 0.20,
                                "liquidez": 0.60, "correlacao": 0.30}),
                    conjunto.carteira),
        abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    assert sem_divergencia.confianca > v.confianca


# ── 11. Recuperação e encerramento ────────────────────────────────────────────
def test_recuperacao_desce_um_nivel_por_avaliacao():
    anterior = tr.Estado(nivel=niveis.NIVEL_SISTEMICO, severidade=0.90,
                         confianca=0.8, desde=AGORA - dt.timedelta(hours=30),
                         atualizado_em=AGORA - dt.timedelta(hours=1),
                         notificado_em=AGORA - dt.timedelta(hours=1))
    calmo = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=40.0, materialidade=0.30,
                         abrangencia=niveis.ABRANGENCIA_GLOBAL),
        ev.mercado({"volatilidade": 1.1, "indices": 0.01, "liquidez": 0.05,
                    "correlacao": 0.02}),
        carteira_ilesa(),
    )
    v = tr.avaliar(calmo, abrangencia=niveis.ABRANGENCIA_GLOBAL,
                   anterior=anterior, agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_CRISE
    assert tr.R_DESCIDA_GRADUAL in chaves(v)
    assert v.notificar  # mudou de nível

    passo2 = tr.avaliar(calmo, abrangencia=niveis.ABRANGENCIA_GLOBAL,
                        anterior=v.estado.__class__(
                            **{**v.estado.__dict__,
                               "desde": AGORA - dt.timedelta(hours=20)}),
                        agora=AGORA)
    assert passo2.nivel.codigo == niveis.NIVEL_VIGILANCIA


def test_rebaixamento_exige_permanencia_minima():
    """Sem isso o estado oscila entre 2 e 3 a cada coleta, e o painel pisca."""
    anterior = tr.Estado(nivel=niveis.NIVEL_CRISE, severidade=0.70,
                         confianca=0.8, desde=AGORA - dt.timedelta(hours=2),
                         notificado_em=AGORA - dt.timedelta(hours=2))
    calmo = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=2,
                         confiabilidade_maxima=1.0, concordancia=0.8,
                         horas_desde_publicacao=30.0, materialidade=0.20,
                         abrangencia=niveis.ABRANGENCIA_PAIS),
        ev.mercado({"volatilidade": 1.0, "indices": 0.0, "liquidez": 0.0,
                    "correlacao": 0.0}),
        carteira_ilesa(),
    )
    v = tr.avaliar(calmo, abrangencia=niveis.ABRANGENCIA_PAIS,
                   anterior=anterior, agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_CRISE
    assert tr.R_PERMANENCIA in chaves(v)


def test_encerramento_explicito_volta_ao_normal_de_uma_vez():
    anterior = tr.Estado(nivel=niveis.NIVEL_CRISE, severidade=0.70,
                         confianca=0.8, evento_id="ev-1",
                         desde=AGORA - dt.timedelta(hours=50))
    v = tr.encerrar(anterior, "mercado normalizado por 5 pregões", agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_NORMAL
    assert v.estado.encerrado
    assert v.estado.motivo_encerramento
    assert v.estado.evento_id == "ev-1"
    assert v.notificar
    assert tr.R_ENCERRAMENTO in chaves(v)


def test_encerramento_sem_motivo_e_recusado():
    anterior = tr.Estado(nivel=niveis.NIVEL_CRISE, desde=AGORA)
    for vazio in ("", "   ", None):
        with pytest.raises(ValueError):
            tr.encerrar(anterior, vazio, agora=AGORA)


# ── Invariantes que os cenários não cobrem ────────────────────────────────────
def test_carteira_ruim_sozinha_nao_cria_evento():
    """Concentração é característica estrutural, não notícia.

    Se a severidade fosse a média das três classes, uma carteira concentrada
    ficaria em Nível 2 para sempre -- alarme permanente movido a nada.
    """
    v = tr.avaliar(ev.Conjunto(carteira=carteira_exposta(
        exposicao_direta=0.60, exposicao_indireta=0.70, concentracao_hhi=0.50,
        liquidez_disponivel=0.0, risco_credito=0.5, risco_cambial=0.8,
        dependencia_geografica=1.0, perda_simulada=0.5)), agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_NORMAL
    assert v.severidade_evento is None
    assert v.severidade_carteira is not None


def test_conjunto_vazio_e_nivel_0_sem_explodir():
    v = tr.avaliar(ev.Conjunto(), agora=AGORA)
    assert v.nivel.codigo == niveis.NIVEL_NORMAL
    assert v.severidade == 0.0
    assert not v.notificar


def test_exposicao_maior_amplifica_o_mesmo_evento():
    info = ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                            confiabilidade_maxima=1.0, concordancia=0.9,
                            horas_desde_publicacao=2.0, materialidade=0.80,
                            abrangencia=niveis.ABRANGENCIA_PAIS)
    mkt = ev.mercado({"volatilidade": 2.2, "indices": 0.10, "liquidez": 0.45,
                      "correlacao": 0.18})
    baixa = tr.avaliar(ev.Conjunto(info, mkt, ev.carteira(
        exposicao_direta=0.03, exposicao_indireta=0.12, concentracao_hhi=0.12,
        liquidez_disponivel=0.50, perda_simulada=0.05)),
        abrangencia=niveis.ABRANGENCIA_PAIS, agora=AGORA)
    alta = tr.avaliar(ev.Conjunto(info, mkt, carteira_exposta()),
                      abrangencia=niveis.ABRANGENCIA_PAIS, agora=AGORA)
    assert alta.severidade > baixa.severidade
    assert alta.nivel.codigo >= baixa.nivel.codigo


def test_cobertura_baixa_nao_escala_para_crise():
    """Fonte oficial, mas quase nada medido: não dá para declarar crise.

    A fonte é oficial de propósito -- sem isso quem barraria seria a R1, e o
    teste passaria sem nunca exercitar a cobertura.
    """
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, materialidade=1.0),
        ev.mercado({"volatilidade": 4.0}),
        ev.Evidencia(ev.CLASSE_CARTEIRA),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    assert tr.R_COBERTURA in chaves(v)
    assert v.nivel.codigo <= niveis.NIVEL_VIGILANCIA


def test_alerta_repetido_sem_mudanca_material_nao_e_reemitido():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, concordancia=0.9,
                         horas_desde_publicacao=2.0, materialidade=0.85,
                         abrangencia=niveis.ABRANGENCIA_PAIS),
        ev.mercado({"volatilidade": 2.4, "indices": 0.12, "liquidez": 0.50,
                    "correlacao": 0.20}),
        carteira_exposta(),
    )
    primeiro = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_PAIS,
                          agora=AGORA)
    assert primeiro.notificar

    logo_depois = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_PAIS,
                             anterior=primeiro.estado,
                             agora=AGORA + dt.timedelta(hours=1))
    assert logo_depois.nivel.codigo == primeiro.nivel.codigo
    assert not logo_depois.notificar

    depois_do_silencio = tr.avaliar(
        conjunto, abrangencia=niveis.ABRANGENCIA_PAIS,
        anterior=primeiro.estado,
        agora=AGORA + dt.timedelta(hours=primeiro.nivel.silencio_horas + 1))
    assert depois_do_silencio.notificar


def test_mudanca_material_de_severidade_reemite_antes_do_silencio():
    def montar(indices: float) -> ev.Conjunto:
        return ev.Conjunto(
            ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                             confiabilidade_maxima=1.0, concordancia=0.9,
                             horas_desde_publicacao=2.0, materialidade=0.85,
                             abrangencia=niveis.ABRANGENCIA_PAIS),
            ev.mercado({"volatilidade": 2.4, "indices": indices,
                        "liquidez": 0.50, "correlacao": 0.20}),
            carteira_exposta(),
        )

    primeiro = tr.avaliar(montar(0.03), abrangencia=niveis.ABRANGENCIA_PAIS,
                          agora=AGORA)
    piorou = tr.avaliar(montar(0.22), abrangencia=niveis.ABRANGENCIA_PAIS,
                        anterior=primeiro.estado,
                        agora=AGORA + dt.timedelta(hours=1))
    assert abs(piorou.severidade - primeiro.severidade) >= tr.DELTA_MATERIAL
    assert piorou.notificar


def test_estado_guarda_a_versao_de_metodologia():
    v = tr.avaliar(ev.Conjunto(), agora=AGORA)
    assert v.estado.versao_metodologia


def test_toda_avaliacao_produz_justificativa_legivel():
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                         confiabilidade_maxima=1.0, materialidade=0.9,
                         abrangencia=niveis.ABRANGENCIA_SETOR),
        ev.mercado({"volatilidade": 2.5, "indices": 0.12}),
        carteira_exposta(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_SETOR, agora=AGORA)
    assert v.justificativa()
    for linha in v.justificativa():
        assert linha.startswith("[")
        assert ":" in linha


def test_exposicao_abaixo_do_corte_brando_nao_e_exposicao_zero():
    """Regressão: 4% do patrimônio não é "o evento não alcança esta carteira".

    A R8 comparava o valor **normalizado**, e tudo abaixo do corte brando (5%
    direta / 10% indireta) normaliza para exatamente 0,0. Uma carteira com 4,9%
    num banco que quebrou recebia teto de Nível 1 -- número certo na escala
    errada, que é como este projeto costuma perder dinheiro.
    """
    info = ev.informacional(fonte_oficial=True, n_fontes_independentes=3,
                            confiabilidade_maxima=1.0, concordancia=0.9,
                            horas_desde_publicacao=1.0, materialidade=0.98,
                            abrangencia=niveis.ABRANGENCIA_ATIVO)
    mkt = ev.mercado({"volatilidade": 2.8, "indices": 0.06, "liquidez": 0.60,
                      "correlacao": 0.10})

    quase_nada = tr.avaliar(
        ev.Conjunto(info, mkt, ev.carteira(
            exposicao_direta=0.049, exposicao_indireta=0.09,
            concentracao_hhi=0.12, liquidez_disponivel=0.30)),
        abrangencia=niveis.ABRANGENCIA_ATIVO, agora=AGORA)
    assert tr.R_SEM_EXPOSICAO not in chaves(quase_nada)

    nada_mesmo = tr.avaliar(
        ev.Conjunto(info, mkt, ev.carteira(
            exposicao_direta=0.0, exposicao_indireta=0.0,
            concentracao_hhi=0.12, liquidez_disponivel=0.30)),
        abrangencia=niveis.ABRANGENCIA_ATIVO, agora=AGORA)
    assert tr.R_SEM_EXPOSICAO in chaves(nada_mesmo)
    assert nada_mesmo.nivel.codigo < quase_nada.nivel.codigo


def test_componente_publica_a_medicao_bruta_ao_lado_da_normalizada():
    carteira = ev.carteira(exposicao_direta=0.049, exposicao_indireta=0.30,
                           liquidez_disponivel=0.25)
    assert carteira.valor_de("exposicao_direta") == 0.0
    assert carteira.bruto_de("exposicao_direta") == pytest.approx(0.049)
    assert carteira.valor_de("exposicao_indireta") > 0.0
    # Liquidez entra invertida na severidade, mas o bruto continua sendo o que
    # foi medido -- inverter os dois faria a auditoria mentir.
    assert carteira.bruto_de("liquidez_disponivel") == pytest.approx(0.25)
    assert carteira.valor_de("liquidez_disponivel") == pytest.approx(0.75)


def test_piso_registrado_nunca_aparece_como_rebaixamento():
    """Regra de piso que não levantou nada não entra na trilha.

    Um `4 -> 2` no registro faria a auditoria ler rebaixamento onde não houve.
    """
    conjunto = ev.Conjunto(
        ev.informacional(fonte_oficial=True, n_fontes_independentes=4,
                         confiabilidade_maxima=1.0, concordancia=0.95,
                         horas_desde_publicacao=1.0, materialidade=0.95,
                         abrangencia=niveis.ABRANGENCIA_GLOBAL),
        ev.mercado({"volatilidade": 4.0, "indices": 0.30, "liquidez": 0.80,
                    "correlacao": 0.40}),
        carteira_exposta(),
    )
    v = tr.avaliar(conjunto, abrangencia=niveis.ABRANGENCIA_GLOBAL, agora=AGORA)
    for regra in v.regras:
        if regra.efeito == tr.EFEITO_PISO and regra.para is not None:
            assert regra.para >= (regra.de or 0), regra.descrever()
