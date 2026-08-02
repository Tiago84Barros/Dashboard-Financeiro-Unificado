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
    # A helper em rede não existe mais; trava a reintrodução no arquivo inteiro
    # (o comentário-lápide cita o nome, por isso o teste olha a definição).
    assert "def _batch_yf_liquidez" not in avancada


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


def test_avancada_avisa_classe_irma_mais_liquida():
    """A Avançada não troca de classe — mas não pode OMITIR que existe melhor.

    O filtro de giro aprova BRAP3 sem dizer que BRAP4 negocia 72× mais com a
    mesma exposição econômica, e quem estuda a empresa aqui acaba comprando o
    papel errado lá fora.
    """
    from unittest.mock import patch

    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
from unittest.mock import patch
import views.empresas_b3 as v
with patch.object(v._db, 'load_giro_diario',
                  return_value={'BRAP3': 649_000.0, 'BRAP4': 46_819_000.0,
                                'WEGE3': 358_068_000.0}), \
     patch.object(v._db, 'load_classes_irmas',
                  return_value={'BRAP3': ('BRAP3', 'BRAP4'),
                                'BRAP4': ('BRAP3', 'BRAP4'),
                                'WEGE3': ('WEGE3',)}):
    v._render_classe_mais_liquida(['BRAP3', 'WEGE3'])
""").run(timeout=60)
    assert not app.exception
    tabela = app.dataframe[0].value
    assert list(tabela["No ranking"]) == ["BRAP3"]      # WEGE3 não tem irmã melhor
    assert list(tabela["Mais negociada"]) == ["BRAP4"]


def test_avancada_nao_mostra_secao_quando_nao_ha_o_que_avisar():
    """Seção que aparece sempre vira ruído — só surge quando há achado."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
from unittest.mock import patch
import views.empresas_b3 as v
with patch.object(v._db, 'load_giro_diario', return_value={'WEGE3': 3.5e8}), \
     patch.object(v._db, 'load_classes_irmas', return_value={'WEGE3': ('WEGE3',)}):
    v._render_classe_mais_liquida(['WEGE3'])
""").run(timeout=60)
    assert not app.exception
    assert not app.dataframe


def test_avancada_expoe_dependencia_de_governo_e_resiliencia():
    """Duas dimensões que nenhum indicador contábil da tela mede."""
    import pandas as pd
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
from unittest.mock import patch
import views.empresas_b3 as v
setores = pd.DataFrame([
    {'ticker': 'PETR4', 'SETOR': 'Petróleo, Gás e Biocombustíveis'},
    {'ticker': 'SBSP3', 'SETOR': 'Utilidade Pública'},
    {'ticker': 'WEGE3', 'SETOR': 'Bens Industriais'},
])
with patch.object(v._db, 'load_resiliencia_ciclo', return_value={
        'PETR4': {'razao': 0.27, 'margem_normal': 0.21, 'margem_crise': 0.06, 'anos': 3},
        'WEGE3': {'razao': 0.85, 'margem_normal': 0.14, 'margem_crise': 0.12, 'anos': 3}}):
    v._render_governo_e_resiliencia(['PETR4', 'SBSP3', 'WEGE3'], setores)
""").run(timeout=60)
    assert not app.exception
    t = app.dataframe[0].value.set_index("Ticker")
    # Controle estatal tem precedência sobre o setor.
    assert t.at["PETR4", "Decisão de governo"] == "controle estatal"
    assert t.at["SBSP3", "Decisão de governo"] == "tarifa regulada"
    # WEGE3 entra por ter resiliência medida, mesmo sem dependência de governo.
    assert t.at["WEGE3", "Decisão de governo"] == "—"
    assert "0.85" in str(t.at["WEGE3", "Resiliência em recessão"])
