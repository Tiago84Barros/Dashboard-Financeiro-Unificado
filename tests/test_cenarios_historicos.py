"""Os 11 cenários históricos exigidos, e quem os confere.

O requisito pede **pelo menos 11 cenários históricos**. O módulo tinha 5, e o
critério de homologação ``cenarios_historicos_reproduzidos`` (limiar 11) não
tinha medidor nenhum: saía ``None`` para sempre, e a Fase 4 não podia avançar
por ausência de medição -- não por reprovação.

Dois defeitos evitados aqui, e os testes existem para provar que foram:

* **contar a lista não é medir.** ``len(SCENARIOS)`` fecharia o critério sem
  conferir nada -- portão que só pode dar um resultado
  (``memoria: gate-que-so-dava-false``). A conferência aplica o cenário pelo
  caminho de código real e compara com o retorno observado no índice.
* **medidor que devolve zero por não achar nada aprova o critério.** Os dois
  critérios "menor melhor" da Fase 4 continuam **sem** medidor, de propósito.
"""
from __future__ import annotations

import dataclasses

from core import stress_tests as S
from core.homologacao import criterios as C
from core.homologacao import medicoes as M


# -- Os 11 -------------------------------------------------------------------
def test_ha_pelo_menos_os_onze_cenarios_exigidos():
    assert len(S.SCENARIOS) >= 11


def test_nenhum_cenario_repetido():
    nomes = [c.nome for c in S.SCENARIOS]
    assert len(nomes) == len(set(nomes))


def test_todo_cenario_declara_data_e_fonte():
    for c in S.SCENARIOS:
        assert c.data_ref, c.nome
        assert c.fonte, c.nome
        assert c.retorno_indice_observado is not None, c.nome


def test_os_cenarios_nao_sao_todos_o_mesmo_evento():
    """Onze repetições de "bolsa cai, dólar sobe" seriam um cenário só.

    O conjunto precisa cobrir mecanismos diferentes -- choque cambial puro,
    choque externo com bolsa local resistindo, pregão único -- senão o teste
    de estresse mede a mesma coisa onze vezes e a carteira que passa nele
    está protegida contra um evento, não contra onze.
    """
    quedas = [c.shock_stock_br for c in S.SCENARIOS]
    assert any(q > 0 for q in quedas), "nenhum cenário com bolsa BR em alta"
    cambios = [c.cambio_usd_brl for c in S.SCENARIOS]
    assert any(x < 0 for x in cambios), "nenhum cenário com dólar em queda"
    assert max(cambios) > 0.5, "nenhum choque cambial de grande magnitude"
    prazos = {c.tempo_recuperacao_meses for c in S.SCENARIOS
              if c.tempo_recuperacao_meses is not None}
    assert len(prazos) >= 8, "prazos de recuperação pouco variados"


def test_o_cenario_assimetrico_de_2022_existe_e_e_assimetrico():
    c = next(x for x in S.SCENARIOS if "2022" in x.nome)
    assert c.shock_stock_br > 0 and c.shock_etf_intl < 0
    assert c.cambio_usd_brl < 0


# -- A conferência -----------------------------------------------------------
def test_todos_os_cenarios_reproduzem_o_observado():
    d = S.diagnostico_cenarios()
    assert d["declarados"] == d["reproduzidos"] == len(S.SCENARIOS)
    assert d["reprovados"] == 0 and d["nao_conferidos"] == 0


def test_cenario_com_choque_adulterado_reprova():
    """A conferência precisa poder dar errado -- senão não é conferência."""
    mau = dataclasses.replace(S.SCENARIOS[0], shock_stock_br=-0.50)
    ok, motivo = S.conferir_cenario(mau)
    assert ok is False
    assert "-0.5000" in motivo and "-0.4100" in motivo


def test_cenario_sem_observado_fica_nao_conferido_e_nao_reprovado():
    """``None``, nunca ``False``: ninguém conferiu, e isso não é reprovação."""
    sem = dataclasses.replace(S.SCENARIOS[0], retorno_indice_observado=None)
    assert S.conferir_cenario(sem)[0] is None
    sem_fonte = dataclasses.replace(S.SCENARIOS[0], fonte="")
    assert S.conferir_cenario(sem_fonte)[0] is None


def test_cenario_nao_conferido_nao_entra_na_contagem():
    ok = S.conferir_cenario(
        dataclasses.replace(S.SCENARIOS[0], retorno_indice_observado=None))[0]
    assert ok is not True  # e portanto não soma em cenarios_reproduzidos


def test_a_contagem_nao_e_o_tamanho_da_lista():
    """Se fosse ``len(SCENARIOS)``, adulterar um cenário não mudaria nada."""
    fonte = S.cenarios_reproduzidos.__doc__ or ""
    assert "len(SCENARIOS)" in fonte
    originais = S.SCENARIOS
    try:
        S.SCENARIOS = [dataclasses.replace(originais[0], shock_stock_br=-0.99),
                       *originais[1:]]
        historicos = sum(1 for c in originais if not c.em_curso)
        assert S.cenarios_reproduzidos() == historicos - 1
    finally:
        S.SCENARIOS = originais


def test_a_conferencia_passa_pelo_caminho_de_codigo_real():
    """Classe fora do mapa de choques apareceria aqui."""
    r = S.aplicar_stress(S.CARTEIRA_CANONICA, S.SCENARIOS[0])
    assert r["por_classe"]["Ações BR"]["pre"] == 100_000.0
    assert abs(r["perda_pct"] - S.SCENARIOS[0].shock_stock_br) < 1e-9


# -- O medidor da homologação ------------------------------------------------
def test_o_criterio_deixa_de_ser_eternamente_nao_medido():
    medidas = M.medir()
    assert medidas["cenarios_historicos_reproduzidos"] == 11.0
    av = C.avaliar(3, medidas)
    assert "cenarios_historicos_reproduzidos" in av.atendidos


def test_os_dois_criterios_de_operacao_real_continuam_sem_medidor():
    """Medidor que devolvesse 0,0 por não achar nada aprovaria o critério.

    Ambos são "menor melhor": zero passa. Zero por não ter havido operação é
    exatamente aprovar por não ter testado, e é o defeito que este teste
    impede de reaparecer.
    """
    for nome in ("falsos_positivos_nivel_3_ou_4", "tempo_ate_rebaixar_nivel_h"):
        assert nome not in M.COBERTOS
        assert nome in M.SEM_MEDIDOR
        assert "não medido" in M.situacao(nome)


def test_a_fase_4_continua_bloqueada_por_medicao_ausente():
    av = C.avaliar(3, M.medir())
    assert not av.pode_avancar
    assert set(av.nao_medidos) == {"falsos_positivos_nivel_3_ou_4",
                                   "tempo_ate_rebaixar_nivel_h"}
    assert not av.reprovados


def test_medidor_que_explode_vira_nao_medido_e_nao_zero(monkeypatch):
    def explode():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(M, "MEDIDORES",
                        {"cenarios_historicos_reproduzidos": explode})
    assert M.medir()["cenarios_historicos_reproduzidos"] is None


def test_todo_criterio_da_fase_4_tem_medidor_ou_motivo_declarado():
    for c in C.EXIGIDO[4]:
        assert c.nome in M.COBERTOS or c.nome in M.SEM_MEDIDOR, c.nome


# -- Evento em curso -----------------------------------------------------------
def _eleitoral():
    return next(c for c in S.SCENARIOS if c.nome == "Choque Eleitoral 2026")


def test_o_choque_eleitoral_de_2026_esta_cadastrado_como_em_curso():
    c = _eleitoral()
    assert c.em_curso
    assert c.tempo_recuperacao_meses is None
    assert c.shock_stock_br == c.retorno_indice_observado == -0.18
    assert c.cambio_usd_brl == 0.09 and c.shock_fii == -0.09
    assert S.conferir_cenario(c)[0] is True


def test_evento_em_curso_nao_afirma_recuperacao():
    with __import__("pytest").raises(ValueError, match="em curso"):
        dataclasses.replace(_eleitoral(), tempo_recuperacao_meses=6)


def test_evento_encerrado_precisa_de_recuperacao():
    with __import__("pytest").raises(ValueError, match="encerrado"):
        dataclasses.replace(S.SCENARIOS[0], tempo_recuperacao_meses=None)


def test_choque_nao_medido_precisa_ser_um_choque_que_existe():
    pytest = __import__("pytest")
    with pytest.raises(ValueError, match="desconhecido"):
        dataclasses.replace(_eleitoral(), choques_nao_medidos=("shock_xyz",))
    with pytest.raises(ValueError, match="conferido"):
        dataclasses.replace(_eleitoral(), choques_nao_medidos=("shock_stock_br",))


def test_cenario_historico_nao_tem_choque_proxy():
    for c in S.SCENARIOS:
        if not c.em_curso:
            assert c.choques_nao_medidos == (), c.nome
            assert isinstance(c.tempo_recuperacao_meses, int), c.nome


def test_evento_em_curso_nao_conta_como_cenario_historico_reproduzido():
    """O critério é de cenários históricos; janela aberta não reproduz nada."""
    historicos = [c for c in S.SCENARIOS if not c.em_curso]
    assert S.cenarios_reproduzidos() == len(historicos) == 11


def test_aplicar_stress_nomeia_a_classe_que_recebeu_choque_nao_medido():
    carteira = [
        {"classe": "Ações BR", "valor_mercado": 1000.0},
        {"classe": "Fundo RF", "valor_mercado": 500.0},
    ]
    r = S.aplicar_stress(carteira, _eleitoral())
    assert r["em_curso"] is True
    assert r["tempo_recuperacao_meses"] is None
    assert r["choque_nao_medido"] == {"Fundos de renda fixa": 500.0}
    # Cenário histórico não marca nada.
    h = S.aplicar_stress(carteira, S.SCENARIOS[0])
    assert h["em_curso"] is False and h["choque_nao_medido"] == {}


def test_pior_caso_funciona_com_recuperacao_nao_medida():
    """Se o evento em curso for o pior, a recuperação sai ``None``, nunca 0."""
    carteira = [{"classe": "Ações BR", "valor_mercado": 1000.0}]
    originais = S.SCENARIOS
    try:
        S.SCENARIOS = [_eleitoral(), originais[2]]   # Joesley: -9%
        pior = S.cenario_pior_caso(carteira)
        assert pior["cenario"] == "Choque Eleitoral 2026"
        assert pior["tempo_recuperacao_meses"] is None
    finally:
        S.SCENARIOS = originais
