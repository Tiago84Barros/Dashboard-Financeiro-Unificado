"""A publicação desativa; a leitura precisa respeitar.

`_build_deactivate_stale` marca `is_active = FALSE, score_status = 'stale'` em
toda linha ausente do snapshot novo — é como a vitrine preserva o histórico sem
manter a empresa em análise. A leitura ignorava a marca: escritor e leitor
liando listas diferentes sobre a mesma linha.

Passou despercebido porque `_apenas_acoes` varria quase todos os restos. Em
31/08/2026 eram 205 linhas desativadas no Supabase, das quais 166 REITs caíam
nele — e as outras 39 eram BDCs (FS KKR, Goldman Sachs BDC, Gladstone Capital).
BDC não é REIT, então passava pelo filtro de ações e chegava à tela com nota de
seis dias antes, dentro de um módulo que declara analisar só ações.
"""
from __future__ import annotations

import pandas as pd

from core.us_read import _apenas_ativas


def _linha(symbol: str, is_active, name: str = "Acme Industrial Corp") -> dict:
    return {"symbol": symbol, "name": name, "sector": "Machinery",
            "industry": "Machinery", "is_active": is_active,
            "score_status": "stale" if is_active is False else "decision_grade"}


def test_linha_desativada_nao_chega_a_tela():
    df = pd.DataFrame([_linha("VIVA", True), _linha("MORTA", False)])
    assert list(_apenas_ativas(df)["symbol"]) == ["VIVA"]


def test_bdc_desativada_sai_mesmo_passando_pelo_filtro_de_acoes():
    """O caso real: fundo fechado de crédito não é REIT e escapava do outro net."""
    df = pd.DataFrame([
        _linha("VIVA", True),
        _linha("FSK", False, "FS KKR Capital Corp"),
        _linha("GSBD", False, "Goldman Sachs BDC, Inc."),
    ])
    assert list(_apenas_ativas(df)["symbol"]) == ["VIVA"]


def test_nulo_significa_nunca_desativada_e_nao_inativa():
    """Vitrine antiga pode ter a coluna nula — nulo não pode apagar a empresa."""
    df = pd.DataFrame([_linha("ANTIGA", None), _linha("VIVA", True)])
    assert set(_apenas_ativas(df)["symbol"]) == {"ANTIGA", "VIVA"}


def test_vitrine_sem_a_coluna_continua_sendo_lida():
    """Drift de schema já esvaziou a tela uma vez; a ausência não pode barrar."""
    df = pd.DataFrame([{"symbol": "VIVA", "name": "Acme", "sector": "X"}])
    assert list(_apenas_ativas(df)["symbol"]) == ["VIVA"]


def test_quadro_vazio_nao_quebra():
    assert _apenas_ativas(pd.DataFrame()).empty
