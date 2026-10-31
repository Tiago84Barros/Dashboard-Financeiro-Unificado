"""Auditoria app4, LLM-A10/A11/A12 (04/10/2026).

* A10: as observações macro são lidas uma vez por bloco de mercado, não uma
  vez por classe de ativo -- sem nunca devolver linha posterior ao ``as_of``
  pedido.
* A11: a falha do provedor vira texto amigável; a exceção vai para o log e
  não para a tela nem para o histórico do chat.
* A12: os relatórios de carteira B3/EUA levam ``REGRA_CONTEXTO_MERCADO``.

Nada aqui toca rede nem banco.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

import core.macro_data.portfolio_context as pc
import core.portfolio_report_b3 as rb3
import core.portfolio_report_us as rus
from core.contexto_mercado import REGRA_CONTEXTO_MERCADO
from core.llm_falha import mensagem_falha_llm

# ── A11 ──────────────────────────────────────────────────────────────────────
_SEGREDO = "org-AbC123 req_9f8e7d HTTP 500 body={'error': 'x'}"


def test_falha_do_provedor_nao_vaza_a_excecao_e_fica_no_log(caplog):
    exc = RuntimeError(f"Todos os provedores LLM falharam — openai: {_SEGREDO}")
    with caplog.at_level(logging.ERROR, logger="core.llm_falha"):
        texto = mensagem_falha_llm(exc, "chat do ativo PETR4")
    assert _SEGREDO not in texto
    assert "org-AbC123" not in texto
    assert "log do app" in texto
    registro = [r for r in caplog.records if r.name == "core.llm_falha"]
    assert registro and "chat do ativo PETR4" in registro[0].getMessage()
    assert registro[0].exc_info is not None


def test_falha_classifica_o_motivo_sem_repetir_o_texto():
    assert "limite de uso" in mensagem_falha_llm(
        RuntimeError("Error code: 429 - insufficient_quota"), "x")
    assert "demorou demais" in mensagem_falha_llm(TimeoutError("read timed out"), "x")
    assert "chave" in mensagem_falha_llm(RuntimeError("401 invalid_api_key"), "x")


def test_mensagem_de_configuracao_do_app_sai_inteira():
    exc = RuntimeError("Nenhum provedor LLM configurado. Defina OPENAI_API_KEY.")
    texto = mensagem_falha_llm(exc, "chat da carteira", acao="gerar o dossiê")
    assert texto.startswith("Não foi possível gerar o dossiê agora:")
    assert "Defina OPENAI_API_KEY" in texto


# ── A12 ──────────────────────────────────────────────────────────────────────
def _captura(monkeypatch, modulo) -> dict:
    capturado: dict[str, str] = {}

    def falso(prompt: str, model: str | None = None) -> str:
        capturado["prompt"] = prompt
        return json.dumps({}, ensure_ascii=False)

    monkeypatch.setattr(modulo, "_call_llm", falso)
    return capturado


def test_relatorios_de_empresa_levam_a_regra_de_contexto():
    dossie_b3 = {"identificacao": {"nome": "T", "setor": "S", "subsetor": "U",
                                   "segmento": "G"}, "series_anuais": []}
    p_b3 = rb3.build_company_prompt("TEST3", dossie_b3, pd.DataFrame(),
                                    pd.DataFrame(), {}, "PARES", "RAG", "CARTEIRA")
    assert p_b3.startswith(REGRA_CONTEXTO_MERCADO)
    p_us = rus.build_company_prompt("TST", {"name": "T"}, pd.DataFrame(), {}, {},
                                    "PARES", "CARTEIRA")
    assert p_us.startswith(REGRA_CONTEXTO_MERCADO)


def test_consolidados_levam_a_regra_de_contexto(monkeypatch):
    cap = _captura(monkeypatch, rb3)
    rb3.analyze_portfolio_report([], {})
    assert cap["prompt"].startswith(REGRA_CONTEXTO_MERCADO)
    cap = _captura(monkeypatch, rus)
    rus.analyze_us_portfolio_report([], {})
    assert cap["prompt"].startswith(REGRA_CONTEXTO_MERCADO)


# ── A10 ──────────────────────────────────────────────────────────────────────
class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return self

    def all(self):
        return self._linhas


class _Conexao:
    def __init__(self, motor):
        self.motor = motor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        if "macro_observations" in str(sql):
            self.motor.leituras.append(params["as_of"])
            return _Resultado([{"lido_em": params["as_of"]}])
        return _Resultado([{"sector": "X"}])


class _Motor:
    url = "postgresql://u@localhost:5433/teste"

    def __init__(self):
        self.leituras: list[datetime] = []

    def connect(self):
        return _Conexao(self)


_T0 = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)


def _ler(motor, as_of, asset_class="b3"):
    return pc._ler_insumos(motor, asset_class=asset_class, sectors=["S"],
                           as_of=as_of, knowledge_mode="strict")


def test_duas_classes_no_mesmo_bloco_leem_as_observacoes_uma_vez():
    motor = _Motor()
    _, obs_b3 = _ler(motor, _T0, "b3")
    _, obs_fii = _ler(motor, _T0 + timedelta(seconds=2), "fii")
    assert motor.leituras == [_T0]
    assert obs_fii == obs_b3


def test_as_of_anterior_a_leitura_guardada_rele():
    # A leitura em _T0 pode conter linha recuperada depois de _T0 - 1 h: usá-la
    # para um pedido histórico quebraria o point-in-time.
    motor = _Motor()
    _ler(motor, _T0)
    _ler(motor, _T0 - timedelta(hours=1))
    assert motor.leituras == [_T0, _T0 - timedelta(hours=1)]


def test_as_of_alem_do_ttl_rele():
    motor = _Motor()
    _ler(motor, _T0)
    _ler(motor, _T0 + timedelta(seconds=pc._TTL_OBSERVACOES_S + 1))
    assert len(motor.leituras) == 2


def test_ttl_em_tempo_de_relogio_expira_a_leitura(monkeypatch):
    motor = _Motor()
    agora = [1000.0]
    monkeypatch.setattr(pc.time, "monotonic", lambda: agora[0])
    _ler(motor, _T0)
    agora[0] += pc._TTL_OBSERVACOES_S + 1
    _ler(motor, _T0)
    assert len(motor.leituras) == 2


def test_motores_diferentes_nao_compartilham_leitura():
    um, outro = _Motor(), _Motor()
    outro.url = "postgresql://u@localhost:5433/outro"
    _ler(um, _T0)
    _ler(outro, _T0)
    assert um.leituras == [_T0] and outro.leituras == [_T0]
