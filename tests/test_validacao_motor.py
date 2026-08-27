# -*- coding: utf-8 -*-
"""A-152: qual evidencia sustenta cada motor de score, dita na propria aba.

Ate aqui so a aba de FIIs declarava seu estado de validacao. A B3 APURAVA o
estado e nomeava os bloqueadores, e nenhuma tela consultava -- o unico chamador
de `validation_readiness` era o relatorio de confianca. A dos EUA declarava
dentro de um expander colapsado de outra aba, e so sobre o backtest.
"""
from __future__ import annotations

import core.validacao_motor as vm
from core.validacao_motor import EstadoValidacao


def test_aprovada_nao_cita_bloqueador():
    e = EstadoValidacao("Seleção de FIIs", "6.8.0", True)
    assert e.rotulo == "Aprovada"
    assert "6.8.0" in e.texto
    assert "fora da amostra" not in e.texto


def test_pendente_nomeia_os_bloqueadores():
    e = EstadoValidacao("Empresas B3", "2.25.0", False,
                        ("PIT estrito sem published_at/revisoes CVM",
                         "universo historico de deslistadas incompleto"))
    assert e.rotulo == "Pendente"
    assert "published_at" in e.texto
    assert "deslistadas" in e.texto
    assert "fora da amostra" in e.texto


def test_pendente_sem_bloqueador_ainda_avisa():
    e = EstadoValidacao("X", "1.0", False)
    assert "fora da amostra" in e.texto


def test_nao_apurada_nao_vira_pendente_nem_aprovada():
    """Apagar a diferenca entre 'medi e reprovou' e 'nao consegui medir' e o
    defeito que este modulo existe para nao repetir."""
    e = EstadoValidacao("X", "1.0", None)
    assert e.rotulo == "Não apurada"
    assert "pendente" not in e.texto.lower()
    assert "aprovada" not in e.texto.lower()


def test_b3_repassa_os_bloqueadores_da_fonte(monkeypatch):
    monkeypatch.setattr("core.b3_validation.build_data_manifest",
                        lambda *_a, **_k: {})
    monkeypatch.setattr("core.b3_validation.validation_readiness",
                        lambda *_a, **_k: {"ready": False, "blockers": ["falta X"]})
    e = vm.validacao_b3(engine=object())
    assert e.aprovada is False
    assert e.bloqueadores == ("falta X",)


def test_b3_pronta_nao_inventa_bloqueador(monkeypatch):
    monkeypatch.setattr("core.b3_validation.build_data_manifest",
                        lambda *_a, **_k: {})
    monkeypatch.setattr("core.b3_validation.validation_readiness",
                        lambda *_a, **_k: {"ready": True, "blockers": []})
    e = vm.validacao_b3(engine=object())
    assert e.aprovada is True and e.bloqueadores == ()


def test_fonte_indisponivel_devolve_nao_apurada(monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr("core.b3_validation.build_data_manifest", explode)
    e = vm.validacao_b3(engine=object())
    assert e.aprovada is None
    assert "RuntimeError" in e.detalhe


def test_fii_so_e_aprovada_quando_o_certificado_e_da_versao_em_uso(monkeypatch):
    monkeypatch.setattr("core.market_read.load_fii_validation_status",
                        lambda *_a, **_k: {"status": "unvalidated",
                                           "blockers": ["nenhuma validação PIT persistida"]})
    e = vm.validacao_fii()
    assert e.aprovada is False
    assert "nenhuma validação PIT persistida" in e.texto


def test_us_sem_vintages_e_pendente_e_diz_o_porque():
    e = vm.validacao_us(history_available=False)
    assert e.aprovada is False
    assert "score_vintages" in e.texto


def test_us_com_painel_pit_e_aprovada():
    e = vm.validacao_us(history_available=True)
    assert e.aprovada is True and e.bloqueadores == ()


def test_us_aceita_o_valor_ja_apurado_pela_tela_sem_reconsultar(monkeypatch):
    def nao_pode_ser_chamado(*_a, **_k):
        raise AssertionError("consultou o banco tendo o valor em maos")
    monkeypatch.setattr("core.us_data.score_panel", nao_pode_ser_chamado)
    assert vm.validacao_us(history_available=True).aprovada is True
