"""R2: produção e safra PIT calculavam o MESMO nome com fórmulas diferentes.

- produção: ``positive_share/(1+cv)`` sobre 36 meses
- safra PIT: ``max(0, 1 - std/mean)`` sobre os 24 últimos meses com pagamento

Sem ``positive_share`` e com janela menor. O certificado validava uma
metodologia que a produção não executa — exatamente o que o comentário de
``fii_pit`` diz ter sido corrigido para o *crescimento* em 23/08/2026, e que a
recorrência não recebeu.

A unificação não é só trocar a fórmula: o PIT recortava os dividendos em
``3*365+31`` dias antes de agregar. Com a janela recortada na vida observada,
esse recorte vira uma segunda fonte de divergência — um fundo que pagou, ficou
mudo dois anos e voltou a pagar há doze teria o primeiro provento "de toda a
série" perdido pelo corte e sairia com nota perfeita no PIT enquanto a
produção, que lê o histórico inteiro, o pune.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from core.fii_methodology import income_recurrence
from data_pipeline.market.fii_pit import _features_as_of, _monthly_market_features


def _precos(fim: str, meses: int) -> pd.DataFrame:
    datas = pd.date_range(end=pd.Timestamp(fim), periods=meses * 21, freq="B")
    return pd.DataFrame({"ticker": "TEST11", "date": datas, "close": 100.0,
                         "volume": 1_000_000.0})


def _dividendos(pagamentos: dict[pd.Timestamp, float],
                amortizacoes: dict[pd.Timestamp, float] | None = None) -> pd.DataFrame:
    linhas = [
        {"ticker": "TEST11", "event_date": quando, "ex_date": quando,
         "payment_date": quando, "amount": valor, "type": "RENDIMENTO"}
        for quando, valor in pagamentos.items() if valor > 0
    ]
    linhas += [
        {"ticker": "TEST11", "event_date": quando, "ex_date": quando,
         "payment_date": quando, "amount": valor, "type": "AMORTIZAÇÃO"}
        for quando, valor in (amortizacoes or {}).items() if valor > 0
    ]
    return pd.DataFrame(linhas)


def _mensal(fim: pd.Timestamp, meses: int, valor: float = 1.0) -> dict:
    """Um pagamento no dia 10 de cada um dos ``meses`` que terminam em ``fim``."""
    saida = {}
    cursor = fim.to_period("M")
    for offset in range(meses):
        mes = (cursor - offset).to_timestamp() + pd.Timedelta(days=9)
        saida[mes] = valor
    return saida


CUTOFF = pd.Timestamp("2026-09-30")


def _recorrencia_pit(pagamentos: dict, amortizacoes: dict | None = None) -> float | None:
    bundle = _monthly_market_features(_precos("2026-09-30", 130),
                                      _dividendos(pagamentos, amortizacoes))["TEST11"]
    return _features_as_of(bundle, CUTOFF)["income_recurrence"]


def _recorrencia_producao(pagamentos: dict) -> float | None:
    """O mesmo que ``_derive_income_observations`` entrega à ingestão: soma por
    mês de TODO o histórico, sem recorte de janela na origem."""
    por_mes: dict[date, float] = {}
    for quando, valor in pagamentos.items():
        chave = quando.date().replace(day=1)
        por_mes[chave] = por_mes.get(chave, 0.0) + valor
    return income_recurrence(por_mes, CUTOFF.date().replace(day=1))


def test_pit_e_producao_concordam_em_fundo_jovem():
    """O caso RBVA11: 16 meses de vida, todos pagos. A fórmula antiga do PIT
    (``1 - std/mean`` sobre os meses COM pagamento) dá 1,0 por acidente, mas
    por não enxergar os meses sem pagamento — não por recortar a vida."""
    pagamentos = _mensal(pd.Timestamp("2026-09-01"), 16)
    assert _recorrencia_pit(pagamentos) == _recorrencia_producao(pagamentos)


def test_pit_e_producao_concordam_em_pagamento_irregular():
    """Aqui as duas fórmulas divergem de verdade: ``positive_share`` pune os
    meses sem pagamento e ``1 - std/mean`` nem os vê, porque o ``resample`` do
    PIT partia da primeira data COM dividendo."""
    pagamentos = _mensal(pd.Timestamp("2026-09-01"), 36)
    for offset in range(0, 36, 3):                    # paga só a cada 3 meses
        mes = (pd.Timestamp("2026-09-01").to_period("M") - offset)
        pagamentos[mes.to_timestamp() + pd.Timedelta(days=9)] = 0.0
    pagamentos = {k: v for k, v in pagamentos.items() if v > 0}
    pit, producao = _recorrencia_pit(pagamentos), _recorrencia_producao(pagamentos)
    assert pit == producao, f"PIT {pit} x produção {producao}"


def test_pit_enxerga_o_primeiro_provento_fora_da_janela_de_tres_anos():
    """O recorte de ``3*365+31`` dias na origem é a divergência que sobra.

    O silêncio tem de ser maior que a janela de ``3*365+31`` dias, senão o
    último pagamento anterior a ele sobrevive ao corte e o teste passa
    igualzinho com e sem a correção — foi o que este teste fazia quando o
    fundo ficava mudo por 24 meses. Com 36 meses de silêncio o corte aparece:
    revertido o pedaço, o PIT dá 1,0 (só vê os 12 meses recentes, todos pagos)
    contra 0,138 da produção.

    Fundo que pagou 2018-2022, ficou mudo três anos e voltou a pagar há doze
    meses: a produção lê o histórico inteiro, acha o primeiro provento em 2018
    e pune os 36 meses mudos. O recorte de origem é a metade da correção que
    não se vê na fórmula, e é por isso que a série vem inteira do histórico.
    """
    pagamentos = _mensal(pd.Timestamp("2022-09-01"), 48)          # 2018-2022
    pagamentos.update(_mensal(pd.Timestamp("2026-09-01"), 12))    # voltou
    pit, producao = _recorrencia_pit(pagamentos), _recorrencia_producao(pagamentos)
    assert producao is not None and producao < .70, (
        f"36 meses mudos têm de punir a produção, deu {producao}")
    assert pit == producao, (
        f"o PIT premiaria o fundo mudo por não enxergar 2018: {pit} x {producao}")


def test_historico_curto_no_pit_nao_produz_metrica():
    """O mínimo de 12 meses observados vale nos dois lados. Oito meses de vida
    davam 1,0 na fórmula antiga do PIT — ``std/mean`` de uma série constante é
    zero, por mais curta que ela seja."""
    pagamentos = _mensal(pd.Timestamp("2026-09-01"), 8)
    assert _recorrencia_pit(pagamentos) is None
    assert _recorrencia_producao(pagamentos) is None


def test_fundo_sem_dividendo_nenhum_nao_produz_metrica():
    assert _recorrencia_pit({}) is None


def test_pit_nao_conta_amortizacao_como_renda():
    """R3 só foi aplicado de um lado. ``fii_pit._load_frames`` seleciona
    ``type`` e nunca o filtra, então ``AMORTIZAÇÃO`` — devolução do principal
    do cotista, não renda — entra na série de renda do PIT e não na da
    produção. Medido em 13/09/2026 no armazém local: 71 de 401 FIIs recebiam
    número diferente nos dois lados depois da "definição única", 64 deles com
    linha de amortização, |Δ| mediano 0,0501 e máximo 0,5245 (BMLC11: produção
    0,8269 x PIT 0,3024).

    O fundo aqui paga renda em 24 meses, pulando um a cada três, e devolve
    capital exatamente nos meses pulados. Para a produção há oito meses sem
    renda; para o PIT, 24 meses de pagamento ininterrupto.
    """
    todos = _mensal(pd.Timestamp("2026-09-01"), 24)
    ordenados = sorted(todos)
    renda = {quando: 1.0 for indice, quando in enumerate(ordenados) if indice % 3}
    devolucao = {quando: 1.0 for indice, quando in enumerate(ordenados) if not indice % 3}
    pit = _recorrencia_pit(renda, devolucao)
    producao = _recorrencia_producao(renda)
    assert pit == producao, (
        f"amortização entra só no PIT: {pit} x produção {producao}")
