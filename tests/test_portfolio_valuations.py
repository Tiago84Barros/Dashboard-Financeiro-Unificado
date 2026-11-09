import math

import pytest

from core.portfolio_valuations import aggregate_valuations


def test_weighted_means_and_total_portfolio_coverage():
    positions = [{'ticker': 'A', 'valor_mercado': 100},
                 {'ticker': 'B', 'valor_mercado': 300},
                 {'ticker': 'TESOURO', 'valor_mercado': 600}]
    result = aggregate_valuations(positions, {'A': {'dy': 0., 'pl': 10},
                                            'B': {'dy': 8., 'pl': 20}})
    assert result['dy']['value'] == 6.
    assert result['pl']['value'] == 17.5
    assert result['dy']['coverage'] == .4
    assert result['pvp']['value'] is None


@pytest.mark.parametrize('invalid', [None, 'bad', math.nan, math.inf, -3, 0])
def test_invalid_multiple_not_zero_filled(invalid):
    result = aggregate_valuations([{'ticker': 'A', 'valor_mercado': 100}],
                                  {'A': {'pl': invalid}})
    assert result['pl']['value'] is None
    assert result['pl']['coverage'] == 0


def test_lots_share_fundamentals_but_count_ticker_once():
    result = aggregate_valuations([{'ticker': 'A', 'valor_mercado': 100},
                                  {'ticker': 'A', 'valor_mercado': 200},
                                  {'ticker': 'B', 'valor_mercado': None}],
                                 {'A': {'pvp': 2}, 'B': {'pvp': 8}})
    assert result['pvp']['value'] == 2
    assert result['pvp']['assets'] == 1


def test_empty_portfolio():
    assert aggregate_valuations([], {})['dy']['value'] is None


def test_panel_with_synthetic_data(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr('design.portfolio_valuations._load',
        lambda *args: ({'SYNTH3': {'dy': 8., 'pl': 12., 'pvp': 1.2}}, [], 'sintética'))
    app = AppTest.from_string('''
from design.portfolio_valuations import render_portfolio_valuations
render_portfolio_valuations([
    {'ticker': 'SYNTH3F', 'classe': 'Ações', 'valor_mercado': 200},
    {'ticker': 'TESOURO', 'classe': 'Tesouro Direto', 'valor_mercado': 800}])
''').run()
    assert not app.exception
    cards = [m.value for m in app.markdown if '<div style="background:' in m.value]
    assert len(cards) == 7
    assert [c for c in cards if '8.00%' in c] and [c for c in cards if '12.00x' in c]
    assert [c for c in cards if '1.20x' in c]
    # A cobertura saiu da tela (vai para o log de lacunas); o card sai num bloco
    # só: div aberta num `st.markdown` e fechada em outro já produziu moldura vazia.
    dy = next(c for c in cards if '8.00%' in c)
    assert 'do valor da carteira' not in dy
    assert dy.count('<div') == dy.count('</div>')


def test_class_failure_does_not_erase_other_source(monkeypatch):
    from core.portfolio_valuations import load_valuation_fundamentals

    def unavailable(*args):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr('core.data_reconciliacao.batch_fund_fmt', unavailable)
    monkeypatch.setattr('core.fundamentus.batch_fiis', lambda *args: {'FII11': {'dy': 9}})
    data, failures = load_valuation_fundamentals(('SYNTH3',), ('FII11',))
    assert data == {'FII11': {'dy': 9}}
    assert failures == ['Ações B3']


def test_carteira_sem_acoes_nem_fiis_nao_registra_lacuna(monkeypatch):
    from streamlit.testing.v1 import AppTest

    registros = []
    monkeypatch.setattr('design.lacunas.registrar_lacuna',
                        lambda *a, **k: registros.append(a))
    monkeypatch.setattr('design.portfolio_valuations._load',
                        lambda *a: (_ for _ in ()).throw(AssertionError('não deveria consultar')))
    app = AppTest.from_string('''
from design.portfolio_valuations import render_portfolio_valuations
render_portfolio_valuations([
    {'ticker': 'TESOURO', 'classe': 'Tesouro Direto', 'valor_mercado': 800}])
''').run()
    assert not app.exception
    assert any('Sem ações ou FIIs' in i.value for i in app.info)
    assert not [r for r in registros if 'valuation_sem_media' in str(r)]


def test_so_fiis_marca_multiplo_de_empresa_como_nao_aplicavel():
    from design.portfolio_valuations import _metricas_aplicaveis

    assert _metricas_aplicaveis(False, True) == {'dy', 'pvp'}
    assert 'ev_ebit' in _metricas_aplicaveis(True, False)
    assert _metricas_aplicaveis(False, False) == set()


# ── Vacância financeira vem da vitrine da Seleção de FIIs ──────────────────
# O Fundamentus publica só a "Vacância Média" dos FIIs, nunca a financeira; o
# painel registrava "Vacância financeira: 0 ativo(s)" todo dia embora a vitrine
# (market.fii_selection_inputs / fii_selection_snapshot_v2) trouxesse a medida
# lida dos relatórios gerenciais (lacuna 20371431).

def _vitrine_fii(linhas):
    import pandas as pd
    return pd.DataFrame(linhas)


def _fundamentus_sem_vacancia_financeira(monkeypatch):
    monkeypatch.setattr('core.fundamentus._scrape_fii_lista', lambda: {})
    monkeypatch.setattr('core.fundamentus._scrape_fii',
                        lambda t: {'vacancia_media': 2.0, 'vacancia_financ': None, 'dy': 9.})


def test_vacancia_financeira_vem_da_vitrine_em_pontos_percentuais(monkeypatch):
    from core.fundamentus import batch_fiis

    _fundamentus_sem_vacancia_financeira(monkeypatch)
    monkeypatch.setattr('core.market_read.load_fii_methodology_inputs', lambda **k: _vitrine_fii([
        {'ticker': 'FII11', 'vacancia_financeira': 0.026,
         'metric_metadata': {'vacancia_financeira': {
             'reference_date': '2026-04-30', 'source': 'public_fii_report_provisional_v1'}}},
        {'ticker': 'OUTRO11', 'vacancia_financeira': None, 'metric_metadata': {}},
    ]))
    dados = batch_fiis(('FII11', 'OUTRO11'))
    # fração da vitrine (0.026) vira 2,6 %, a mesma unidade do Fundamentus
    assert dados['FII11']['vacancia_financ'] == pytest.approx(2.6)
    assert dados['FII11']['vacancia_financ_fonte'] == 'public_fii_report_provisional_v1'
    assert dados['FII11']['vacancia_financ_ref'] == '2026-04-30'
    # o que o Fundamentus trouxe não é sobrescrito
    assert dados['FII11']['vacancia_media'] == 2.0
    # fundo sem a medida na vitrine continua sem dado: ausência não vira zero
    assert dados['OUTRO11']['vacancia_financ'] is None
    assert 'vacancia_financ_fonte' not in dados['OUTRO11']

    media = aggregate_valuations([{'ticker': 'FII11', 'valor_mercado': 100},
                                  {'ticker': 'OUTRO11', 'valor_mercado': 100}],
                                 dados, ('vacancia_financ',))['vacancia_financ']
    assert media['value'] == pytest.approx(2.6)
    assert media['assets'] == 1
    assert media['coverage'] == .5


def test_vacancia_financeira_do_fundamentus_prevalece_sobre_a_vitrine(monkeypatch):
    from core.fundamentus import batch_fiis

    monkeypatch.setattr('core.fundamentus._scrape_fii_lista', lambda: {})
    monkeypatch.setattr('core.fundamentus._scrape_fii',
                        lambda t: {'vacancia_media': 2.0, 'vacancia_financ': 1.5})
    monkeypatch.setattr('core.market_read.load_fii_methodology_inputs',
                        lambda **k: (_ for _ in ()).throw(AssertionError('não deveria consultar')))
    assert batch_fiis(('FII11',))['FII11']['vacancia_financ'] == 1.5


def test_vitrine_indisponivel_deixa_vacancia_financeira_vazia(monkeypatch):
    from core.fundamentus import batch_fiis

    _fundamentus_sem_vacancia_financeira(monkeypatch)
    monkeypatch.setattr('core.market_read.load_fii_methodology_inputs',
                        lambda **k: (_ for _ in ()).throw(RuntimeError('vitrine fora')))
    dados = batch_fiis(('FII11',))
    assert dados['FII11']['vacancia_financ'] is None
    assert dados['FII11']['dy'] == 9.

    # vitrine carregada mas sem a coluna: idem, sem valor padrão
    monkeypatch.setattr('core.market_read.load_fii_methodology_inputs',
                        lambda **k: _vitrine_fii([{'ticker': 'FII11', 'dy': 0.1}]))
    assert batch_fiis(('FII11',))['FII11']['vacancia_financ'] is None


def test_painel_de_fiis_informa_fonte_e_referencia_da_vacancia_financeira(monkeypatch):
    import design.portfolio_valuations as dpv

    detalhes = []
    monkeypatch.setattr(dpv, 'detalhe_tecnico',
                        lambda texto, **k: detalhes.append((k.get('codigo'), texto)))
    fundamentals = {
        'FII11': {'vacancia_financ': 2.6, 'vacancia_financ_fonte': 'public_fii_report_provisional_v1',
                  'vacancia_financ_ref': '2026-04-30'},
        'OUTRO11': {'vacancia_financ': 6.7, 'vacancia_financ_fonte': 'public_fii_report_provisional_v1',
                    'vacancia_financ_ref': '2025-03-31'},
        'FORA11': {'vacancia_financ': 9.9, 'vacancia_financ_fonte': 'x', 'vacancia_financ_ref': '2020-01-01'},
    }
    positions = [{'ticker': 'FII11', 'valor_mercado': 100}, {'ticker': 'OUTRO11', 'valor_mercado': 100}]
    resultado = aggregate_valuations(positions, fundamentals, ('vacancia_financ',))
    dpv._nota_vacancia_financeira(positions, fundamentals, resultado)
    codigos = [c for c, _ in detalhes]
    assert codigos == ['carteira.valuation_vacancia_financeira_fonte']
    texto = detalhes[0][1]
    # intervalo só dos fundos da classe: o ticker fora das posições não entra
    assert 'referência de 2025-03-31 a 2026-04-30' in texto
    assert '2020-01-01' not in texto
    assert 'public_fii_report_provisional_v1' in texto

    # sem média (Fundamentus vazio e vitrine sem cobertura) não há nota: a lacuna fica com o aviso
    detalhes.clear()
    vazio = aggregate_valuations(positions, {'FII11': {'vacancia_financ': None}}, ('vacancia_financ',))
    dpv._nota_vacancia_financeira(positions, {'FII11': {'vacancia_financ': None}}, vazio)
    assert detalhes == []
