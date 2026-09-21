"""
data_pipeline/importers/investments/b3_posicao_detalhada.py
==========================================================
Importa o extrato "Posicao Detalhada" da Area do Investidor da B3.

O que este arquivo e
--------------------
Uma FOTO da carteira num instante, nao um evento. Por isso grava em
`portfolio_position_snapshots`, como o Consolidado da XP -- e nunca em
`portfolio_positions`, que `positions.recompute_for_user()` recalcula do zero
a partir de `investment_transactions` depois de cada importacao: um snapshot
escrito la seria apagado na importacao seguinte, sem erro visivel.

A data da foto vem de DENTRO do arquivo (celula "Conta: ... | 21/09/2026,
18:45"), nao do nome. Isso importa porque `report_date` entra na chave unica
do snapshot: renomear o arquivo nao pode mudar a identidade da foto, e um
arquivo sem data no cabecalho e recusado em vez de cair em `date.today()` --
o que faria todo o historico colapsar numa foto so.

Uma aba, sete secoes
--------------------
O extrato e uma unica planilha ("Sua carteira") com secoes heterogeneas
empilhadas, cada uma com suas proprias colunas: Acoes tem "Qtd. total" na
coluna G, Fundos Imobiliarios nao tem coluna de quantidade nenhuma, e o Saldo
dos FIIs esta na coluna D. Ler por indice de coluna quebraria na primeira
secao nova. O parser le o CABECALHO de cada secao e mapeia rotulo -> valor.

Regras de entrada
-----------------
1. **Nada sem quantidade entra.** Uma posicao sem quantidade nao e posicao:
   e um saldo do qual nao se sabe o tamanho, e o que fosse gravado em
   `quantity` (obrigatoria no schema) seria invencao. Duas secoes caem aqui --
   Renda Fixa (CDB) e Fundos de Investimentos (FIP) --, porque o extrato nao
   publica nem quantidade nem preco unitario delas. Elas saem contadas em
   `rows_skipped`, com o motivo, em vez de sumirem caladas.

   Os Fundos Imobiliarios sao a excecao aparente: nao tem coluna de
   quantidade, mas tem Saldo e Ultima cotacao, e a divisao cai em inteiro
   exato nos seis fundos (3.846,60 / 106,85 = 36). Ai a quantidade e
   RECUPERADA, nao inventada -- e a linha so entra se o resultado for inteiro
   dentro de `_TOL_COTAS`. Se um dia a divisao nao fechar, a linha e recusada
   pelo mesmo criterio.

2. **Quantidade zero nao e posicao.** Ativo vendido continua aparecendo no
   extrato com saldo e quantidade zero (CSMG3, MBRF3, HGRE11 em 21/09/2026),
   carregando preco medio e rentabilidade historicos. Nao ha lista de tickers
   aqui de proposito: a regra e `quantity != 0`, e ela nao envelhece.

3. **Custodia Remunerada nao entra.** A secao repete acoes que ja estao em
   "Acoes" com a MESMA quantidade -- BBAS3 1.479 e PETR3 109 aparecem nas
   duas --, porque e a mesma posicao vista por outro angulo, nao uma posicao
   adicional. Importar as duas dobraria R$ 34 mil de BBAS3 no patrimonio.

4. **Provento provisionado nao e provento.** Vai para
   `portfolio_provisioned_income` (migration 073), nunca para `dividends`:
   e valor declarado com previsao de pagamento em 2026-2027, e somar com o
   recebido inflaria o rendimento realizado de toda tela que le `dividends`.

Puro ate a borda do banco: o parsing da planilha nao toca engine e e testavel
com um workbook em memoria.
"""
from __future__ import annotations

import io
import logging
import re
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from core.config import settings

from .common import (
    ensure_external_id_columns,
    finalize_summary,
    get_or_create_asset,
    make_external_id,
    make_summary,
    parse_date_br,
    safe_error,
    to_float_br,
)
from .xlsx_probe import norm as _norm
from .xp_consolidado import _ensure_portfolio, _insert_snapshot, _tesouro_ticker

logger = logging.getLogger(__name__)

SOURCE = "b3_posicao_detalhada"
SOURCE_TABLE = "b3_posicao_detalhada"
INSTITUTION = "B3 - Area do Investidor"
SHEET_NAME = "SUA CARTEIRA"

# Folga na recuperacao da quantidade de FII por Saldo / Ultima cotacao.
# Os dois lados vem arredondados a centavos, entao o quociente nao cai em
# inteiro exato em ponto flutuante mesmo quando a cota e inteira. 1e-4 aceita
# o arredondamento e ainda recusa qualquer fracao real de cota.
_TOL_COTAS = 1e-4

# Cabecalho de secao: "36,6% | Renda Variavel Brasil", "-0% | Renda Variavel
# Brasil". O que identifica nao e o texto, e a forma percentual seguida de "|".
_RE_SUBSECAO = re.compile(r"^\s*-?[\d.,]+\s*%\s*\|")

# Data da foto, dentro do cabecalho: "Conta: undefined | 21/09/2026, 18:45".
_RE_DATA_FOTO = re.compile(r"(\d{2}/\d{2}/\d{4})")

# Evento do provento, como o extrato escreve. `income_type` e derivado; o
# texto cru fica guardado tambem, porque a B3 abrevia de forma inconsistente
# ("DIVI" e "DIVIDENDO" no mesmo arquivo) e normalizar destroi a evidencia.
# Ordem importa: "JUROS SOBRE CAPITAL" antes de "JURO", "DIVIDENDO" antes de
# "DIVI" -- o marcador mais curto casaria com o mais longo.
_EVENTO_TIPO: list[tuple[str, str]] = [
    ("JUROS SOBRE CAPITAL", "jcp"),
    ("JURO", "jcp"),
    ("DIVIDENDO", "dividend"),
    ("DIVI", "dividend"),
    ("RENDIMENTO", "reit_income"),
    ("AMORTIZACAO", "amortization"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Leitura da planilha (pura)
# ─────────────────────────────────────────────────────────────────────────────

def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


# Milhar sem decimal: "1.479", "4.567". Os valores monetarios do extrato
# sempre trazem centavos ("R$ 34.017,00"), mas as QUANTIDADES nao -- e
# `to_float_br` le "1.479" como 1,479, porque ponto sem virgula e decimal na
# convencao dela. BBAS3 entraria com 1,479 acoes e nenhum erro apareceria:
# o numero e valido, so esta mil vezes menor.
_RE_MILHAR_SEM_DECIMAL = re.compile(r"^-?\d{1,3}(\.\d{3})+$")


def numero_br(valor: Any) -> float | None:
    """`to_float_br` com o caso do milhar sem casa decimal resolvido."""
    if isinstance(valor, str):
        limpo = valor.replace("R$", "").replace("\xa0", "").strip()
        if _RE_MILHAR_SEM_DECIMAL.match(limpo):
            return float(limpo.replace(".", ""))
    return to_float_br(valor)


def _vazia(linha: tuple) -> bool:
    return all(not _texto(v) for v in linha)


def extrair_data_foto(linhas: list[tuple]) -> date | None:
    """Data do extrato, do cabecalho. None se o arquivo nao declarar uma.

    Varre so o topo: a data aparece na primeira linha, e mais abaixo o arquivo
    esta cheio de datas de vencimento e de previsao de pagamento que casariam
    com o mesmo padrao.
    """
    for linha in linhas[:3]:
        for valor in linha:
            achado = _RE_DATA_FOTO.search(_texto(valor))
            if achado:
                return parse_date_br(achado.group(1))
    return None


def secoes(linhas: list[tuple]) -> list[dict]:
    """Quebra a aba em secoes {bloco, categoria, subsecao, colunas, linhas}.

    Tres formas de linha estrutural, distinguidas pela forma e nao pelo texto:

      - so a coluna A preenchida  -> titulo de BLOCO ("Proventos",
        "Custodia Remunerada"). E o que separa carteira de provisao.
      - coluna A + um unico valor no resto -> titulo de CATEGORIA, com o total
        da categoria ("Acoes" ... "R$ 109.127,53").
      - coluna A em forma percentual "NN% | Rotulo" -> CABECALHO de colunas.
        Da linha seguinte ate a proxima linha em branco sao dados.
    """
    resultado: list[dict] = []
    bloco = "CARTEIRA"
    categoria = ""
    atual: dict | None = None

    for linha in linhas:
        if _vazia(linha):
            atual = None
            continue

        primeira = _texto(linha[0])
        resto = [_texto(v) for v in linha[1:]]

        if primeira and _RE_SUBSECAO.match(primeira):
            rotulo = primeira.split("|", 1)[1].strip() if "|" in primeira else ""
            atual = {
                "bloco": bloco,
                "categoria": categoria,
                "subsecao": rotulo,
                # rotulo normalizado -> indice da coluna
                "colunas": {
                    _norm(_texto(v)): i
                    for i, v in enumerate(linha)
                    if i > 0 and _texto(v)
                },
                "linhas": [],
            }
            resultado.append(atual)
            continue

        preenchidos = [v for v in resto if v]
        if primeira and not preenchidos:
            bloco = _norm(primeira)
            categoria = ""
            atual = None
            continue
        if primeira and len(preenchidos) == 1:
            categoria = _norm(primeira)
            atual = None
            continue

        if atual is not None and primeira:
            atual["linhas"].append(linha)

    return resultado


def _campo(secao: dict, linha: tuple, *rotulos: str) -> Any:
    """Valor da linha sob o primeiro rotulo de cabecalho que existir."""
    for rotulo in rotulos:
        idx = secao["colunas"].get(_norm(rotulo))
        if idx is not None and idx < len(linha):
            return linha[idx]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Traducao secao -> posicao
# ─────────────────────────────────────────────────────────────────────────────

def cotas_por_divisao(saldo: float | None, cotacao: float | None):
    """Recupera a quantidade de cotas por Saldo / cotacao.

    Devolve (quantidade, motivo). `motivo` preenchido significa recusa: a
    divisao nao caiu em inteiro, entao o extrato nao permite afirmar a
    quantidade e a linha nao entra.
    """
    if saldo is None or not cotacao:
        return None, "sem saldo ou cotacao para recuperar a quantidade"
    bruto = saldo / cotacao
    inteiro = round(bruto)
    if abs(bruto - inteiro) > _TOL_COTAS:
        return None, (
            f"saldo/cotacao = {bruto:.6f}, que nao e inteiro: a quantidade "
            f"nao pode ser afirmada"
        )
    return float(inteiro), ""


def posicao(secao: dict, linha: tuple):
    """Traduz uma linha de dados em posicao, ou devolve o motivo da recusa.

    Devolve (pos, motivo). Exatamente um dos dois vem preenchido.
    """
    primeira = _texto(linha[0])
    categoria = secao["categoria"]
    subsecao = _norm(secao["subsecao"])

    saldo = numero_br(
        _campo(secao, linha, "Saldo", "Saldo a mercado", "Valor total")
    )

    if categoria.startswith("ACOES"):
        qtd = numero_br(_campo(secao, linha, "Qtd. total", "Qtd."))
        preco = numero_br(
            _campo(secao, linha, "Ultimo preco (R$)", "Ultimo preco")
        )
        medio = numero_br(_campo(secao, linha, "Preco medio"))
        tipo = "etf" if "ALTERNATIVO" in subsecao else "stock"
        ticker = nome = primeira.upper()

    elif categoria.startswith("FUNDOS IMOBILIARIOS"):
        preco = numero_br(_campo(secao, linha, "Ultima cotacao"))
        medio = numero_br(_campo(secao, linha, "Preco medio (abertura)"))
        qtd, motivo = cotas_por_divisao(saldo, preco)
        if motivo:
            return None, motivo
        tipo = "fii"
        ticker = nome = primeira.upper()

    elif categoria.startswith("ALUGUEL"):
        qtd = numero_br(_campo(secao, linha, "Qtd.", "Qtd. total"))
        preco = numero_br(_campo(secao, linha, "Ultima cotacao"))
        medio = None
        # "Tomador" e posicao tomada em emprestimo: o saldo vem negativo no
        # extrato, e a quantidade tem que acompanhar o sinal. Guardar 17
        # positivo ao lado de um saldo -124,10 quebraria a identidade
        # quantidade x preco = valor e somaria uma posicao comprada que nao
        # existe.
        if qtd is not None and saldo is not None and saldo < 0:
            qtd = -abs(qtd)
        tipo = "stock"
        ticker = nome = primeira.upper()

    elif categoria.startswith("TESOURO DIRETO"):
        qtd = numero_br(_campo(secao, linha, "Quantidade", "Disponivel"))
        if qtd is None or qtd == 0:
            return None, "sem quantidade"
        vencimento = _campo(secao, linha, "Vencimento")
        # Mesma funcao que o Consolidado da XP usa, de proposito: dois
        # esquemas de ticker para o mesmo titulo fragmentariam a carteira em
        # dois ativos. A traducao abaixo so leva o codigo da B3 ("LFT") ao
        # vocabulario que aquela funcao entende ("Tesouro Selic").
        ticker, _ = _tesouro_ticker(_codigo_tesouro(primeira), vencimento)
        return {
            "ticker": ticker,
            "name": primeira,
            "asset_type": "tesouro",
            "quantity": qtd,
            # O extrato nao publica PU do titulo -- so saldo e quantidade,
            # ambos arredondados. Dividir um pelo outro daria um PU proximo
            # mas falso, e preco derivado de arredondamento ja custou caro
            # aqui antes.
            "market_price": None,
            "market_value": saldo if saldo is not None else 0.0,
            "invested_value": numero_br(
                _campo(secao, linha, "Valor aplicado")
            ),
            "is_loaned": False,
            "currency": "BRL",
        }, ""

    else:
        # Renda Fixa e Fundos de Investimentos caem aqui: o extrato traz saldo
        # e valor aplicado, e nenhuma quantidade nem preco unitario.
        return None, (
            f"secao '{secao['categoria'].title()}' nao publica quantidade "
            f"nem preco unitario"
        )

    if qtd is None:
        return None, "sem quantidade"
    if qtd == 0:
        return None, "quantidade zero (posicao encerrada)"

    aplicado = None
    if medio is not None and medio > 0:
        aplicado = round(medio * abs(qtd), 2)

    return {
        "ticker": ticker,
        "name": nome,
        "asset_type": tipo,
        "quantity": qtd,
        "market_price": preco,
        "market_value": saldo if saldo is not None else 0.0,
        "invested_value": aplicado,
        "is_loaned": categoria.startswith("ALUGUEL"),
        "currency": "BRL",
    }, ""


def _codigo_tesouro(nome: str) -> str:
    """Traduz o codigo da B3 para o nome que `_tesouro_ticker` reconhece."""
    upper = _norm(nome)
    if upper.startswith("LFT"):
        return "Tesouro Selic"
    if upper.startswith("LTN") or upper.startswith("NTNF"):
        return "Tesouro Prefixado"
    if upper.startswith("NTNB"):
        return "Tesouro IPCA"
    return nome


def _classe_ativo(tipo: str) -> str:
    return {
        "stock": "stock",
        "etf": "etf",
        "fii": "reit",
        "tesouro": "fixed_income",
    }.get(tipo, "other")


def tipo_provento(evento: str) -> str:
    alvo = _norm(evento)
    for marcador, tipo in _EVENTO_TIPO:
        if marcador in alvo:
            return tipo
    return "other"


def chave_snapshot(report_date: date, ticker: str, categoria: str) -> str:
    """Identidade da foto de um ativo numa data. Nao inclui o VALOR.

    Com a quantidade dentro da chave, duas exportacoes do mesmo dia (o
    cabecalho do extrato traz hora) gravariam DUAS linhas do mesmo ativo na
    mesma data, e quem somasse `market_value` por data contaria o papel duas
    vezes. A foto de hoje tem que SOBRESCREVER a de hoje de manha.

    A categoria entra porque DEXP3 pode estar comprado em "Acoes" e tomado em
    "Aluguel" no mesmo dia -- mesmo ticker, mesmo asset_type "stock",
    posicoes opostas. Sem ela uma apagaria a outra.
    """
    return make_external_id("b3pos-snap", [
        report_date.isoformat(), ticker, categoria,
    ])


def chave_provisionado(
    report_date: date,
    ticker: str,
    evento: str,
    previsao: date | None,
    ordinal: int,
) -> str:
    """Identidade de um provento provisionado. Tambem nao inclui o valor.

    O evento ja separa as duas linhas de PETR3 na mesma data de previsao
    ("DIVI" e "JURO"). O valor bruto e revisado de um extrato para o outro --
    com ele na chave, a revisao viraria linha nova em vez de corrigir a
    existente. O `ordinal` cobre o caso de a B3 listar o mesmo trio duas
    vezes: colapsar duas parcelas numa so perderia dinheiro em silencio.
    """
    return make_external_id("b3pos-prov", [
        report_date.isoformat(),
        ticker,
        evento,
        previsao.isoformat() if previsao else "",
        str(ordinal),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────

def _tabela_existe(conn: Connection, nome: str) -> bool:
    """Checa ANTES de tentar inserir, nao depois de falhar.

    No Postgres um INSERT que falha envenena a transacao inteira: o `except`
    captura, mas todo comando seguinte morre com "current transaction is
    aborted". Um plano B montado dentro do except nunca entregaria nada.
    """
    return conn.execute(
        text("SELECT to_regclass(:n)"), {"n": nome},
    ).scalar() is not None


def _inserir_provisionado(
    conn: Connection,
    *,
    user_id: str,
    portfolio_id: str,
    asset_id: str,
    report_date: date,
    prov: dict,
    source_id: str,
) -> None:
    conn.execute(
        text("""
            INSERT INTO portfolio_provisioned_income
                (user_id, portfolio_id, asset_id, report_date,
                 payment_forecast, event_label, income_type,
                 quantity, gross_amount, net_amount, currency,
                 source_system, source_table, source_id)
            VALUES
                (:uid, :pid, :aid, :rd,
                 :pf, :ev, :it,
                 :qty, :gross, :net, 'BRL',
                 'app4', :source_table, :sid)
            ON CONFLICT (portfolio_id, asset_id, report_date,
                         source_system, source_table, source_id)
            DO UPDATE SET
                payment_forecast = EXCLUDED.payment_forecast,
                event_label      = EXCLUDED.event_label,
                income_type      = EXCLUDED.income_type,
                quantity         = EXCLUDED.quantity,
                gross_amount     = EXCLUDED.gross_amount,
                net_amount       = EXCLUDED.net_amount,
                imported_at      = NOW()
        """),
        {
            "uid": user_id,
            "pid": portfolio_id,
            "aid": asset_id,
            "rd": report_date,
            "pf": prov.get("payment_forecast"),
            "ev": (prov["event_label"] or "PROVISIONADO")[:80],
            "it": prov["income_type"],
            "qty": prov.get("quantity"),
            "gross": prov.get("gross_amount"),
            "net": prov.get("net_amount"),
            "source_table": SOURCE_TABLE,
            "sid": source_id,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def parse(payload: bytes | tuple[str, bytes], engine: Engine) -> dict[str, Any]:
    """Importa um extrato "Posicao Detalhada". Aceita bytes ou (nome, bytes).

    O nome do arquivo e aceito por simetria com os outros importadores do
    lote, e ignorado: a data da foto vem de dentro do arquivo.
    """
    summary = make_summary(SOURCE)
    summary["provisioned_imported"] = 0

    user_id = settings.OWNER_USER_ID
    if not user_id:
        summary["errors"].append("OWNER_USER_ID nao configurado.")
        return finalize_summary(summary)

    if isinstance(payload, (tuple, list)) and len(payload) == 2:
        file_bytes = bytes(payload[1])
    else:
        file_bytes = bytes(payload)

    try:
        import openpyxl
    except ImportError:
        summary["errors"].append("openpyxl nao instalado.")
        return finalize_summary(summary)

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"Arquivo invalido: {safe_error(exc)}")
        return finalize_summary(summary)

    aba = next(
        (ws for ws in wb.worksheets if SHEET_NAME in _norm(ws.title)), None,
    )
    if aba is None:
        summary["errors"].append(
            "Aba 'Sua carteira' nao encontrada: nao e um extrato "
            "Posicao Detalhada."
        )
        return finalize_summary(summary)

    linhas = list(aba.iter_rows(values_only=True))
    report_date = extrair_data_foto(linhas)
    if report_date is None:
        # Cair em date.today() faria todo extrato antigo virar a foto de hoje,
        # e `report_date` esta na chave unica do snapshot: o historico inteiro
        # colapsaria numa linha so, sobrescrita a cada importacao.
        summary["errors"].append(
            "Extrato sem data no cabecalho ('Conta: ... | DD/MM/AAAA'). "
            "Sem ela a foto nao tem identidade e nao pode ser gravada."
        )
        return finalize_summary(summary)

    blocos = secoes(linhas)
    ensure_external_id_columns(engine)

    with engine.connect() as conn, conn.begin():
        try:
            portfolio_id = _ensure_portfolio(conn, user_id)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                f"Falha ao preparar carteira: {safe_error(exc)}"
            )
            return finalize_summary(summary)

        tem_provisionado = _tabela_existe(conn, "portfolio_provisioned_income")

        for secao in blocos:
            bloco = secao["bloco"]
            linhas_secao = secao["linhas"]

            if "CUSTODIA" in bloco:
                # Mesma posicao ja contada em "Acoes", vista pelo angulo da
                # custodia remunerada: BBAS3 1.479 e PETR3 109 sao os mesmos
                # papeis. Importar as duas dobraria o patrimonio dessas linhas.
                if linhas_secao:
                    summary["rows_skipped"] += len(linhas_secao)
                    summary["files_skipped_notes"].append(
                        f"Custodia Remunerada: {len(linhas_secao)} linhas "
                        f"ignoradas (mesma posicao ja contada em Acoes)."
                    )
                continue

            if "PROVENTO" in bloco or "DISTRIBUI" in bloco:
                if not tem_provisionado:
                    if linhas_secao:
                        summary["rows_skipped"] += len(linhas_secao)
                        summary["files_skipped_notes"].append(
                            f"Proventos provisionados: {len(linhas_secao)} "
                            f"linhas ignoradas -- a tabela "
                            f"portfolio_provisioned_income ainda nao existe "
                            f"neste banco (migration 073)."
                        )
                    continue
                _importar_provisionados(
                    conn, secao, summary,
                    user_id=user_id,
                    portfolio_id=portfolio_id,
                    report_date=report_date,
                )
                continue

            _importar_posicoes(
                conn, secao, summary,
                user_id=user_id,
                portfolio_id=portfolio_id,
                report_date=report_date,
            )

    summary["_report_date"] = report_date.isoformat()
    summary["_institution"] = INSTITUTION
    return finalize_summary(summary)


def _importar_posicoes(
    conn: Connection,
    secao: dict,
    summary: dict,
    *,
    user_id: str,
    portfolio_id: str,
    report_date: date,
) -> None:
    for linha in secao["linhas"]:
        pos, motivo = posicao(secao, linha)
        if pos is None:
            summary["rows_skipped"] += 1
            summary["files_skipped_notes"].append(
                f"{_texto(linha[0])}: {motivo}."
            )
            continue
        try:
            with conn.begin_nested():
                asset_id = get_or_create_asset(
                    conn,
                    ticker=pos["ticker"],
                    name=pos["name"] or pos["ticker"],
                    asset_class=_classe_ativo(pos["asset_type"]),
                    currency="BRL",
                )
                sid = chave_snapshot(
                    report_date, pos["ticker"], secao["categoria"],
                )
                _insert_snapshot(
                    conn,
                    user_id=user_id,
                    portfolio_id=portfolio_id,
                    asset_id=asset_id,
                    pos=pos,
                    report_date=report_date,
                    institution=INSTITUTION,
                    source_id=sid,
                    source_table=SOURCE_TABLE,
                )
                summary["positions_imported"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                f"[{secao['categoria'].title()}] {pos['ticker']}: "
                f"{safe_error(exc)}"
            )


def _importar_provisionados(
    conn: Connection,
    secao: dict,
    summary: dict,
    *,
    user_id: str,
    portfolio_id: str,
    report_date: date,
) -> None:
    # Desempate para o caso de a B3 listar o mesmo trio duas vezes (nao
    # acontece no extrato observado, mas colapsar duas parcelas numa so
    # perderia dinheiro em silencio). A ordem das linhas dentro de um trio e
    # estavel entre exportacoes, entao o ordinal nao reabre a chave.
    ordinal: Counter[tuple] = Counter()

    for linha in secao["linhas"]:
        ticker = _texto(linha[0]).upper()
        if not ticker:
            continue
        evento = _texto(_campo(secao, linha, "Evento"))
        bruto = numero_br(_campo(secao, linha, "Valor provisionado bruto"))
        liquido = numero_br(
            _campo(secao, linha, "Valor provisionado liquido")
        )
        previsao = parse_date_br(_campo(secao, linha, "Previsao pagamento"))
        qtd = numero_br(_campo(secao, linha, "Provisionado"))
        try:
            with conn.begin_nested():
                asset_id = get_or_create_asset(
                    conn,
                    ticker=ticker,
                    name=ticker,
                    asset_class=_classe_ativo("stock"),
                    currency="BRL",
                )
                chave = (ticker, evento, previsao)
                ordinal[chave] += 1
                sid = chave_provisionado(
                    report_date, ticker, evento, previsao, ordinal[chave],
                )
                _inserir_provisionado(
                    conn,
                    user_id=user_id,
                    portfolio_id=portfolio_id,
                    asset_id=asset_id,
                    report_date=report_date,
                    prov={
                        "payment_forecast": previsao,
                        "event_label": evento,
                        "income_type": tipo_provento(evento),
                        "quantity": qtd,
                        "gross_amount": bruto,
                        "net_amount": liquido,
                    },
                    source_id=sid,
                )
                summary["provisioned_imported"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                f"[Provisionado] {ticker}: {safe_error(exc)}"
            )
