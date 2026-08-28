# -*- coding: utf-8 -*-
"""SCORE-05: a vantagem fora da amostra medida vale; a herdada, não."""
from __future__ import annotations

import json

import pytest

from core.vantagem_oos import (
    avaliar,
    carregar_medicao,
    gravar_medicao,
    nova_medicao,
)


def _medicao(**kw):
    base = dict(motor="us", versao_metodologia="v1", metrica="excesso por periodo",
                media=0.01, ic_low=0.002, ic_high=0.02, n_periodos=15,
                configuracao="top_n=20", fonte="armazem local")
    base.update(kw)
    return nova_medicao(**base)


def test_intervalo_inteiramente_positivo_aprova(tmp_path):
    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(), arq)
    ok, detalhe = avaliar("us", "v1", arq)
    assert ok is True
    assert "+0,20%" in detalhe.replace(".", ",") or "0.20%" in detalhe


def test_intervalo_que_atravessa_o_zero_reprova(tmp_path):
    """Média positiva com IC contendo zero não é vantagem — é ruído com sinal."""
    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(media=0.0017, ic_low=-0.0013, ic_high=0.0045), arq)
    ok, detalhe = avaliar("us", "v1", arq)
    assert ok is False
    assert "atravessa o zero" in detalhe


def test_medicao_de_outra_versao_nao_atesta_esta(tmp_path):
    """Subir a versão sem remedir não pode herdar a aprovação anterior."""
    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(versao_metodologia="v1"), arq)
    ok, detalhe = avaliar("us", "v2", arq)
    assert ok is None
    assert "v1" in detalhe and "v2" in detalhe


def test_ausencia_de_arquivo_e_nao_apurado_e_nao_reprovacao(tmp_path):
    ok, detalhe = avaliar("us", "v1", tmp_path / "inexistente.json")
    assert ok is None
    assert "nenhuma medicao" in detalhe


def test_intervalo_ausente_nao_reprova(tmp_path):
    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(ic_low=None, ic_high=None), arq)
    ok, _ = avaliar("us", "v1", arq)
    assert ok is None


def test_nan_no_intervalo_nao_passa_por_numero(tmp_path):
    """float('nan') sobrevive a float() e mentiria como se fosse medição."""
    arq = tmp_path / "v.json"
    arq.write_text(json.dumps({"us": {"motor": "us", "versao_metodologia": "v1",
                                      "ic_low": "NaN", "ic_high": "NaN",
                                      "n_periodos": 3}}), encoding="utf-8")
    ok, _ = avaliar("us", "v1", arq)
    assert ok is None


def test_gravar_um_motor_preserva_o_outro(tmp_path):
    """A medição da B3 não pode apagar a dos EUA — o arquivo é compartilhado."""
    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(motor="us"), arq)
    gravar_medicao(_medicao(motor="b3", metrica="Rank-IC anual"), arq)
    assert carregar_medicao("us", arq) is not None
    assert carregar_medicao("b3", arq)["metrica"] == "Rank-IC anual"


def test_arquivo_corrompido_nao_derruba_a_gravacao(tmp_path):
    arq = tmp_path / "v.json"
    arq.write_text("{isto nao e json", encoding="utf-8")
    gravar_medicao(_medicao(), arq)
    assert carregar_medicao("us", arq) is not None


def test_motor_desconhecido_e_erro_e_nao_registro_silencioso():
    with pytest.raises(ValueError):
        _medicao(motor="cripto")


def test_portao_do_validacao_motor_le_a_medicao(tmp_path, monkeypatch):
    """O portão da comparação de rigor tem de mudar quando a medição existe."""
    import core.vantagem_oos as vo
    from core.validacao_motor import DIM_VANTAGEM, _vantagem_persistida

    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(motor="b3", versao_metodologia="b3-x"), arq)
    monkeypatch.setattr(vo, "CAMINHO_MEDICAO", arq)

    portao = _vantagem_persistida("b3", "b3-x")
    assert portao.ok is True
    assert portao.dimensao == DIM_VANTAGEM

    # Versão diferente volta a "não apurado", não a uma aprovação herdada.
    assert _vantagem_persistida("b3", "b3-y").ok is None


def test_reprovacao_carrega_a_ressalva_do_rank_ic(tmp_path):
    """Reprovar pelo excesso sem dizer que o score ordena é meia-verdade."""
    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(media=-0.001, ic_low=-0.09, ic_high=0.07,
                            extras={"rank_ic_medio": 0.0959, "rank_ic_t": 3.73}), arq)
    ok, detalhe = avaliar("us", "v1", arq)
    assert ok is False
    assert "ordena o universo" in detalhe and "3.73" in detalhe


def test_rank_ic_insignificante_nao_vira_ressalva(tmp_path):
    """t abaixo de 2 não sustenta a frase — ressalva sem força vira desculpa."""
    arq = tmp_path / "v.json"
    gravar_medicao(_medicao(media=-0.001, ic_low=-0.09, ic_high=0.07,
                            extras={"rank_ic_medio": 0.01, "rank_ic_t": 0.4}), arq)
    _, detalhe = avaliar("us", "v1", arq)
    assert "ordena o universo" not in detalhe


def test_tela_marca_o_corte_do_detalhe_em_vez_de_apagar():
    """Truncar é aceitável; truncar sem o leitor perceber, não."""
    from views.confianca import _resumo

    curto = "IC 95% de -1% a +2%"
    assert _resumo(curto) == curto
    longo = "x" * 900
    cortado = _resumo(longo)
    assert cortado.endswith("…") and len(cortado) < len(longo)
