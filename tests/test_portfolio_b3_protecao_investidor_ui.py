"""Task 7 — tela mostra DY sustentável e etiqueta o líder admitido por
viabilidade.

A montagem de ``motivos`` e dos avisos de transparência do piso mora dentro
de ``render()``, uma função gigante que precisa de Streamlit + banco reais
(nem Supabase nem o warehouse local estão alcançáveis neste ambiente). Por
isso a Task 7 extraiu a lógica de exibição para três funções puras —
``_motivo_afrouxamento_lider``, ``_motivos_dy_sustentavel`` e
``_avisos_afrouxamento_piso`` — e ``render()`` apenas as chama. Testar essas
funções prova exatamente o que aparece na tela, porque são a MESMA linha de
produção, não uma reimplementação paralela.
"""
import math

import pandas as pd
import pytest

from views.portfolio_b3 import (
    _avisos_afrouxamento_piso,
    _motivo_afrouxamento_lider,
    _motivos_dy_sustentavel,
)


def _df_mult(**linhas: dict) -> pd.DataFrame:
    registros = []
    for tk, campos in linhas.items():
        registro = {"Ticker": tk, "DY": campos.get("DY", 0.05),
                    "payout_sustentabilidade": campos.get("payout_sustentabilidade"),
                    "dy_sustentavel": campos.get("dy_sustentavel"),
                    "n_anos_payout": campos.get("n_anos_payout", 0)}
        registros.append(registro)
    return pd.DataFrame.from_records(registros)


# ── (a) DY sustentável aparece quando há sustentabilidade ────────────────────

def test_motivos_dy_sustentavel_mostra_linha_quando_sustentavel():
    df = _df_mult(PETR4={
        "DY": 0.10, "payout_sustentabilidade": 0.8,
        "dy_sustentavel": 0.08, "n_anos_payout": 5,
    })

    motivos = _motivos_dy_sustentavel("PETR4", df)

    assert len(motivos) == 1
    assert "DY sustentável 8.0%" in motivos[0]
    assert "divulgado 10.0%" in motivos[0]
    assert "sustentabilidade 80%" in motivos[0]
    assert "5 anos" in motivos[0]


# ── (b) indisponibilidade aparece (não é omitida) quando faltam anos ─────────

def test_motivos_dy_sustentavel_mostra_indisponibilidade_sem_omitir():
    df = _df_mult(VALE3={
        "DY": 0.06, "payout_sustentabilidade": float("nan"),
        "dy_sustentavel": float("nan"), "n_anos_payout": 1,
    })

    motivos = _motivos_dy_sustentavel("VALE3", df)

    assert len(motivos) == 1
    assert "indisponível" in motivos[0]
    assert "menos de 3 anos" in motivos[0]


def test_motivos_dy_sustentavel_vazio_quando_ticker_ausente_do_universo():
    df = _df_mult(VALE3={"DY": 0.06, "payout_sustentabilidade": 0.5,
                          "dy_sustentavel": 0.03, "n_anos_payout": 4})

    assert _motivos_dy_sustentavel("PETR4", df) == []


# ── (c) aviso de afrouxamento aparece quando há item ──────────────────────────

def test_motivo_afrouxamento_lider_presente_quando_marcado():
    piso_log = {"afrouxado_por_viabilidade": [
        {"tk": "LIDER3", "segmento": "Papel e Celulose",
         "motivo": "nenhum candidato do segmento sobreviveu"},
    ]}

    motivo = _motivo_afrouxamento_lider("LIDER3", piso_log)

    assert motivo is not None
    assert "Entrou com ressalva" in motivo


def test_motivo_afrouxamento_lider_ausente_quando_nao_marcado():
    piso_log = {"afrouxado_por_viabilidade": []}

    assert _motivo_afrouxamento_lider("LIDER3", piso_log) is None


def test_avisos_afrouxamento_piso_gera_mensagem_por_item():
    piso_log = {"afrouxado_por_viabilidade": [
        {"tk": "LIDER3", "segmento": "Papel e Celulose",
         "motivo": "nenhum candidato do segmento sobreviveu à confirmação "
                   "histórica de payout"},
    ]}

    avisos = _avisos_afrouxamento_piso(piso_log)

    assert len(avisos) == 1
    assert "Papel e Celulose" in avisos[0]
    assert "LIDER3" in avisos[0]
    assert "MARCADO" in avisos[0]
    assert "não confirmada" in avisos[0]


def test_avisos_afrouxamento_piso_vazio_quando_sem_afrouxamento():
    assert _avisos_afrouxamento_piso({"afrouxado_por_viabilidade": []}) == []
    # log sem a chave (compat com quem monta piso_log fora do padrão da Task 5)
    assert _avisos_afrouxamento_piso({}) == []
