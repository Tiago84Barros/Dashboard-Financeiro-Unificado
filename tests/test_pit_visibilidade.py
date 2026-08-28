"""A visibilidade point-in-time não pode depender do futuro da empresa (A-159).

`_build_rows` carimba a linha com `available_at = max(filings)`. Sob esse
critério, um campo que só passou a ser tagueado anos depois torna o exercício
inteiro invisível para toda safra anterior -- e só continua arquivando quem
sobreviveu. O painel acaba enxergando MENOS dado de quem viveu mais, que é o
contrário de conservador num painel já sobrevivente.

Medido na coorte de 2012 (`scripts/testar_score_prediz_morte_us.py`): cobertura
média de 36% para sobreviventes contra 51% para as que desapareceram.
"""
from __future__ import annotations

from datetime import date

from scripts._pit_visibilidade import REGRA_CAMPO, REGRA_LINHA, aplicar

AS_OF = date(2013, 6, 30)


def _linha(**kw):
    base = {"reference_date": "2012-12-31", "revenue": 100.0, "operating_income": 10.0,
            "_filed": {"revenue": "2013-02-01", "operating_income": "2013-02-01"},
            "available_at": "2013-02-01"}
    base.update(kw)
    return base


def test_campo_tagueado_depois_nao_apaga_o_exercicio_inteiro() -> None:
    """O caso que produz o viés: uma tag estreada em 2016 esconde o ano de 2012."""
    r = _linha(sbc=5.0, _filed={"revenue": "2013-02-01",
                                "operating_income": "2013-02-01",
                                "sbc": "2016-02-01"},
               available_at="2016-02-01")
    assert aplicar([r], AS_OF, REGRA_LINHA, "inc") == []
    campo = aplicar([r], AS_OF, REGRA_CAMPO, "inc")
    assert len(campo) == 1
    assert campo[0]["revenue"] == 100.0
    assert campo[0]["sbc"] is None      # ausente, e ausente não é zero


def test_campo_publicado_depois_nao_vaza_valor() -> None:
    """Deixar o número entrar antes da data seria olhar o futuro -- o vício oposto."""
    r = _linha(sbc=5.0, _filed={"revenue": "2013-02-01", "sbc": "2016-02-01"})
    assert aplicar([r], AS_OF, REGRA_CAMPO, "inc")[0]["sbc"] is None


def test_exercicio_sem_nenhum_campo_conhecido_continua_invisivel() -> None:
    r = _linha(_filed={"revenue": "2014-02-01", "operating_income": "2014-02-01"},
               available_at="2014-02-01")
    assert aplicar([r], AS_OF, REGRA_CAMPO, "inc") == []
    assert aplicar([r], AS_OF, REGRA_LINHA, "inc") == []


def test_regra_linha_reproduz_a_producao() -> None:
    dentro, fora = _linha(), _linha(available_at="2014-02-01")
    assert len(aplicar([dentro, fora], AS_OF, REGRA_LINHA, "inc")) == 1


def test_derivado_acompanha_a_ausencia_do_insumo() -> None:
    """Dívida líquida com caixa invisível viraria número que ninguém publicou."""
    r = {"reference_date": "2012-12-31", "short_term_debt": 10.0,
         "long_term_debt": 20.0, "cash_and_equivalents": 5.0,
         "total_debt": 30.0, "net_debt": 25.0, "available_at": "2016-01-01",
         "_filed": {"short_term_debt": "2013-02-01", "long_term_debt": "2013-02-01",
                    "cash_and_equivalents": "2016-01-01"}}
    v = aplicar([r], AS_OF, REGRA_CAMPO, "bal")[0]
    assert v["total_debt"] == 30.0
    assert v["cash_and_equivalents"] is None
    assert v["net_debt"] is None


def test_demonstracao_errada_falha_em_vez_de_nao_derivar() -> None:
    """Sem derivacao o numero derivado sobrevive a mascara: falhar e mais seguro."""
    import pytest
    with pytest.raises(ValueError):
        aplicar([_linha()], AS_OF, REGRA_CAMPO, "balanco")
