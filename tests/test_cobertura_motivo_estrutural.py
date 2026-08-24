# -*- coding: utf-8 -*-
"""A-133: ausencia ESTRUTURAL de preco nao pode usar o rotulo de falha.

Ate 24/08/2026 `retornos_mensais` marcava classe sem preco por natureza
(caixa, renda fixa) com o mesmo `sem_preco` de um ativo cuja serie deveria
existir e faltou. O proprio comentario acima da linha prometia motivo
proprio. Consequencia: a tela pedia acao ("olhe a ingestao") sobre um CDB,
que nunca tera serie mensal, e diluia o sinal do caso que de fato exige acao.
"""
from __future__ import annotations

import pandas as pd

from core.global_portfolio import returns
from core.global_portfolio.returns import Cobertura, retornos_mensais
from views.portfolio_global import aviso_de_cobertura


def _posicoes() -> pd.DataFrame:
    return pd.DataFrame([
        {"symbol": "PETR4", "asset_class": "b3", "weight_global": 0.5,
         "currency": "BRL"},
        {"symbol": "CDB01", "asset_class": "renda_fixa", "weight_global": 0.3,
         "currency": "BRL"},
        {"symbol": "CAIXA", "asset_class": "caixa", "weight_global": 0.2,
         "currency": "BRL"},
    ])


def test_motivos_sao_constantes_distintas():
    assert returns.MOTIVO_CLASSE_SEM_PRECO != returns.MOTIVO_SEM_PRECO


def test_classe_sem_preco_nao_recebe_rotulo_de_falha():
    """CDB e caixa: ausencia por natureza, nao defeito de ingestao."""
    _, cob = retornos_mensais(_posicoes(), loader=lambda _s: pd.DataFrame())
    assert cob.motivo_de("CDB01") == returns.MOTIVO_CLASSE_SEM_PRECO
    assert cob.motivo_de("CAIXA") == returns.MOTIVO_CLASSE_SEM_PRECO


def test_classe_com_preco_sem_serie_continua_sendo_falha():
    """PETR4 e classe b3: a serie DEVERIA existir, logo o motivo acusa."""
    _, cob = retornos_mensais(_posicoes(), loader=lambda _s: pd.DataFrame())
    assert cob.motivo_de("PETR4") == returns.MOTIVO_SEM_PRECO


def test_os_dois_casos_convivem_sem_se_fundir():
    _, cob = retornos_mensais(_posicoes(), loader=lambda _s: pd.DataFrame())
    grupos = cob.simbolos_por_motivo()
    assert grupos[returns.MOTIVO_CLASSE_SEM_PRECO] == ("CAIXA", "CDB01")
    assert grupos[returns.MOTIVO_SEM_PRECO] == ("PETR4",)


def test_aviso_separa_estrutural_de_falha_de_ingestao():
    """Porta de entrada: distinguir no motor sem exibir nao serve a ninguem."""
    cob = Cobertura(
        simbolos_com_serie=("PETR4",), simbolos_sem_serie=("CDB01", "NVDA"),
        peso_coberto=0.6, meses=30,
        motivos=(("CDB01", returns.MOTIVO_CLASSE_SEM_PRECO),
                 ("NVDA", returns.MOTIVO_SEM_PRECO)),
    )
    msg = aviso_de_cobertura(cob)
    assert msg is not None
    assert "por natureza" in msg
    assert "ingest" in msg
    # o CDB nao pode aparecer sob o rotulo que pede acao
    assert msg.index("CDB01", msg.index("Por motivo")) < msg.index("NVDA", msg.index("Por motivo"))


def test_aviso_sem_motivos_nao_inventa_quebra():
    """Cobertura antiga (sem `motivos`) nao pode gerar frase vazia."""
    cob = Cobertura(simbolos_com_serie=("PETR4",), simbolos_sem_serie=("X",),
                    peso_coberto=0.7, meses=30)
    msg = aviso_de_cobertura(cob)
    assert msg is not None and "Por motivo" not in msg
