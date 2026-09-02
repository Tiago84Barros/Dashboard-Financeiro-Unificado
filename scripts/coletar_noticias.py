"""Coleta manual de notícias, para rodar na mão e inspecionar o resultado.

Por padrão **não grava nada**: coleta, avalia e imprime. Gravar exige
``--gravar``, explicitamente. É o mesmo princípio do resto do repositório --
escrita em banco não acontece por efeito colateral de um comando de leitura.

    python scripts/coletar_noticias.py --tickers PETR4,VALE3
    python scripts/coletar_noticias.py --forcar --gravar
    python scripts/coletar_noticias.py --situacao
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import settings  # noqa: E402
from core.noticias import taxonomia  # noqa: E402
from core.noticias.cache import Cache  # noqa: E402
from core.noticias.coleta import coletar  # noqa: E402
from core.noticias.frescor_noticias import RegistroColeta, formatar_idade  # noqa: E402
from core.noticias.provedores.base import Consulta  # noqa: E402
from core.noticias.provedores.registro import construir, descrever  # noqa: E402
from core.noticias.rate_limit import LIMITES_PADRAO, Orcamento  # noqa: E402

logger = logging.getLogger("coletar_noticias")


def _situacao() -> int:
    print("Provedores configurados (NOTICIAS_PROVEDORES):")
    for s in descrever():
        marca = "ok " if s.disponivel else "-- "
        motivo = f" ({s.motivo})" if s.motivo else ""
        print(f"  {marca}{s.nome}{motivo}")

    orcamento = Orcamento()
    print("\nCota restante hoje:")
    for familia in LIMITES_PADRAO:
        restante = orcamento.restante(familia)
        print(f"  {familia}: {restante}")

    registro = RegistroColeta()
    print("\nUltima coleta bem-sucedida:")
    provedores = registro.provedores()
    if not provedores:
        print("  nenhuma registrada ate agora")
    for nome in provedores:
        estado = registro.estado(
            nome, cadencia_minutos=settings.noticias_freq_normal_min)
        print(f"  {nome}: {estado.texto()}")
    return 0


def _imprimir(resultado) -> None:
    print(f"\nColetado em {resultado.coletado_em.isoformat()}")
    print(f"Provedores consultados: {', '.join(resultado.provedores_consultados) or '-'}")
    print(f"Provedores que responderam: {', '.join(resultado.provedores_ok) or '-'}")
    for falha in resultado.falhas:
        print(f"  FALHA {falha.texto()}")
    print(f"Itens brutos: {resultado.itens_brutos} | "
          f"apos deduplicacao: {len(resultado.avaliadas)} | "
          f"eventos: {len(resultado.eventos)}")

    for limitacao in resultado.limitacoes:
        print(f"  LIMITACAO {limitacao}")

    if resultado.sem_fonte:
        print("\nNenhum provedor respondeu. A lista abaixo NAO reflete o "
              "momento atual.")

    print("")
    for avaliada in resultado.avaliadas[:30]:
        n = avaliada.noticia
        rel = avaliada.relevancia
        dominio = n.fonte.dominio if n.fonte else "?"
        quando = (n.publicado_em.strftime("%d/%m %H:%M")
                  if n.publicado_em else "sem data")
        idade = formatar_idade(n.idade_em_minutos())
        print(f"[{rel.nota:5.1f}] {taxonomia.ROTULO_FAIXA.get(rel.faixa, rel.faixa):<12} "
              f"{n.tipo.rotulo:<24} {dominio:<24} {quando} ({idade})")
        print(f"        {n.titulo[:110]}")
        print(f"        {rel.texto_cobertura()}")
        print(f"        {avaliada.impacto.texto()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default="",
                        help="lista separada por virgula")
    parser.add_argument("--temas", default="", help="lista separada por virgula")
    parser.add_argument("--limite", type=int, default=None)
    parser.add_argument("--forcar", action="store_true",
                        help="ignora o cache e a cadencia")
    parser.add_argument("--gravar", action="store_true",
                        help="persiste no banco (padrao: nao grava)")
    parser.add_argument("--situacao", action="store_true",
                        help="apenas mostra provedores, cota e frescor")
    parser.add_argument("--verboso", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verboso else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")

    if args.situacao:
        return _situacao()

    provedores = construir(
        orcamento=Orcamento(),
        # --forcar zera o TTL: a chamada vai à rede de qualquer jeito, mas a
        # cota continua valendo. Forçar não é furar o limite da API.
        cache=Cache(ttl_s=0.0 if args.forcar else settings.noticias_cache_ttl_s),
    )
    if not provedores:
        print("Nenhum provedor disponivel. Rode com --situacao para ver o que "
              "falta configurar.")
        return 2

    consulta = Consulta(
        tickers=tuple(t.strip().upper() for t in args.tickers.split(",") if t.strip()),
        temas=tuple(t.strip() for t in args.temas.split(",") if t.strip()),
        limite=args.limite or settings.noticias_limite,
    )

    resultado = coletar(consulta, provedores, registro=RegistroColeta())
    _imprimir(resultado)

    if args.gravar:
        from core.noticias.armazenamento import gravar
        print("\n" + str(gravar(resultado)))
    else:
        print("\n(nada gravado; use --gravar para persistir)")

    return 0 if resultado.provedores_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
