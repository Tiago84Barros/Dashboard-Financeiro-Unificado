"""Constroi a base historica da Memoria de Mercado NO ARMAZEM LOCAL.

Este e o unico ponto do modulo que abre conexao com o armazem `dfu_warehouse`
(Postgres 16 em localhost:5433). A convencao do repositorio e que `core/` nao
abre o armazem por conta propria: quem constroi a engine e um script, e a
camada `core.memoria_mercado.repositorio` apenas recebe a engine pronta e
recusa qualquer destino que nao seja local.

Instrucao explicita desta entrega, e o motivo dela: o Supabase estava em 425 MB
de 500 MB. Um evento medido gera dezenas de campos por horizonte, por versao de
metodologia. Isso nao cabe la, e derrubar o Supabase derruba o app publicado.

Cobertura de precos, medida em 02/09/2026
----------------------------------------
    market_us.prices_daily             13.342.783 linhas / 16.267 datas -> diaria
    market.fii_b3_security_history        606.552 linhas /  4.099 datas -> diaria
    market.b3_security_history          1.627.752 linhas /  4.134 datas -> diaria

A terceira linha era `market.historical_prices`, com 137.735 linhas em 1.542
datas -- ~24 pregoes por ano ate 2013. Nela os horizontes de 1 e 5 pregoes de
acoes da B3 saiam NAO MEDIDOS, porque o portao de densidade de
`core.memoria_mercado.serie` recusa chamar de "1 pregao" o intervalo entre duas
observacoes mensais. A serie diaria veio do COTAHIST oficial em 02/09/2026
(`data_pipeline/market/b3_precos.py`); o portao continua onde estava, e agora
aprova. Leia-se `close_unitario`, nao `close`: o COTAHIST cota lotes, e o
fechamento cru erra por 1000x nos papeis com FATCOT=1000.

Nao existe serie utilizavel de indice no armazem (SPY e QQQ tem 9 linhas cada;
BOVA11 tem 220). O indice de referencia default e o equiponderado sintetico
construido do proprio painel, marcado como tal em cada evento.

Uso
---
    python scripts/construir_memoria_mercado.py --mercado us --eventos eventos.json
    python scripts/construir_memoria_mercado.py --mercado fii --do-banco-de-noticias
    python scripts/construir_memoria_mercado.py --mercado b3 --do-catalogo --dry-run

`--do-banco-de-noticias` nao produz safra hoje, e nao e defeito: em 05/09/2026 o
acervo local tinha 48 itens, todos de 03 a 05/09/2026. Medir reacao exige preco
DEPOIS do evento. `--do-catalogo` existe para isso: as fontes historicas datadas de
`core.calibracao.catalogo`, entre elas as 10.829 publicacoes DFP da CVM entre
2011 e 2026 -- data de entrega real e preco diario ao lado.

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

from core.calibracao import catalogo as cat  # noqa: E402
from core.memoria_mercado import benchmark as bmk  # noqa: E402
from core.memoria_mercado import destino as dst  # noqa: E402
from core.memoria_mercado import repositorio as repo  # noqa: E402
from core.memoria_mercado import similaridade as sim  # noqa: E402
from core.memoria_mercado.retornos import HORIZONTES, medir_evento  # noqa: E402
from core.memoria_mercado.serie import SeriePrecos  # noqa: E402

logger = logging.getLogger("memoria_mercado.construir")

#: De onde sai a serie de precos de cada mercado. Os tres tem esquemas
#: diferentes porque vieram de ingestoes diferentes; unificar aqui e mais barato
#: que uma view no armazem, e deixa a diferenca visivel.
FONTES = {
    "us": {
        "sql": """
            SELECT symbol AS simbolo, date AS data,
                   close AS fechamento, volume
              FROM market_us.prices_daily
             WHERE symbol = ANY(:simbolos)
             ORDER BY symbol, date
        """,
        "descricao": "market_us.prices_daily",
        "diaria": True,
    },
    "fii": {
        "sql": """
            SELECT ticker AS simbolo, trade_date AS data,
                   close AS fechamento, quantity AS volume
              FROM market.fii_b3_security_history
             WHERE ticker = ANY(:simbolos)
             ORDER BY ticker, trade_date
        """,
        "descricao": "market.fii_b3_security_history",
        "diaria": True,
    },
    "b3": {
        "sql": """
            SELECT ticker AS simbolo, trade_date AS data,
                   close_unitario AS fechamento, quantity AS volume
              FROM market.b3_security_history
             WHERE ticker = ANY(:simbolos)
             ORDER BY ticker, trade_date
        """,
        "descricao": "market.b3_security_history",
        "diaria": True,
    },
}


#: Colunas que :func:`carregar_series` le de qualquer FONTE. A consulta pode
#: mudar de tabela; os quatro apelidos, nao.
COLUNAS_EXIGIDAS = ("simbolo", "data", "fechamento", "volume")


def verificar_fonte(engine, mercado: str) -> dict:
    """Executa a consulta da fonte contra o banco antes de usa-la.

    Existe por um defeito real: as tres consultas de :data:`FONTES` nasceram
    escritas de memoria e **nenhuma das tres rodava** -- `price_date`,
    `close_price` e `reference_date` nao existem em lugar nenhum do armazem.
    O erro so apareceria na primeira execucao de verdade, depois de baixar
    eventos e montar o indice.

    A checagem custa uma consulta com lista vazia de simbolos: nao devolve
    linha, mas o Postgres resolve tabela e colunas na hora de planejar. Se a
    tabela sumiu ou uma coluna trocou de nome, falha aqui, com o nome do
    mercado no erro.
    """
    fonte = FONTES[mercado]
    with engine.begin() as conn:
        resultado = conn.execute(text(fonte["sql"]), {"simbolos": []})
        entregues = set(resultado.keys())
    faltando = [c for c in COLUNAS_EXIGIDAS if c not in entregues]
    if faltando:
        raise RuntimeError(
            f"fonte de precos de '{mercado}' ({fonte['descricao']}) nao "
            f"entrega {', '.join(faltando)}; entrega {sorted(entregues)}")
    return {"fonte": fonte["descricao"], "colunas": sorted(entregues)}


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


def carregar_eventos_do_catalogo(engine, *, mercado: str,
                                 tipos: tuple[str, ...] | None = None,
                                 so_data_de_anuncio: bool = True
                                 ) -> tuple[list[dict], tuple[str, ...]]:
    """Eventos historicos datados, lidos de :mod:`core.calibracao.catalogo`.

    Este script nao tem consulta propria a fonte de evento, e isso e desenho.
    O catalogo ja e o lugar onde mora "que tabela do armazem da um evento com
    data ponto-no-tempo, e o que ela nao consegue provar" -- ele inclusive
    recusa, em :func:`catalogo.cobertura`, um tipo da taxonomia que nao esteja
    declarado nem como fonte nem como ausencia. Uma segunda copia da mesma
    consulta aqui seria ``memoria: guarda-duplicada-diverge``: duas listas de
    fontes que comecam iguais e divergem na primeira correcao aplicada so numa
    delas.

    ``so_data_de_anuncio`` e True por padrao, e e o oposto do padrao do
    catalogo. A safra responde "como o mercado reagiu a uma noticia deste
    tipo", e uma fonte datada por `ex_date` responde "quanto o preco caiu no
    dia em que o provento saiu do preco" -- aritmetica, nao reacao. As duas
    bases teriam a mesma cara e o portao quantitativo compararia a noticia
    contra a populacao errada sem nada quebrar. O catalogo mantem essas fontes
    porque elas calibram o efeito que de fato medem; quem muda e o pedido.

    As ressalvas da fonte sobem junto como limitacoes. Elas nao sao decoracao:
    e o que impede alguem de ler a safra como se todas as linhas valessem o
    mesmo -- a fonte anual da B3, por exemplo, herda o vies de sobrevivencia de
    ``market.ticker_cvm``, que e o mapa de hoje.
    """
    montado = cat.montar(engine, tipos=set(tipos) if tipos else None,
                         so_data_de_anuncio=so_data_de_anuncio)
    eventos = [e for e in montado["eventos"] if e.get("mercado") == mercado]
    for evento in eventos:
        # So a dimensao que esta fonte realmente conhece. As demais de
        # ``similaridade.DIMENSOES`` (juros, cambio, valuation...) ficam
        # ausentes de proposito: ``None`` significa nao medido, e inventar aqui
        # um valor plausivel faria a busca por evento parecido casar por um
        # numero que ninguem observou.
        evento["cenario"] = {sim.DIM_TIPO_EVENTO: evento["tipo_evento"]}
    logger.info("catalogo: %d eventos do mercado '%s'", len(eventos), mercado)
    return eventos, tuple(montado["limitacoes"])


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
    parser.add_argument("--do-catalogo", action="store_true",
                        help="le as fontes historicas de core.calibracao.catalogo")
    parser.add_argument("--tipos", default=None,
                        help="tipos de evento do catalogo, separados por virgula")
    parser.add_argument("--incluir-data-mecanica", action="store_true",
                        help="inclui fontes datadas por efeito mecanico "
                             "(ex_date, delisted_date); fora por padrao")
    parser.add_argument("--modelo", choices=list(bmk.MODELOS),
                        default=bmk.MODELO_DIFERENCA)
    parser.add_argument("--limpar-tipo", default=None,
                        help="apaga todas as safras de um tipo antes de gravar")
    parser.add_argument("--dry-run", action="store_true",
                        help="mede e relata, sem gravar")
    args = parser.parse_args()

    if args.tipos and not args.do_catalogo:
        logger.error("--tipos so faz sentido com --do-catalogo")
        return 2

    # A engine do armazem sobe antes da coleta de eventos porque o catalogo le
    # do MESMO armazem de onde sairao os precos. Abrir uma segunda engine para
    # a mesma URL seria pedir duas vezes a senha ao container e dobrar conexao
    # sem ganhar nada.
    engine = create_engine(warehouse_url(), pool_pre_ping=True)
    repo.exigir_local(engine)   # cinto e suspensorio: recusa destino remoto

    # A safra NAO e gravada necessariamente onde o preco e lido. Quem decide o
    # endereco e `core.memoria_mercado.destino.url_memoria` -- a mesma funcao
    # que `core.noticias.bases_historicas` chama para ler. Foi a divergencia
    # entre os dois (armazem `postgres` na gravacao, banco `noticias` na
    # leitura) que fez 4.463 eventos medidos conviverem com "sem safra
    # construida". Ver o docstring daquele modulo.
    destino = dst.engine_memoria() or engine
    repo.exigir_local(destino)
    if str(destino.url) != str(engine.url):
        logger.info("precos lidos de %s; safra gravada em %s",
                    engine.url.database, destino.url.database)

    eventos: list[dict] = []
    if args.eventos:
        eventos.extend(carregar_eventos_de_arquivo(args.eventos))
    limitacoes_da_fonte: tuple[str, ...] = ()
    if args.do_catalogo:
        tipos = tuple(t.strip() for t in args.tipos.split(",")) if args.tipos else None
        do_catalogo, limitacoes_da_fonte = carregar_eventos_do_catalogo(
            engine, mercado=args.mercado, tipos=tipos,
            so_data_de_anuncio=not args.incluir_data_mecanica)
        eventos.extend(do_catalogo)
    if args.do_banco_de_noticias:
        # O acervo mudou de casa em 04/09/2026 (commit 61c39e8): ele nao cabe
        # no Supabase -- sao ~22 MB por janela contra 23 MB de folga -- e
        # passou a morar no armazem local. Este script continuava lendo
        # ``get_engine()``, que aponta para a producao: media a fonte errada e
        # devolvia zero eventos sem erro nenhum. O Supabase fica como fallback
        # porque a safra antiga ainda pode estar la.
        from core.noticias.destino import engine_acervo

        engine_noticias = engine_acervo()
        if engine_noticias is None:
            from core.database import get_engine
            engine_noticias = get_engine()
            logger.warning("acervo local indisponivel: lendo o banco remoto")
        if engine_noticias is None:
            logger.error("banco de noticias nao configurado")
            return 2
        eventos.extend(carregar_eventos_do_banco_de_noticias(engine_noticias))

    if not eventos:
        logger.error("nenhum evento informado: use --eventos, --do-catalogo "
                     "ou --do-banco-de-noticias")
        return 2

    try:
        verificar_fonte(engine, args.mercado)
    except Exception as erro:
        logger.error("%s", erro)
        return 3

    resultado = construir(engine, mercado=args.mercado, eventos=eventos,
                          modelo=args.modelo)
    relatorio = resultado["relatorio"]
    if limitacoes_da_fonte:
        # As ressalvas viajam no relatorio, e nao so no docstring do catalogo:
        # quem le o JSON da rodada precisa ver que o universo desta safra e
        # sobrevivente antes de citar o numero.
        relatorio["limitacoes_da_fonte"] = list(limitacoes_da_fonte)

    if args.dry_run:
        relatorio["gravado"] = False
        logger.info("dry-run: nada gravado")
    else:
        if args.limpar_tipo:
            relatorio["linhas_removidas"] = repo.limpar_tipo(destino,
                                                             args.limpar_tipo)
        relatorio.update(repo.gravar(resultado["medidos"], destino,
                                     cenarios=resultado["cenarios"]))

    logger.info("%s", json.dumps(relatorio, ensure_ascii=False, sort_keys=True,
                                 default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
