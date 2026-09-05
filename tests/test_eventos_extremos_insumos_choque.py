"""O indice de antifragilidade passa a ser publicavel em producao (A-143).

Por que este arquivo existe
---------------------------
A revisao de 02/09 mediu a mesma carteira de dois jeitos: ``calcular(pos)`` --
como a tela fazia -- devolvia ``None`` com 59% de cobertura, e a mesma chamada
com os insumos de choque devolvia um numero com 86%. O ``None`` nao era defeito
do motor: ele se recusa a publicar sem o nucleo de resistencia a choque, e a
recusa esta certa. O que faltava era a entrada.

O que se cobra aqui:

1. **Ausencia e ``None`` com motivo, nunca ``0.0``.** Em perda simulada,
   ``0.0`` afirma "esta carteira nao perde nada no pior cenario historico" --
   a conclusao mais forte possivel, publicada justamente quando nao se mediu.
2. **Classe fora do mapa nao recebe choque errado em silencio.**
3. **Correlacao curta demais nao vira correlacao da serie inteira.**
4. **A tela chama o motor com os insumos**, verificado por mutacao.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.eventos_extremos import antifragilidade as af
from core.eventos_extremos import insumos_choque as ic


def _posicoes(classes=("b3", "b3", "us", "fii")) -> pd.DataFrame:
    simbolos = ("PETR4", "VALE3", "AAPL", "HGLG11")
    setores = ("Energia", "Materiais", "Tecnologia", "Logistica")
    paises = ("BR", "BR", "US", "BR")
    moedas = ("BRL", "BRL", "USD", "BRL")
    pesos = (0.30, 0.25, 0.25, 0.20)
    return pd.DataFrame([
        {"asset_class": c, "symbol": s, "sector": st, "country": p,
         "currency": m, "weight_global": w, "valor_brl": w * 100_000.0}
        for c, s, st, p, m, w in zip(classes, simbolos, setores, paises,
                                     moedas, pesos)])


# ─────────────────────────────── perda simulada ──────────────────────────────

def test_a_perda_e_publicada_como_fracao_positiva():
    """``perda_pct`` sai negativa do motor de stress; a antifragilidade quer
    a perda positiva. A inversao acontece num lugar so, e este teste e o lugar.
    """
    perda, limitacoes = ic.perda_simulada(_posicoes())

    assert perda is not None
    assert 0.0 < perda < 1.0, f"perda fora de faixa plausivel: {perda}"
    assert not limitacoes


def test_carteira_vazia_nao_vira_perda_zero():
    """Zero seria a afirmacao mais forte do modulo, feita sem medir nada."""
    perda, limitacoes = ic.perda_simulada(pd.DataFrame())

    assert perda is None, "carteira vazia publicou perda"
    assert limitacoes


def test_classe_fora_do_mapa_nao_recebe_o_choque_de_acao_brasileira():
    """O defeito silencioso que o mapa explicito tranca.

    ``stress_tests.aplicar_stress`` faz ``_CLASSE_TO_SHOCK.get(classe,
    "shock_stock_br")``: classe desconhecida nao levanta erro, recebe o choque
    de acao da B3. Uma carteira inteira de outra classe atravessaria como acao
    brasileira e a perda sairia errada com cara de perda certa.
    """
    exotica = _posicoes(classes=("b3", "b3", "cripto_exotica", "fii"))
    perda, limitacoes = ic.perda_simulada(exotica)

    assert perda is not None, "as classes conhecidas ainda tem de ser medidas"
    assert any("cripto_exotica" in m for m in limitacoes), (
        "classe desconhecida entrou na conta sem aparecer nas limitacoes")


def test_o_mapa_de_classes_cobre_o_registro_canonico():
    """Classe nova no registro sem entrada aqui sai da perda em silencio.

    Este teste falha no dia em que alguem registrar uma quarta classe de ativo
    -- que e o dia certo para descobrir, e nao meses depois pela perda baixa
    demais.
    """
    from core.portfolio.registry import asset_classes

    faltando = set(asset_classes()) - set(ic.CLASSE_PARA_STRESS)
    assert not faltando, (
        f"classes do registro sem mapa de choque: {sorted(faltando)}")


def test_sem_valor_em_reais_a_perda_relativa_ainda_e_apuravel():
    """Perda percentual e relativa: os pesos bastam."""
    sem_valor = _posicoes().drop(columns=["valor_brl"])
    perda, _ = ic.perda_simulada(sem_valor)
    com_valor, _ = ic.perda_simulada(_posicoes())

    assert perda is not None
    assert abs(perda - com_valor) < 1e-9, (
        "a perda relativa mudou conforme a carteira tinha ou nao valor "
        "absoluto -- ela nao deveria depender do tamanho")


# ───────────────────────────── correlacao de estresse ────────────────────────

def _retornos(meses: int, ativos: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        rng.normal(0.0, 0.05, size=(meses, ativos)),
        index=pd.date_range("2024-01-31", periods=meses, freq="ME"),
        columns=[f"A{i}" for i in range(ativos)])


def test_o_piso_da_janela_e_o_da_fonte_que_calcula():
    """O segundo "gate que so podia dar False" desta sessao, agora trancado.

    ``correlation.matriz`` devolve ``NaN`` em par com menos de
    ``MIN_OBS_CORRELACAO`` observacoes, e a media de matriz toda ``NaN`` e
    ``None``. Com a janela em 12 e o piso de la em 18, a correlacao saia "nao
    medida" em toda execucao -- sem erro, sem log, para sempre. O piso daqui e
    importado de la justamente para nao poder divergir.
    """
    from core.global_portfolio.correlation import MIN_OBS_CORRELACAO

    assert ic.MIN_MESES_CORRELACAO == MIN_OBS_CORRELACAO
    assert ic.JANELA_ESTRESSE_MESES >= ic.MIN_MESES_CORRELACAO

    valor, _ = ic.correlacao_estresse(_retornos(ic.JANELA_ESTRESSE_MESES))
    assert valor is not None, (
        "com exatamente a janela declarada a correlacao nao saiu: o piso e "
        "inalcancavel na pratica")


def test_a_correlacao_sai_da_janela_recente_e_nao_da_serie_inteira():
    """Correlacao de longo prazo nao e correlacao sob estresse.

    Publicar uma no lugar da outra responderia a pergunta errada com numero
    plausivel -- e o numero entra num indice que a tela mostra.
    """
    longa = _retornos(120)
    valor, limitacoes = ic.correlacao_estresse(longa)
    inteira, _ = ic.correlacao_estresse(longa, janela=120)

    assert valor is not None and not limitacoes
    assert -1.0 <= valor <= 1.0
    assert valor != inteira, (
        "a janela de estresse devolveu o mesmo numero da serie inteira: "
        "ou a janela nao esta sendo aplicada, ou o cenario e degenerado")


def test_janela_curta_demais_nao_e_completada_pela_serie_inteira():
    """O modo de falha e o fallback que so preenche lacuna e nunca contradiz."""
    valor, limitacoes = ic.correlacao_estresse(_retornos(60), janela=4)

    assert valor is None
    assert any("NAO foi substituida" in m for m in limitacoes)


def test_um_ativo_so_nao_tem_correlacao_definida():
    valor, limitacoes = ic.correlacao_estresse(_retornos(24, ativos=1))

    assert valor is None
    assert limitacoes


def test_sem_retornos_a_ausencia_e_declarada():
    valor, limitacoes = ic.correlacao_estresse(None)

    assert valor is None
    assert limitacoes


# ──────────────────────────── qualidade de credito ───────────────────────────

def test_qualidade_de_credito_e_ausencia_declarada_e_nao_proxy():
    """Nao ha rating em nenhuma classe; derivar de proxy publicaria um numero
    que ninguem apurou. Ela nao esta no nucleo, entao a ausencia nao bloqueia.
    """
    insumos = ic.medir(_posicoes())

    assert insumos.qualidade_credito is None
    assert any("sem fonte" in m for m in insumos.limitacoes)
    assert af.C_CREDITO not in af.NUCLEO, (
        "qualidade de credito entrou no nucleo: sem fonte, o indice volta a "
        "ser estruturalmente impublicavel")


# ─────────────────────── o efeito medido no indice ───────────────────────────

def test_com_os_insumos_o_indice_deixa_de_ser_none():
    """A medicao que fecha A-143, refeita como teste.

    Antes: ``None`` com 59% de cobertura. Depois: numero publicado, cobertura
    maior. O numero em si nao e cravado aqui -- ele depende dos choques
    calibrados, que sao outro assunto -- mas a transicao de ``None`` para
    numero e o que o achado pedia.
    """
    posicoes = _posicoes()
    antes = af.calcular(posicoes)
    insumos = ic.medir(posicoes)
    depois = af.calcular(posicoes, **insumos.como_kwargs())

    assert antes.valor is None, (
        "cenario invalido: o indice ja saia publicado sem insumo nenhum")
    assert depois.valor is not None, (
        "os insumos de choque nao chegaram ao motor: o indice segue None")
    assert depois.cobertura > antes.cobertura


def test_medir_nunca_levanta_com_entrada_degenerada():
    """A tela tem de abrir sem carteira, sem preco e sem motor de stress."""
    for entrada in (None, pd.DataFrame(), _posicoes().drop(columns=["asset_class"])):
        insumos = ic.medir(entrada)
        assert insumos.perda_simulada is None
        assert insumos.limitacoes


# ──────────────────────────────── a fiacao ───────────────────────────────────

def test_a_tela_chama_o_motor_com_os_insumos():
    """Sem isto, o motor volta a receber so ``posicoes`` e o indice some.

    E a regressao invisivel: nenhuma excecao, nenhum log, so um painel que
    volta a dizer "nao calculado" para sempre.
    """
    import inspect

    from views import inteligencia_mercado as view

    fonte = inspect.getsource(view.montar_tudo)
    assert "insumos_choque" in fonte
    assert "como_kwargs()" in fonte


def test_os_motivos_das_ausencias_chegam_a_tela():
    """Componente ausente sem motivo visivel foi a origem do achado."""
    import inspect

    from views import inteligencia_mercado as view

    fonte = inspect.getsource(view.montar_tudo)
    assert "lim_choque" in fonte
    assert "extras: list[str] = list(lim_choque)" in fonte
