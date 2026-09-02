"""Backtest ponto-no-tempo e calibração dos pesos.

O requisito pede backtest e calibração e proíbe pesos arbitrários definitivos.
Os dois primeiros blocos aqui existem para provar que o backtest não é o
autoengano de sempre -- estimar um evento com uma amostra que já o contém
mede o quanto a mediana descreve os dados que a produziram, o que é sempre
excelente e nunca significa nada.

O terceiro bloco cobre a outra metade: enquanto não houver base, `calibrar`
devolve o prior com ``calibrado=False``, e esse ``False`` precisa continuar
saindo escrito em vez de virar um peso com cara de medido.
"""
from __future__ import annotations

from functools import lru_cache

from core.memoria_mercado import calibracao as cal
from core.memoria_mercado import estimativa as est
from core.memoria_mercado import similaridade as sim
from core.noticias.impacto import CONFIANCA_MEDIA
from core.noticias.taxonomia import DIRECAO_ALTA, DIRECAO_BAIXA
from tests.apoio_memoria import cenario, painel


@lru_cache(maxsize=None)
def eventos(n: int = 40) -> tuple:
    """Painel medido, em cache: cada chamada de `painel` mede n eventos."""
    return tuple(painel(n, reacao=-0.06, dispersao=0.04))


def caso(i: int, *, central: float, realizado: float,
         faixa: tuple[float, float] | None = None,
         mediana: float | None = None, publicavel: bool = True,
         dimensoes: dict | None = None) -> cal.CasoBacktest:
    """Caso de backtest montado à mão, para exercitar `avaliar` sem depender
    de qual amostra o walk-forward produziria."""
    if faixa is None and publicavel:
        faixa = (central - 0.02, central + 0.02)
    e = est.Estimativa(
        tipo_evento="resultado", simbolo=f"ATV{i:02d}", faixa=faixa,
        valor_central=(central if publicavel else None),
        horizonte=((5, 20) if publicavel else None), horizonte_base=20,
        direcao=(DIRECAO_BAIXA if central < 0 else DIRECAO_ALTA),
        n_amostra=30, similaridade=80.0, confianca=CONFIANCA_MEDIA,
        experimental=False, publicavel=publicavel,
        mediana_historica=(central if mediana is None else mediana),
    )
    return cal.CasoBacktest(chave=f"k{i:02d}", simbolo=e.simbolo, horizonte=20,
                            estimativa=e, realizado=realizado,
                            dimensoes=dict(dimensoes or {}))


# ── o walk-forward é ponto-no-tempo ──────────────────────────────────────────

def test_cada_caso_e_estimado_so_com_os_eventos_anteriores_a_ele():
    """A propriedade que separa backtest de autoengano: o evento sob teste
    nunca está na amostra que o estima."""
    casos = cal.walk_forward(eventos(), tipo_evento="resultado", horizonte=20)
    assert casos

    ordenados = sorted(eventos(), key=lambda e: (e.data_evento, e.chave))
    posicao = {e.chave: i for i, e in enumerate(ordenados)}
    for c in casos:
        # A amostra tem exatamente os eventos que vieram antes dele.
        assert c.estimativa.n_amostra == posicao[c.chave]
        assert c.estimativa.n_amostra >= cal.N_MINIMO_TREINO


def test_os_primeiros_eventos_nao_viram_caso_por_falta_de_treino():
    casos = cal.walk_forward(eventos(), tipo_evento="resultado", horizonte=20,
                             minimo_treino=25)
    assert len(casos) == len(eventos()) - 25
    assert all(c.estimativa.n_amostra >= 25 for c in casos)


def test_walk_forward_e_deterministico_na_ordem_de_entrada():
    a = cal.walk_forward(eventos(), tipo_evento="resultado", horizonte=20)
    b = cal.walk_forward(tuple(reversed(eventos())), tipo_evento="resultado",
                         horizonte=20)
    assert [c.chave for c in a] == [c.chave for c in b]
    assert [c.estimativa.valor_central for c in a] == [
        c.estimativa.valor_central for c in b]


def test_horizonte_nao_medido_nao_entra_no_backtest():
    """`memoria: foto-truncada-vira-evidencia`: evento sem 60 pregões de futuro
    é um evento não medido, não uma reação nula em 60 pregões."""
    casos = cal.walk_forward(eventos(), tipo_evento="resultado", horizonte=60)
    medidos = [e for e in eventos() if e.janelas[60].medida]
    assert len(casos) == max(0, len(medidos) - cal.N_MINIMO_TREINO)


def test_sem_eventos_o_backtest_e_vazio_e_nao_falha():
    assert cal.walk_forward([], tipo_evento="resultado", horizonte=20) == []
    r = cal.avaliar([])
    assert r.n == 0 and not r.suficiente
    assert r.mae is None and r.cobertura_faixa is None
    assert any("backtest vazio" in x for x in r.limitacoes)


def test_cenarios_entram_no_walk_forward_e_a_similaridade_e_medida():
    cenarios = {e.chave: cenario(juros_br=13.0 - i * 0.2)
                for i, e in enumerate(sorted(eventos(),
                                             key=lambda x: x.data_evento))}
    casos = cal.walk_forward(eventos(), tipo_evento="resultado", horizonte=20,
                             cenarios=cenarios)
    assert casos
    assert all(c.dimensoes for c in casos)
    assert all(c.estimativa.similaridade is not None for c in casos)

    # Sem cenários o caminho é o de similaridade neutra, e ele é diferente.
    sem = cal.walk_forward(eventos(), tipo_evento="resultado", horizonte=20)
    assert all(c.dimensoes == {} for c in sem)
    assert all(c.estimativa.similaridade is None for c in sem)


# ── as quatro medidas ─────────────────────────────────────────────────────────

def test_backtest_completo_sai_com_as_quatro_medidas_e_o_tamanho():
    r = cal.avaliar(cal.walk_forward(eventos(), tipo_evento="resultado",
                                     horizonte=20))
    assert r.n > 0
    assert 0.0 <= r.cobertura_faixa <= 1.0
    assert 0.0 <= r.acerto_direcional <= 1.0
    assert r.mae > 0 and r.mae_referencia > 0
    assert r.vies is not None
    assert "cobertura da faixa" in r.texto()


def test_poucos_casos_nao_produzem_conclusao():
    r = cal.avaliar([caso(i, central=-0.06, realizado=-0.05) for i in range(6)])
    assert r.n == 6
    assert not r.suficiente
    assert any(f"abaixo do minimo de {cal.N_MINIMO_BACKTEST}" in x
               for x in r.limitacoes)
    assert "sem conclusao" in r.texto()


def test_caso_sem_faixa_sai_das_medidas_e_e_contado_a_parte():
    """`memoria: medicao-que-pune-a-evidencia`: um caso sem estimativa não é um
    erro de zero -- ele não é um caso."""
    casos = ([caso(i, central=-0.06, realizado=-0.06) for i in range(20)]
             + [caso(90 + i, central=0.0, realizado=-0.30, publicavel=False)
                for i in range(5)])
    r = cal.avaliar(casos)
    assert r.n == 20
    assert r.n_sem_estimativa == 5
    assert r.mae == 0.0        # os 5 descartados não contaminaram o erro
    assert any("nao geraram faixa" in x for x in r.limitacoes)


def test_faixa_larga_demais_e_denunciada_mesmo_acertando_sempre():
    """Cobertura de 100% não é qualidade: é uma faixa que não decide nada."""
    casos = [caso(i, central=-0.06, realizado=-0.06, faixa=(-0.50, 0.50))
             for i in range(24)]
    r = cal.avaliar(casos)
    assert r.cobertura_faixa == 1.0
    assert any("larga demais" in x for x in r.limitacoes)


def test_faixa_estreita_demais_e_denunciada_como_precisao_falsa():
    casos = [caso(i, central=-0.06, realizado=-0.06 + 0.10 * (1 if i % 2 else -1),
                  faixa=(-0.061, -0.059)) for i in range(24)]
    r = cal.avaliar(casos)
    assert r.cobertura_faixa == 0.0
    assert any("precisao falsa" in x for x in r.limitacoes)


def test_ganho_sobre_a_referencia_mede_o_ajuste_contra_nao_ajustar_nada():
    """`memoria: diagnostico-precisa-porta-de-entrada`: mecanismo de ajuste que
    não é medido contra a alternativa de não ajustar é decoração."""
    # O central acerta em cheio; a mediana histórica crua erra 4 pontos.
    bons = [caso(i, central=-0.06, realizado=-0.06, mediana=-0.10)
            for i in range(24)]
    r = cal.avaliar(bons)
    assert r.mae == 0.0
    assert abs(r.mae_referencia - 0.04) < 1e-9
    assert r.ganho_sobre_referencia == 1.0

    # E o sinal aparece invertido quando o ajuste piora a estimativa.
    ruins = [caso(i, central=-0.20, realizado=-0.06, mediana=-0.07)
             for i in range(24)]
    assert cal.avaliar(ruins).ganho_sobre_referencia < 0
    assert "-" in cal.avaliar(ruins).texto()


def test_vies_separa_erro_sistematico_de_erro_absoluto():
    """Erros que se cancelam dão MAE alto e viés zero; erros para o mesmo lado
    dão os dois altos. Publicar só o MAE esconderia a diferença."""
    alternado = [caso(i, central=0.0, realizado=(0.05 if i % 2 else -0.05))
                 for i in range(24)]
    r = cal.avaliar(alternado)
    assert abs(r.mae - 0.05) < 1e-9
    assert abs(r.vies) < 1e-9

    torto = [caso(i, central=0.0, realizado=-0.05) for i in range(24)]
    assert abs(cal.avaliar(torto).vies - 0.05) < 1e-9


def test_acerto_direcional_ignora_o_que_nao_tem_direcao():
    casos = ([caso(i, central=-0.06, realizado=-0.05) for i in range(10)]
             + [caso(50 + i, central=0.0, realizado=-0.05) for i in range(10)])
    r = cal.avaliar(casos)
    assert r.acerto_direcional == 1.0     # os 10 sem direção saíram da conta


# ── calibração dos pesos ──────────────────────────────────────────────────────

def test_sem_base_o_prior_e_devolvido_declarado_como_nao_calibrado():
    """Este é o caminho normal hoje, e precisa continuar sendo dito em voz
    alta em vez de virar peso com aparência de medido."""
    p = cal.calibrar_pesos_similaridade(
        [caso(i, central=-0.06, realizado=-0.05) for i in range(5)])
    assert p.pesos == sim.PESOS_PRIOR
    assert not p.calibrado
    assert p.n == 5
    assert p.encolhimento is None
    assert any(f"abaixo do minimo de {cal.N_MINIMO_BACKTEST}" in x
               for x in p.limitacoes)


def test_dimensao_que_nao_reduz_o_erro_nao_ganha_peso():
    """Hipótese falsificável: se a dimensão mede algo, mais similaridade nela
    deveria significar menos erro. Sem isso, o prior fica."""
    # Similaridade alta acompanhando erro ALTO: correlação negativa em todas.
    casos = [caso(i, central=-0.06, realizado=-0.06 - 0.01 * i,
                  dimensoes=dict.fromkeys(sim.DIMENSOES, i / 24.0))
             for i in range(24)]
    p = cal.calibrar_pesos_similaridade(casos)
    assert not p.calibrado
    assert p.pesos == sim.PESOS_PRIOR
    assert p.correlacoes and all(v <= 0 for v in p.correlacoes.values())
    assert any("evidencia contra o proprio fator" in x for x in p.limitacoes)


def _casos_com_dimensao_util(n: int = 24) -> list[cal.CasoBacktest]:
    """`DIM_JUROS_BR` antecipa o erro; as outras ficam constantes (e por isso
    sem correlação definida)."""
    saida = []
    for i in range(n):
        s = i / (n - 1.0)                       # 0 -> 1, similaridade crescente
        erro = 0.10 * (1.0 - s)                 # ... e erro decrescente
        dims = dict.fromkeys(sim.DIMENSOES, 0.5)
        dims[sim.DIM_JUROS_BR] = s
        saida.append(caso(i, central=-0.06, realizado=-0.06 - erro,
                          dimensoes=dims))
    return saida


def test_dimensao_que_antecipa_o_erro_concentra_o_peso():
    p = cal.calibrar_pesos_similaridade(_casos_com_dimensao_util())
    assert p.calibrado
    assert p.correlacoes[sim.DIM_JUROS_BR] > 0.9
    assert p.pesos[sim.DIM_JUROS_BR] > sim.PESOS_PRIOR[sim.DIM_JUROS_BR]
    # Somam 1 a menos do arredondamento de 6 casas em 15 dimensões. A soma não
    # precisa ser exata: `similaridade.calcular` renormaliza pelo peso medido.
    assert abs(sum(p.pesos.values()) - 1.0) < 1e-4
    assert set(p.pesos) == set(sim.PESOS_PRIOR)


def test_o_encolhimento_impede_que_poucos_casos_reescrevam_os_pesos():
    casos = _casos_com_dimensao_util()
    p = cal.calibrar_pesos_similaridade(casos)
    assert abs(p.encolhimento - 24 / (24 + cal.N_ENCOLHIMENTO)) < 1e-4
    assert p.encolhimento < 0.5      # com 24 casos o prior ainda pesa mais

    # Encolhimento maior puxa o resultado de volta para o prior.
    conservador = cal.calibrar_pesos_similaridade(casos, encolhimento=300)
    distancia = lambda x: abs(x.pesos[sim.DIM_JUROS_BR]      # noqa: E731
                              - sim.PESOS_PRIOR[sim.DIM_JUROS_BR])
    assert distancia(conservador) < distancia(p)
    assert any("recalibrar quando a base crescer" in x
               for x in conservador.limitacoes)


def test_pesos_calibrados_voltam_para_a_similaridade_e_mudam_o_fator():
    """A calibração só vale se o resultado for utilizável onde o número é
    produzido -- senão é medição sem porta de entrada."""
    p = cal.calibrar_pesos_similaridade(_casos_com_dimensao_util())
    hoje = cenario(juros_br=6.0)
    prior = sim.calcular(hoje, cenario())
    novo = sim.calcular(hoje, cenario(), pesos=p.pesos,
                        pesos_calibrados=p.calibrado)
    assert novo.fator != prior.fator
    assert novo.pesos_calibrados
    assert not any("nao calibrados" in x for x in novo.limitacoes)
