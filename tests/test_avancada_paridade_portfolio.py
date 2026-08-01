"""Paridade entre a Análise Avançada e a Criação de Portfólio.

A Criação de Portfólio é extensão da Análise Avançada, mas foi ajustada
primeiro. Estes testes travam o que as duas telas precisam responder igual —
divergir aqui significa dar universos diferentes para a MESMA pergunta.
"""
import re
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]


def _fonte(nome: str) -> str:
    return (RAIZ / "views" / nome).read_text(encoding="utf-8")


def test_liquidez_vem_do_armazem_nas_duas_telas():
    """yfinance na análise e armazém no portfólio faziam as telas discordarem.

    Pior: a versão em rede derrubava a aba INTEIRA quando a fonte falhava
    ("nenhum ativo será aprovado"), que é o pior modo de falha possível para
    uma tela de análise.
    """
    avancada = _fonte("empresas_b3.py")
    trecho = avancada[avancada.index("Filtro de liquidez de negociação"):][:2000]
    assert "load_giro_diario" in trecho
    assert "_batch_yf_liquidez" not in trecho


def test_piso_de_liquidez_padrao_igual_ao_perfil_recomendado():
    """R$ 500 mil nas duas: piso é escolha de tamanho de posição, não de tela."""
    from core.b3_portfolio_presets import PRESETS, RECOMENDADO

    avancada = _fonte("empresas_b3.py")
    bloco = re.search(r"liq_opts = \{(.*?)\}.*?index=(\d+),\s*key=\"b3_av_liq_min\"",
                      avancada, re.S)
    assert bloco, "não achei o seletor de liquidez da Análise Avançada"
    opcoes = re.findall(r'"([^"]+)":', bloco.group(1))
    escolhida = opcoes[int(bloco.group(2))]

    def _reais(rotulo: str) -> float:
        n = float(re.search(r"([\d.,]+)", rotulo).group(1).replace(",", "."))
        if "mil" in rotulo:
            return n * 1e3
        if "milhão" in rotulo or "milhões" in rotulo or "mi" in rotulo:
            return n * 1e6
        return n

    assert _reais(escolhida) == _reais(PRESETS[RECOMENDADO].valores["pb3_min_adtv"])


def test_falha_de_liquidez_nao_esvazia_a_analise():
    """Sem leitura, o filtro é PULADO — não pode remover todo o universo."""
    avancada = _fonte("empresas_b3.py")
    trecho = avancada[avancada.index("Filtro de liquidez de negociação"):][:2500]
    # O bloco de exclusão precisa estar sob `else`, senão get() devolve None
    # para todos os tickers e a lista final fica vazia.
    assert "else:" in trecho
    assert "não aplicado" in trecho


def test_ranking_da_avancada_mostra_o_piso_de_qualidade():
    """117 de 427 empresas são críticas e podiam liderar sem qualquer sinal."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import views.empresas_b3 as v
mult = pd.DataFrame([
    {'Ticker':'AZUL4','Patrimonio_Negativo':1.0,'Liquidez_Corrente':0.5,
     'Margem_Operacional':0.05,'ROE':-0.3,'P_FCO':5.0,'Endividamento_Total':1.0},
    {'Ticker':'BOA3','Margem_Operacional':0.20,'ROE':0.18,'P_FCO':8.0,
     'Endividamento_Total':0.5,'Liquidez_Corrente':2.0},
])
v._render_saude_do_ranking(mult, pd.DataFrame({'Ticker': ['AZUL4', 'BOA3']}))
""").run(timeout=60)
    assert not app.exception
    assert any("AZUL4" in e.value for e in app.error)
    assert not any("BOA3" in e.value for e in app.error)


def test_secao_some_quando_o_topo_esta_limpo():
    """Alarme que aparece sempre treina o usuário a ignorar alarme."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import views.empresas_b3 as v
mult = pd.DataFrame([
    {'Ticker':'BOA3','Margem_Operacional':0.20,'ROE':0.18,'P_FCO':8.0,
     'Endividamento_Total':0.5,'Liquidez_Corrente':2.0},
])
v._render_saude_do_ranking(mult, pd.DataFrame({'Ticker': ['BOA3']}))
""").run(timeout=60)
    assert not app.exception
    assert not app.error
    assert not app.warning
