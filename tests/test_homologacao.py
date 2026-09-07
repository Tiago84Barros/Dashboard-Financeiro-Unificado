"""Prompt 5 — liberação gradual: flags, teto por fase, critérios e rollback.

O que estes testes tentam impedir, concretamente:

* uma flag ligada por engano liberar decisão real antes da fase (o teto);
* um critério não medido ser lido como critério atendido (o avanço mudo);
* um rollback deixar ligado o que a fase menor não deveria alcançar;
* o default de configuração ser "tudo ligado" quando ninguém configurou.
"""
from __future__ import annotations

import pytest

from core.homologacao import criterios as C
from core.homologacao import flags as F


def leitor_de(mapa):
    """Um ``_get_secret`` falso, para não depender do ambiente real."""
    return lambda nome, padrao="": mapa.get(nome, padrao)


# ── Flags: o default e o teto ────────────────────────────────────────────────
def test_sem_configuracao_so_a_fase_1_e_o_que_ela_permite():
    est = F.carregar(leitor=leitor_de({}))
    assert est.fase == F.OBSERVACAO
    assert set(est.ligadas) == {F.COLETA, F.CLASSIFICACAO}
    # nenhuma das seis que afirmam algo ao usuário
    for nome in (F.IMPACTO_HISTORICO, F.ANTIFRAGILIDADE, F.LLM,
                 F.ALTERACAO_PRIORIDADE, F.MODO_CRISE, F.ALERTAS_EXTERNOS,
                 F.RECOMENDACAO_EMERGENCIAL):
        assert not est.ativo(nome), nome


def test_flag_ligada_acima_da_fase_nao_liga_e_o_motivo_diz_qual_das_duas():
    est = F.carregar(leitor=leitor_de({
        "APP4_FASE": "2",
        "APP4_FLAG_RECOMENDACAO_EMERGENCIAL": "true",
        "APP4_FLAG_LLM": "false",
    }))
    assert not est.ativo(F.RECOMENDACAO_EMERGENCIAL)
    # o motivo separa "a fase barra" de "a flag está desligada"
    assert "fase" in est.motivo(F.RECOMENDACAO_EMERGENCIAL)
    assert "APP4_FLAG_LLM" in est.motivo(F.LLM)
    # e a tela de administração consegue mostrar quem tentou ligar e não ligou
    assert est.barradas_pela_fase == (F.RECOMENDACAO_EMERGENCIAL,)


def test_as_nove_flags_sao_independentes():
    """Desligar a LLM não pode desligar a coleta."""
    ligado_tudo = {c.variavel: "true" for c in F.CHAVES.values()}
    ligado_tudo["APP4_FASE"] = "4"
    base = F.carregar(leitor=leitor_de(ligado_tudo))
    assert len(base.ligadas) == 9
    for alvo in F.CHAVES:
        mapa = dict(ligado_tudo)
        mapa[F.CHAVES[alvo].variavel] = "false"
        est = F.carregar(leitor=leitor_de(mapa))
        assert not est.ativo(alvo)
        assert set(est.ligadas) == set(F.CHAVES) - {alvo}


@pytest.mark.parametrize("bruto", ["", "  ", "talvez", "TRUE-ish", "2", "sim!"])
def test_valor_ilegivel_nunca_liga_flag_desligada_por_padrao(bruto):
    est = F.carregar(leitor=leitor_de({"APP4_FLAG_MODO_CRISE": bruto,
                                       "APP4_FASE": "4"}))
    assert not est.ativo(F.MODO_CRISE)


@pytest.mark.parametrize("bruto", ["", "zero", "9", "-1", "4.5", "quatro"])
def test_fase_ilegivel_cai_na_fase_1_e_nao_na_4(bruto):
    est = F.carregar(leitor=leitor_de({"APP4_FASE": bruto}))
    assert est.fase == F.OBSERVACAO


def test_flag_desconhecida_falha_alto():
    """Typo no nome não pode virar 'funcionalidade desligada' em silêncio."""
    with pytest.raises(KeyError):
        F.ativo("modo_crize", estado=F.carregar(leitor=leitor_de({})))


# ── Critérios: não medido não avança e não reprova ───────────────────────────
def test_criterio_nao_medido_e_none_e_nao_false():
    c = C.EXIGIDO[F.PAINEL][0]
    assert c.avalia(None) is None
    assert c.avalia(0.99) is True
    assert c.avalia(0.10) is False


def test_ausencia_de_medida_nao_conta_como_zero():
    """Num critério 'menor melhor', zero passaria. Ausência não pode passar."""
    av = C.avaliar(F.PAINEL, {})  # nada medido
    assert not av.pode_avancar
    assert not av.reprovados          # não medir não é reprovar
    assert len(av.nao_medidos) == len(C.EXIGIDO[F.RECOMENDACAO])
    assert "não medido" in av.texto()


def test_avanco_so_com_todos_os_criterios_medidos_e_atendidos():
    medidas = {"cobertura_de_frescor": 0.97, "itens_sem_fonte": 0.0,
               "taxa_de_erro_da_coleta": 0.01}
    est = F.Estado(fase=F.OBSERVACAO, valores=dict(F.PADRAO))
    novo, av = C.avancar(est, medidas)
    assert av.pode_avancar and novo.fase == F.PAINEL

    # tirar uma medida derruba o avanço, sem reprovar o sistema
    parcial = dict(medidas)
    parcial.pop("taxa_de_erro_da_coleta")
    igual, av2 = C.avancar(est, parcial)
    assert igual.fase == F.OBSERVACAO
    assert av2.nao_medidos == ("taxa_de_erro_da_coleta",)
    assert not av2.reprovados


def test_criterio_reprovado_aparece_com_o_numero_medido():
    est = F.Estado(fase=F.OBSERVACAO, valores=dict(F.PADRAO))
    _, av = C.avancar(est, {"cobertura_de_frescor": 0.40,
                            "itens_sem_fonte": 0.0,
                            "taxa_de_erro_da_coleta": 0.01})
    assert av.reprovados == ("cobertura_de_frescor",)
    assert "0.4" in av.texto() and "NÃO atende" in av.texto()


def test_todo_criterio_pode_atender_e_pode_reprovar():
    """Portão que só podia dar um resultado é decoração, não portão."""
    for fase, lista in C.EXIGIDO.items():
        for c in lista:
            passa = c.limiar if c.sentido == C.MAIOR_MELHOR else c.limiar
            falha = (c.limiar - 1) if c.sentido == C.MAIOR_MELHOR else (c.limiar + 1)
            assert c.avalia(passa) is True, (fase, c.nome)
            assert c.avalia(falha) is False, (fase, c.nome)


def test_avancar_nao_passa_da_fase_4():
    est = F.Estado(fase=F.CRISE, valores=dict(F.PADRAO))
    novo, av = C.avancar(est, {})
    assert novo.fase == F.CRISE
    assert av.pode_avancar  # nada a exigir; simplesmente não há próxima fase


def test_avanco_preserva_as_flags_configuradas():
    valores = dict(F.PADRAO, **{F.LLM: True, F.MODO_CRISE: True})
    est = F.Estado(fase=F.OBSERVACAO, valores=valores)
    novo, _ = C.avancar(est, {"cobertura_de_frescor": 1.0,
                              "itens_sem_fonte": 0.0,
                              "taxa_de_erro_da_coleta": 0.0})
    assert novo.valores == valores
    assert novo.ativo(F.LLM)              # a Fase 2 alcança
    assert not novo.ativo(F.MODO_CRISE)   # a Fase 2 não alcança


# ── Rollback ─────────────────────────────────────────────────────────────────
def test_rollback_desliga_pelo_teto_sem_apagar_a_configuracao():
    valores = {n: True for n in F.CHAVES}
    est = F.Estado(fase=F.CRISE, valores=valores)
    assert len(est.ligadas) == 9

    volta = C.rollback(est)
    assert volta.fase == F.RECOMENDACAO
    assert volta.valores == valores            # a configuração sobrevive
    assert not volta.ativo(F.MODO_CRISE)
    assert not volta.ativo(F.ALERTAS_EXTERNOS)
    assert not volta.ativo(F.RECOMENDACAO_EMERGENCIAL)
    assert volta.ativo(F.ALTERACAO_PRIORIDADE)
    assert set(volta.barradas_pela_fase) == {
        F.MODO_CRISE, F.ALERTAS_EXTERNOS, F.RECOMENDACAO_EMERGENCIAL}


def test_rollback_direto_para_a_fase_1_desliga_tudo_que_afirma():
    est = F.Estado(fase=F.CRISE, valores={n: True for n in F.CHAVES})
    volta = C.rollback(est, para=F.OBSERVACAO)
    assert volta.fase == F.OBSERVACAO
    assert set(volta.ligadas) == {F.COLETA, F.CLASSIFICACAO}


@pytest.mark.parametrize("para,esperado", [(0, 1), (-5, 1), (9, 4), (3, 3)])
def test_rollback_nunca_sai_do_intervalo_de_fases(para, esperado):
    est = F.Estado(fase=F.CRISE, valores=dict(F.PADRAO))
    assert C.rollback(est, para=para).fase == esperado


def test_rollback_na_fase_1_nao_quebra():
    est = F.Estado(fase=F.OBSERVACAO, valores=dict(F.PADRAO))
    assert C.rollback(est).fase == F.OBSERVACAO


# ── Coerência entre os dois módulos ──────────────────────────────────────────
def test_toda_fase_acima_da_1_tem_criterio_de_entrada():
    """Fase que se alcança sem critério é liberação sem prova."""
    assert set(C.EXIGIDO) == {F.PAINEL, F.RECOMENDACAO, F.CRISE}
    for lista in C.EXIGIDO.values():
        assert lista, "fase sem nenhum critério"


def test_toda_flag_tem_fase_minima_valida_e_variavel_propria():
    variaveis = {c.variavel for c in F.CHAVES.values()}
    assert len(variaveis) == len(F.CHAVES) == 9
    for c in F.CHAVES.values():
        assert c.fase_minima in F.NOME_FASE
        assert c.efeito and c.rotulo


def test_resumo_de_auditoria_e_serializavel_e_diz_o_que_ficou_de_fora():
    import json
    est = F.Estado(fase=F.PAINEL, valores={n: True for n in F.CHAVES})
    r = est.resumo_auditoria()
    json.dumps(r)
    assert set(r["barradas_pela_fase"]) == {
        F.ALTERACAO_PRIORIDADE, F.MODO_CRISE, F.ALERTAS_EXTERNOS,
        F.RECOMENDACAO_EMERGENCIAL}
    _, av = C.avancar(est, {"erro_de_calibracao_probabilidade": 0.02})
    json.dumps(av.resumo_auditoria())
    assert av.resumo_auditoria()["pode_avancar"] is False


# ── A porta de entrada: flag que ninguem consulta e decoracao ────────────────
def test_a_coleta_recusa_quando_a_flag_esta_desligada():
    """Testa o comportamento, nao o texto: a funcao devolve o motivo."""
    from views import inteligencia_mercado as V

    est = F.Estado(fase=F.OBSERVACAO,
                   valores=dict(F.PADRAO, **{F.COLETA: False}))
    coleta, motivo = V.coletar_noticias(("PETR4",), est)
    assert coleta is None
    assert "desligada" in motivo and "APP4_FLAG_COLETA" in motivo


def test_cada_secao_conjuntural_consulta_a_sua_propria_flag():
    """Sem isto, a fase seria uma frase no README e o codigo faria outra coisa."""
    import inspect

    from views import inteligencia_mercado as V

    esperado = {
        V.render_crise: F.MODO_CRISE,
        V.render_antifragilidade: F.ANTIFRAGILIDADE,
        V.render_memoria: F.IMPACTO_HISTORICO,
        V.render_explicacao: F.LLM,
        V.coletar_noticias: F.COLETA,
    }
    for fn, flag in esperado.items():
        corpo = inspect.getsource(fn)
        assert f"hom.{flag.upper()}" in corpo, fn.__name__


def test_secao_desligada_diz_o_motivo_em_vez_de_sumir():
    """Sumico silencioso nao distingue 'desligado' de 'quebrado'."""
    import inspect

    from views import inteligencia_mercado as V

    corpo = inspect.getsource(V.secao_desligada)
    assert "estado.motivo(nome)" in corpo
    assert "chave.efeito" in corpo
    assert "NOME_FASE" in corpo


def test_tela_de_homologacao_nao_liga_nada():
    """Sem controle de acesso por papel, botao de liberacao e risco, nao recurso."""
    import inspect

    from views import homologacao as H

    fonte = inspect.getsource(H)
    assert "st.button" not in fonte and "st.toggle" not in fonte
    assert "st.checkbox" not in fonte


def test_tela_de_homologacao_nao_depende_so_de_cor():
    from views import homologacao as H

    est = F.Estado(fase=F.PAINEL, valores={n: True for n in F.CHAVES})
    simbolos = {H._simbolo(est, n)[0] for n in F.CHAVES}
    assert simbolos == {H.SIMBOLO_LIGADA, H.SIMBOLO_BARRADA}
    assert H._simbolo(F.Estado(fase=F.PAINEL, valores=dict(F.PADRAO)),
                      F.LLM)[0] == H.SIMBOLO_DESLIGADA
