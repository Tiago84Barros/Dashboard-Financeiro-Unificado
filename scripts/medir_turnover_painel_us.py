# -*- coding: utf-8 -*-
"""Reconta entradas e saidas do painel PIT e mede o que a MEDICAO de retorno ve.

Duas perguntas, e elas nao tem a mesma resposta desde que as empresas mortas
entraram no universo:

  1. o painel tem saidas? -- `medir_turnover`. Ate 30/08/2026 a resposta era
     ZERO em 16 safras, assinatura de amostra 100% sobrevivente. Com os
     fundamentos das deslistadas ingeridos, o painel passou a perder empresas.
  2. o retorno delas e observavel? -- NAO. Nenhuma fonte acessivel serve preco
     de ticker morto, entao a linha sem preco futuro e descartada pelo backtest
     e o excesso continua apurado entre sobreviventes.

Descartar nao e neutro: equivale a supor que quem morreu teria rendido a media
dos vivos. Como o top-N evita justamente os nomes que morrem e o equal-weight os
carrega, essa suposicao trabalha CONTRA o excesso medido -- o numero publicado e
a ponta pessimista de uma banda, nao a verdade nem uma escolha conservadora.
`scripts/sensibilidade_retorno_deslistagem_us.py` mede a banda inteira.

A fracao sem preco futuro fica gravada aqui para a tela poder dizer o tamanho
disso em vez de avisar que "existe um vies".

    python scripts/medir_turnover_painel_us.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION  # noqa: E402
from core.us_survivorship import (  # noqa: E402
    CAMINHO_MEDICAO,
    carregar_medicao,
    gravar_medicao,
    medicao_turnover_verificada,
    medir_turnover,
)
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

_SQL_RETORNO = """
  SELECT COUNT(*) AS linhas,
         COUNT(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM market_us.prices_monthly p
           WHERE p.symbol = v.symbol AND p.month_end > v.as_of_date)) AS com_preco
  FROM market_us.score_vintages v
  WHERE v.track='fundamental' AND v.score_version = :v
"""


# O que esta medicao apura e, portanto, sobrescreve. Todo o resto do arquivo e
# de outra medicao e passa adiante intacto.
_CHAVES_DESTA_MEDICAO = frozenset({
    "medido_em", "safras", "primeira_safra", "ultima_safra",
    "empresas_primeira", "empresas_ultima", "entradas", "saidas",
    "medicao_de_retorno",
})


def main() -> int:
    engine = create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))
    medicao = medir_turnover(engine)
    if not medicao_turnover_verificada(medicao):
        print("medicao reprovada no contrato; nada gravado")
        return 1
    with engine.connect() as conn:
        linhas, com_preco = conn.execute(
            text(_SQL_RETORNO), {"v": US_FUNDAMENTAL_SCORE_VERSION}).fetchone()
    medicao["medicao_de_retorno"] = {
        "linhas_do_painel": int(linhas),
        "com_preco_futuro": int(com_preco),
        "sem_preco_futuro": int(linhas - com_preco),
        "fracao_sem_preco": round((linhas - com_preco) / linhas, 4) if linhas else None,
    }
    # O bloco da coorte vem de outra medicao (mortalidade fora do painel) e nao
    # pode ser perdido por esta: sao evidencias que se completam.
    #
    # A preservacao e por lista NEGRA -- as chaves que esta medicao produz --, e
    # nao por lista branca das que ela deve manter. A lista branca ja falhou:
    # dizia ("coorte", "operacional", "score_vs_morte") e a chave real era
    # `coorte_operacional`, entao a coorte preferida pelo seletor de mortalidade
    # sumiu do arquivo sem erro nenhum, e a frase da tela caiu para a coorte
    # ampla parecendo apenas ter mudado de numero. Chave que ninguem previu tem
    # de sobreviver por padrao.
    anterior = carregar_medicao() or {}
    for chave, valor in anterior.items():
        if chave not in _CHAVES_DESTA_MEDICAO and chave not in medicao:
            medicao[chave] = valor
    print(json.dumps({k: v for k, v in medicao.items()
                      if not isinstance(v, dict) or k == "medicao_de_retorno"},
                     indent=2, ensure_ascii=False))
    print("gravado em", gravar_medicao(medicao, CAMINHO_MEDICAO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
