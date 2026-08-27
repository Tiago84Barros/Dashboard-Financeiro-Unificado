# -*- coding: utf-8 -*-
"""A-143: a auditoria de qualidade nao olhava a receita.

O A-142 substituia a receita de qualquer empresa com caixa aplicado pela
receita de intermediacao financeira. Deu receita NEGATIVA em 1.816 linhas
anuais -- 9,2% das que tinham receita -- e `run_audit` nao reportou nada,
porque nenhum de seus sete checks tocava a coluna `revenue`.

Havia um `check_margin_plausible` no modulo, testado unitariamente, que
nenhum gate consultava. Motor de diagnostico sem porta de entrada e
decoracao.
"""
from __future__ import annotations

from data_pipeline.us import quality


class _Row(tuple):
    """Resultado de `.one()`: os checks indexam por posicao."""


class _Conn:
    """Devolve contagens roteadas pelo trecho de SQL que as pede."""

    def __init__(self, por_trecho: dict[str, tuple], padrao=(0, 0, 0)):
        self.por_trecho = por_trecho
        self.padrao = padrao
        self.inseridos: list[dict] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "INSERT INTO" in sql:
            self.inseridos.append(dict(params or {}))
            return self
        for trecho, valores in self.por_trecho.items():
            if trecho in sql:
                return _Resultado(valores)
        return _Resultado(self.padrao)

    def one(self):
        return self.padrao


class _Resultado:
    def __init__(self, valores):
        self.valores = valores

    def one(self):
        return _Row(self.valores)


class _Engine:
    def __init__(self, conn):
        self._conn = conn

    def begin(self):
        engine_conn = self._conn

        class _Ctx:
            def __enter__(self):
                return engine_conn

            def __exit__(self, *exc):
                return False

        return _Ctx()


def _rodar(revenue_negativa: int, total: int = 1000, nulas: int = 0):
    conn = _Conn({"revenue < 0": (total, nulas, revenue_negativa)})
    return quality.run_audit(_Engine(conn)), conn


def _check(resultado, nome):
    return next(c for c in resultado["checks"] if c["name"] == nome)


def test_a_auditoria_agora_mede_o_sinal_da_receita():
    resultado, _ = _rodar(revenue_negativa=0)
    nomes = {c["name"] for c in resultado["checks"]}
    assert "revenue_sign" in nomes
    assert "net_income_exceeds_revenue" in nomes


def test_receita_negativa_reprova_o_gate():
    """Sob o A-142 eram 9,2%; o gate tolera 1%."""
    resultado, _ = _rodar(revenue_negativa=92, total=1000)
    check = _check(resultado, "revenue_sign")
    assert check["gate"] is True
    assert check["severity"] == "critical"
    assert check["passed"] is False


def test_o_residuo_legitimo_nao_reprova():
    """Depois da correcao sobraram 16 linhas em ~7.500 (0,21%): tres veiculos
    financeiros cuja receita e negativa no proprio arquivo da SEC."""
    resultado, _ = _rodar(revenue_negativa=2, total=1000)
    assert _check(resultado, "revenue_sign")["passed"] is True


def test_lucro_acima_da_receita_e_serie_e_nao_veredito():
    """0,38% no parser limpo contra 0,67% no defeituoso: separacao pequena
    demais para gate. Declarar gate aqui produziria alarme falso constante."""
    resultado, _ = _rodar(revenue_negativa=0)
    check = _check(resultado, "net_income_exceeds_revenue")
    assert check["gate"] is False
    assert check["passed"] is None


def test_receita_ausente_nao_conta_como_falha():
    """Empresa pre-receita nao tem receita; ausencia nao e sinal errado."""
    resultado, _ = _rodar(revenue_negativa=0, total=1000, nulas=400)
    check = _check(resultado, "revenue_sign")
    assert check["checked"] == 600
    assert check["skipped"] == 400
