"""Constroi a base historica da Memoria de Mercado NO ARMAZEM LOCAL.

Este e o unico ponto do modulo que abre conexao com o armazem `dfu_warehouse`
(Postgres 16 em localhost:5433). A convencao do repositorio e que `core/` nao
abre o armazem por conta propria: quem constroi a engine e um script, e a
camada `core.memoria_mercado.repositorio` apenas recebe a engine pronta e
recusa qualquer destino que nao seja local.

Instrucao explicita desta entrega, e o motivo dela: o Supabase estava em 425 MB
de 500 MB. Um evento medido gera dezenas de campos por horizonte, por versao de
metodologia. Isso nao cabe la, e derrubar o Supabase derruba o app publicado.

Cobertura de precos, medida antes de escrever qualquer linha
------------------------------------------------------------
    market_us.prices_daily             13.342.783 linhas / 16.267 datas -> diaria
    market.fii_b3_security_history        606.552 linhas /  4.099 datas -> diaria
    market.historical_prices (acoes B3)   137.735 linhas /  1.542 datas -> NAO

1.542 datas distintas em 26 anos dao ~24 pregoes por ano ate 2013. Por isso o
portao de densidade de `core.memoria_mercado.serie` existe: para acoes da B3 os
horizontes de 1 e 5 pregoes saem NAO MEDIDOS, e nao estimados a partir de uma
serie que nao e diaria.

Nao existe serie utilizavel de indice no armazem (SPY e QQQ tem 9 linhas cada;
BOVA11 tem 220). O indice de referencia default e o equiponderado sintetico
construido do proprio painel, marcado como tal em cada evento.

Uso
---
    python scripts/construir_memoria_mercado.py --mercado us --eventos eventos.json
    python scripts/construir_memoria_mercado.py --mercado fii --do-banco-de-noticias
    python scripts/construir_memoria_mercado.py --mercado b3 --dry-run

O arquivo de eventos e uma lista JSON de objetos com, no minimo:
    {"chave": "...", "simbolo": "AAPL", "tipo_evento": "resultado_trimestral",
     "data": "2023-05-04", "setor": "Technology", "cenario": {...}}
`cenario` e opcional e usa as chaves DIM_* de core.memoria_mercado.similaridade.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.memoria_mercado import benchmark as bmk  # noqa: E402
from core.memoria_mercado import repositorio as repo  # noqa: E402
from core.memoria_mercado.retornos import HORIZONTES, medir_evento  # noqa: E402
from core.memoria_mercado.serie import SeriePrecos  # noqa: E402

logger = logging.getLogger("memoria_mercado.construir")

#: De onde sai a serie de precos de cada mercado. Os tres tem esquemas
#: diferentes porque vieram de ingestoes diferentes; unificar aqui e mais barato
#: que uma view no armazem, e deixa a diferenca visivel.
FONTES = {
    "us": {
        "sql": """
            SELECT ticker AS simbolo, price_date AS data,
                   close_price AS fechamento, volume
              FROM market_us.prices_daily
             WHERE ticker = ANY(:simbolos)
             ORDER BY ticker, price_date
        """,
        "descricao": "market_us.prices_daily",
        "diaria": True,
    },
    "fii": {
        "sql": """
            SELECT ticker AS simbolo, reference_date AS data,
                   close_price AS fechamento, volume
              FROM market.fii_b3_security_history
             WHERE ticker = ANY(:simbolos)
             ORDER BY ticker, reference_date
        """,
        "descricao": "market.fii_b3_security_history",
        "diaria": True,
    },
    "b3": {
        "sql": """
            SELECT ticker AS simbolo, price_date AS data,
                   close_price AS fechamento, volume
              FROM market.historical_prices
             WHERE ticker = ANY(:simbolos)
             ORDER BY ticker, price_date
        """,
        "descricao": "market.historical_prices",
        "diaria": False,
    },
}


def warehouse_url() -> str:
    """URL do armazem local, com a senha lida do container. Nunca logada."""
    inspecao = subprocess.run(
        ["docker", "inspect", "dfu_warehouse", "--format", "{{json .Config.Env}}"],
        check=True, capture_output=True, text=True,
    )
    ambiente = json.loads(inspecao.stdout)
    entrada = next((i for i in ambiente
                    if str(i).startswith("POSTGRES_PASSWORD=")), None)
    if not entrada:
        raise RuntimeError("senha do warehouse local indisponivel")
    senha = str(entrada).split("=", 1)[1]
    return f"postgresql://postgres:{quote_plus(senha)}@localhost:5433/postgres"


def carregar_eventos_de_arquivo(caminho: Path) -> list[dict]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(dados, list):
        raise ValueError("o arquivo de eventos deve conter uma lista JSON")
    return [dict(d) for d in dados]


def carregar_eventos_do_banco_de_noticias(engine_noticias) -> list[dict]:
    """Le eventos do backend de noticias ja existente, se ele estiver povoado.

    A leitura e do banco configurado em `core.database` — a ESCRITA continua
    sendo so no armazem local. Ler de la e legitimo; gravar nao.
    """
    sql = """
        SELECT i.id_dedup AS chave, i.tipo_evento,
               COALESCE(i.publicado_em, i.coletado_em) AS data,
               i.entidades
          FROM noticias_itens i
         WHERE i.tipo_evento IS NOT NULL
         ORDER BY 3
    """
    saida: list[dict] = []
    with engine_noticias.begin() as conn:
        for linha in conn.execute(text(sql)).mappings():
            entidades = linha["entidades"] or {}
            if isinstance(entidades, str):
                entidades = json.loads(entidades)
            for ticker in (entidades.get("tickers") or []):
                saida.append({
                    "chave": f"{linha['chave']}:{ticker}",
                    "simbolo": str(ticker).upper(),
                    "tipo_evento": linha["tipo_evento"],
                    "data": str(linha["data"])[:10],
                })
    return saida


def carregar_series(engine, mercado: str, simbolos) -> dict[str, SeriePrecos]:
    fonte = FONTES[mercado]
    alvo = sorted({str(s).upper() for s in simbolos})
    if not alvo:
        return {}
    por_simbolo: dict[str, list[tuple]] = defaultdict(list)
    with engine.begin() as conn:
        for linha in conn.execute(text(fonte["sql"]), {"simbolos": alvo}).mappings():
            por_simbolo[str(linha["simbolo"]).upper()].append(
                (linha["data"], linha["fechamento"], linha["volume"]))
    return {s: SeriePrecos.de_pares(s, pares, fonte=fonte["descricao"])
            for s, pares in por_simbolo.items()}


def construir(engine, *, mercado: str, eventos: list[dict],
              horizontes=HORIZONTES, modelo: str = bmk.MODELO_DIFERENCA,
              minimo_ativos_indice: int = 20) -> dict:
    """Mede todos os eventos e devolve `(medidos, cenarios, relatorio)`."""
    simbolos = {str(e.get("simbolo", "")).upper() for e in eventos}
    simbolos.discard("")
    series = carregar_series(engine, mercado, simbolos)

    indice = bmk.indice_equiponderado(
        list(series.values()), nome=f"{mercado}_equiponderado",
        minimo_ativos=minimo_ativos_indice)
    sintetico = not indice.vazia

    medidos = []
    cenarios: dict[str, dict] = {}
    sem_serie: list[str] = []
    sem_pregao: list[str] = []

    for bruto in eventos:
        simbolo = str(bruto.get("simbolo", "")).upper()
        serie = series.get(simbolo)
        if serie is None or serie.vazia:
            sem_serie.append(simbolo)
            continue
        evento = medir_evento(
            chave=str(bruto.get("chave") or f"{simbolo}:{bruto.get('data')}"),
            simbolo=simbolo,
            tipo_evento=str(bruto.get("tipo_evento") or "indefinido"),
            data_evento=bruto.get("data"),
            ativo=serie,
            indice=(indice if sintetico else None),
            modelo=modelo,
            horizontes=tuple(horizontes),
            setor=bruto.get("setor"),
        )
        if evento is None:
            sem_pregao.append(str(bruto.get("chave")))
            continue
        # `medir_evento` ja marca benchmark e benchmark_sintetico a partir da
        # `fonte` da serie do indice; nao ha nada a corrigir aqui.
        medidos.append(evento)
        if bruto.get("cenario"):
            cenarios[evento.chave] = dict(bruto["cenario"])

    relatorio = {
        "mercado": mercado,
        "fonte_precos": FONTES[mercado]["descricao"],
        "serie_diaria": FONTES[mercado]["diaria"],
        "eventos_recebidos": len(eventos),
        "eventos_medidos": len(medidos),
        "sem_serie_de_precos": len(sem_serie),
        "sem_pregao_na_data": len(sem_pregao),
        "indice_sintetico": sintetico,
        "pregoes_do_indice": len(indice),
        "cenarios": len(cenarios),
    }
    if not sintetico:
        relatorio["aviso_indice"] = (
            "nenhum indice construido: menos ativos que o minimo por pregao; "
            "todos os eventos ficam sem retorno anormal")
    if not FONTES[mercado]["diaria"]:
        relatorio["aviso_densidade"] = (
            "serie de precos nao e diaria: horizontes curtos sairao nao "
            "medidos pelo portao de densidade")
    return {"medidos": medidos, "cenarios": cenarios, "relatorio": relatorio}


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mercado", choices=sorted(FONTES), required=True)
    parser.add_argument("--eventos", type=Path,
                        help="arquivo JSON com a lista de eventos")
    parser.add_argument("--do-banco-de-noticias", action="store_true",
                        help="le eventos das tabelas do Motor Conjuntural")
    parser.add_argument("--modelo", choices=list(bmk.MODELOS),
                        default=bmk.MODELO_DIFERENCA)
    parser.add_argument("--limpar-tipo", default=None,
                        help="apaga todas as safras de um tipo antes de gravar")
    parser.add_argument("--dry-run", action="store_true",
                        help="mede e relata, sem gravar")
    args = parser.parse_args()

    eventos: list[dict] = []
    if args.eventos:
        eventos.extend(carregar_eventos_de_arquivo(args.eventos))
    if args.do_banco_de_noticias:
        from core.database import get_engine
        engine_noticias = get_engine()
        if engine_noticias is None:
            logger.error("banco de noticias nao configurado")
            return 2
        eventos.extend(carregar_eventos_do_banco_de_noticias(engine_noticias))

    if not eventos:
        logger.error("nenhum evento informado: use --eventos ou "
                     "--do-banco-de-noticias")
        return 2

    engine = create_engine(warehouse_url(), pool_pre_ping=True)
    repo.exigir_local(engine)   # cinto e suspensorio: recusa destino remoto

    resultado = construir(engine, mercado=args.mercado, eventos=eventos,
                          modelo=args.modelo)
    relatorio = resultado["relatorio"]

    if args.dry_run:
        relatorio["gravado"] = False
        logger.info("dry-run: nada gravado")
    else:
        if args.limpar_tipo:
            relatorio["linhas_removidas"] = repo.limpar_tipo(engine,
                                                             args.limpar_tipo)
        relatorio.update(repo.gravar(resultado["medidos"], engine,
                                     cenarios=resultado["cenarios"]))

    logger.info("%s", json.dumps(relatorio, ensure_ascii=False, sort_keys=True,
                                 default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
