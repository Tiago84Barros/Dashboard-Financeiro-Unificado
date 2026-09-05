"""Entrada de linha de comando da coleta de notícias.

Existe para o cron do GitHub Actions ter uma chamada estável e para o
desenvolvedor poder rodar o mesmo caminho na mão. Não há lógica aqui: o
que decide é o job; o que checa é ``core.noticias.saude``.

Código de saída
---------------
``0`` para ``success``, ``partial_success`` e ``skipped``. Coleta pulada por
cadência é o comportamento correto e marcar o job como vermelho por isso
treinaria qualquer um a ignorar a cor. ``1`` só para ``failed``.

``--destino``: a checagem que tem de vir antes da cota
------------------------------------------------------
O acervo mora no armazém local. Um agendador que não o alcança coleta, gasta
requisição de provedor e descarta tudo -- o job **avisa** (vira
``partial_success`` com "coleta não persistida"), mas a cota já foi. Gastar
para jogar fora é o defeito, e ele é evitável antes da primeira requisição:
``--destino`` verifica o destino sem tocar em nenhuma API e sai com ``1``
quando não há onde gravar. É o passo que roda antes da coleta no agendador.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SAIDA_OK = {"success", "partial_success", "skipped"}


def _bool(valor: str | None) -> bool:
    return str(valor or "").strip().lower() in {"1", "true", "yes", "sim"}


def _nivel(valor: str | None) -> int | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return max(0, min(4, int(texto)))
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coleta de noticias do APP4")
    parser.add_argument("--forcar", action="store_true",
                        help="ignora o freio de cadencia")
    parser.add_argument("--nivel", default=None,
                        help="nivel de crise a assumir (0-4)")
    parser.add_argument("--saude", action="store_true",
                        help="so verifica a saude dos servicos e sai")
    parser.add_argument("--destino", action="store_true",
                        help="so verifica se ha onde gravar o acervo e sai; "
                             "sai com 1 quando nao ha")
    args = parser.parse_args(argv)

    if args.destino:
        from core.noticias import saude

        v = saude.checar_acervo()
        print(v.descrever())
        # Este SIM reprova o passo, ao contrario de `--saude`: sem destino, a
        # coleta seguinte gastaria cota para descartar o resultado. O agendador
        # tem de parar aqui, e nao depois de pagar por isso.
        return 0 if v.ok else 1

    if args.saude:
        from core.noticias import saude

        verificacoes = saude.checar_tudo()
        for v in verificacoes:
            print(v.descrever())
        print(json.dumps(saude.resumo(verificacoes), ensure_ascii=False))
        # Saúde nunca reprova o passo: ela informa. Um provedor sem chave é
        # configuração do usuário, não defeito do pipeline.
        return 0

    from data_pipeline.jobs import update_noticias

    resultado = update_noticias.run(
        forcar=args.forcar or _bool(os.environ.get("FORCAR")),
        nivel=_nivel(args.nivel if args.nivel is not None
                     else os.environ.get("NIVEL")),
    )
    print(json.dumps(resultado, ensure_ascii=False, default=str))
    return 0 if resultado.get("status") in SAIDA_OK else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
