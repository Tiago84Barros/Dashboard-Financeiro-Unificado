"""
data_pipeline/importers/investments/b3_posicao.py
=================================================
Parser do extrato "Posicao" da Area do Investidor da B3 (.xlsx).

Nao confundir com `b3_posicao_detalhada`. Sao DOIS downloads diferentes do
mesmo portal, e nenhum substitui o outro:

  Posicao Detalhada  uma aba so ("Sua carteira"), secoes empilhadas, saldo em
                     reais e a data dentro do cabecalho. Traz preco medio.
  Posicao (este)     uma aba por classe de ativo -- Acoes, Emprestimos, ETF,
                     Fundo de Investimento, Renda Fixa, Tesouro Direto --,
                     cada uma com cabecalho proprio na linha 1. Traz ISIN,
                     instituicao, numero de contrato de aluguel e as tres
                     marcacoes da renda fixa (MTM, CURVA, FECHAMENTO). NAO
                     traz custo de aquisicao, exceto o Valor Aplicado do
                     Tesouro.

O arquivo e identico em layout ao "Relatorio Consolidado" que a XP entrega
(`xp_consolidado`): mesmas colunas, mesma ordem. O que muda e o nome das abas
-- la "Posicao - Acoes", aqui "Acoes" -- e a procedencia. Por isso ele e uma
fonte propria, com `source_table` propria, e nao um apelido do parser da XP.

Grava so em `portfolio_position_snapshots`. E uma FOTO, nunca uma transacao:
`portfolio_positions` e reconstruida de `investment_transactions` por
`positions.recompute_for_user()`, e escrever foto la apagaria o preco medio.

Decisoes que a planilha forca e que nao sao obvias:

1. DATA DA FOTO. O arquivo nao declara a data em nenhuma celula. Ela vive no
   nome do download ("posicao-2026-10-27-08-15-29.xlsx") e, como reserva, em
   docProps/core.xml (dcterms:created, gravado pelo gerador). Sem data o
   extrato e recusado -- cair em `date.today()` faria todo arquivo antigo
   virar a foto de hoje, e `report_date` esta na chave unica do snapshot: o
   historico colapsaria numa linha so, sobrescrita a cada importacao.

2. LEITURA POR ROTULO, NUNCA POR INDICE. "Quantidade" e a coluna 8 em Acoes,
   7 em ETF e 11 em Emprestimos, e a B3 muda ordem de coluna entre versoes do
   export sem avisar. Indice fixo erraria calado -- leria "Quantidade
   Disponivel" no lugar de "Quantidade" e nenhuma excecao apareceria. O
   casamento do rotulo e EXATO depois de normalizar: "QUANTIDADE" e prefixo de
   "QUANTIDADE DISPONIVEL", e busca por substring pegaria a errada.

3. EMPRESTIMOS tem a coluna "Natureza". Doador emprestou o papel: ele continua
   sendo dele, entra positivo. Tomador pegou emprestado: ele DEVE o papel,
   entra NEGATIVO em quantidade e em valor. Ignorar a Natureza somaria a
   divida ao patrimonio. No arquivo observado nenhum ticker de Emprestimos
   aparece nas abas de custodia -- evidencia de que o papel emprestado SAI de
   "Acoes" --, entao as duas abas se somam e nao ha duplicidade a deduplicar.
   Se um dia coincidirem, a leitura certa e emprestimo parcial (parte na
   custodia, parte emprestada), que tambem soma.

4. CHAVE DO SNAPSHOT. Nao inclui quantidade nem valor: duas exportacoes do
   mesmo dia (o nome do arquivo traz hora) gravariam duas linhas do mesmo
   ativo na mesma data, e quem somasse `market_value` contaria o papel duas
   vezes. A foto de hoje tem que SOBRESCREVER a de hoje de manha. Inclui a
   categoria (a aba) e um discriminador por aba -- instituicao, conta,
   natureza, numero de contrato, codigo do papel --, porque o extrato e da
   custodia central: o mesmo ticker pode vir em duas corretoras, e com chave
   so de ticker a segunda sobrescreveria a primeira e metade da posicao
   desapareceria sem erro.

5. RENDA FIXA tem tres marcacoes e o arquivo observado preenche apenas CURVA
   (MTM e FECHAMENTO vem "-"). A escolha e por marcacao INTEIRA -- preco e
   valor da mesma coluna --, na ordem MTM, CURVA, FECHAMENTO, e a marcacao
   usada e anotada no resumo. Misturar preco de uma com valor de outra daria
   um par que nao existe no documento.

6. CUSTO. Fora o Tesouro ("Valor Aplicado"), o extrato nao publica custo de
   aquisicao. `invested_value` fica NULL. Repetir o valor de mercado no custo
   daria rentabilidade zero para sempre, e a tela nao teria como saber que o
   numero foi inventado.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date
from typing import Any, Callable

from sqlalchemy.engine import Connection, Engine

from core.config import settings

from .b3_posicao_detalhada import numero_br
from .common import (
    ensure_external_id_columns,
    finalize_summary,
    get_or_create_asset,
    make_external_id,
    make_summary,
    safe_error,
)
from .xlsx_probe import norm as _norm
from .xp_consolidado import (
    _ensure_portfolio,
    _insert_snapshot,
    _is_fundo_rf,
    _map_asset_type,
    _tesouro_ticker,
)

logger = logging.getLogger(__name__)

SOURCE = "b3_posicao"
SOURCE_TABLE = "b3_posicao"
INSTITUTION = "B3 - Area do Investidor"

# Data no nome do download: "posicao-2026-10-27-08-15-29.xlsx". O sufixo de
# hora e ignorado de proposito -- ver decisao 4 no cabecalho.
_RE_DATA_NOME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Reserva: o gerador grava o instante da exportacao em docProps/core.xml.
# Lido por regex no texto cru, sem parser de XML: o arquivo vem de fora e
# parser de XML carrega expansao de entidade.
_RE_DATA_DOCPROPS = re.compile(r"<dcterms:created[^>]*>(\d{4})-(\d{2})-(\d{2})")

# As tres marcacoes da Renda Fixa, em ordem de preferencia (decisao 5).
_MARCACOES: tuple[tuple[str, str, str], ...] = (
    ("MTM", "PRECO ATUALIZADO MTM", "VALOR ATUALIZADO MTM"),
    ("CURVA", "PRECO ATUALIZADO CURVA", "VALOR ATUALIZADO CURVA"),
    ("FECHAMENTO", "PRECO ATUALIZADO FECHAMENTO", "VALOR ATUALIZADO FECHAMENTO"),
)


# ---------------------------------------------------------------------------
# Leitura de celula (pura)
# ---------------------------------------------------------------------------

def _texto(valor: Any) -> str:
    """Texto da celula. "-" e a forma do extrato dizer "nao se aplica"."""
    if valor is None:
        return ""
    txt = str(valor).strip()
    return "" if txt == "-" else txt


def cabecalho(linha: tuple) -> dict[str, int]:
    """Mapa rotulo normalizado -> indice da coluna, da linha 1 da aba."""
    mapa: dict[str, int] = {}
    for i, valor in enumerate(linha):
        rotulo = _norm(_texto(valor))
        if rotulo and rotulo not in mapa:
            mapa[rotulo] = i
    return mapa


def _celula(hdr: dict[str, int], linha: tuple, *rotulos: str) -> Any:
    """Valor cru da primeira coluna cujo rotulo casa EXATAMENTE (decisao 2)."""
    for rotulo in rotulos:
        i = hdr.get(_norm(rotulo))
        if i is not None and i < len(linha):
            return linha[i]
    return None


def _txt(hdr: dict[str, int], linha: tuple, *rotulos: str) -> str:
    return _texto(_celula(hdr, linha, *rotulos))


def _num(hdr: dict[str, int], linha: tuple, *rotulos: str) -> float | None:
    return numero_br(_celula(hdr, linha, *rotulos))


def _ticker_e_nome(hdr: dict[str, int], linha: tuple) -> tuple[str, str]:
    """Ticker e nome a partir de "BRAP3 - BRADESPAR S.A.".

    A coluna "Codigo de Negociacao" tem prioridade sobre o prefixo do Produto:
    ela e o codigo da fita, enquanto o prefixo do Produto e texto livre.
    """
    produto = _txt(hdr, linha, "Produto")
    if " - " in produto:
        ticker, nome = produto.split(" - ", 1)
        ticker, nome = ticker.strip().upper(), nome.strip()
    else:
        ticker, nome = produto.upper(), produto
    codigo = _txt(hdr, linha, "Codigo de Negociacao")
    if codigo:
        ticker = codigo.upper()
    return ticker, nome


def _instituicao(hdr: dict[str, int], linha: tuple) -> str:
    """Corretora da linha, ou a B3 quando a coluna nao veio.

    O extrato e da custodia central e nomeia a corretora de cada posicao --
    guardar "B3" em todas perderia a unica informacao que diz onde o papel
    esta.
    """
    return _txt(hdr, linha, "Instituicao") or INSTITUTION


# ---------------------------------------------------------------------------
# Leitores por aba -- devolvem (posicao, motivo_de_recusa)
# ---------------------------------------------------------------------------

def _base(
    hdr: dict[str, int],
    linha: tuple,
    ticker: str,
    nome: str,
    asset_type: str,
) -> dict:
    return {
        "ticker": ticker,
        "name": nome,
        "asset_type": asset_type,
        "market_price": None,
        "market_value": None,
        "invested_value": None,
        "is_loaned": False,
        "currency": "BRL",
        "institution": _instituicao(hdr, linha),
        "_disc": [],
    }


def _ler_acoes(hdr: dict[str, int], linha: tuple):
    ticker, nome = _ticker_e_nome(hdr, linha)
    qtd = _num(hdr, linha, "Quantidade")
    if qtd is None:
        return None, "sem a coluna Quantidade"
    pos = _base(hdr, linha, ticker, nome, "stock")
    pos.update({
        "quantity": qtd,
        "market_price": _num(hdr, linha, "Preco de Fechamento"),
        "market_value": _num(hdr, linha, "Valor Atualizado"),
        "_disc": [pos["institution"], _txt(hdr, linha, "Conta")],
    })
    return pos, ""


def _ler_etf(hdr: dict[str, int], linha: tuple):
    pos, motivo = _ler_acoes(hdr, linha)
    if pos is None:
        return None, motivo
    # "Criptoativo" aparece em Tipo (BITH11). Continua sendo ETF listado na
    # B3: a classe do ativo e o veiculo, nao o lastro.
    pos["asset_type"] = "etf"
    return pos, ""


def _ler_fundos(hdr: dict[str, int], linha: tuple):
    pos, motivo = _ler_acoes(hdr, linha)
    if pos is None:
        return None, motivo
    # Mesma regra do parser da XP, de proposito: um fundo classificado aqui
    # como FII e la como fundo_rf trocaria de classe conforme o arquivo
    # importado, e a carteira mudaria de composicao sem nada ter mudado.
    tipo = _txt(hdr, linha, "Tipo")
    pos["asset_type"] = "fundo_rf" if _is_fundo_rf(tipo) else "fii"
    return pos, ""


def _ler_emprestimos(hdr: dict[str, int], linha: tuple):
    ticker, nome = _ticker_e_nome(hdr, linha)
    qtd = _num(hdr, linha, "Quantidade")
    if qtd is None:
        return None, "sem a coluna Quantidade"

    natureza = _txt(hdr, linha, "Natureza")
    chave = _norm(natureza)
    if chave.startswith("DOADOR"):
        sinal = 1.0
    elif chave.startswith("TOMADOR"):
        sinal = -1.0
    else:
        # Sem a Natureza nao da para dizer se o papel e dele ou devido por
        # ele, e as duas respostas tem sinais opostos. Chutar uma somaria
        # divida ao patrimonio ou apagaria posicao viva (decisao 3).
        return None, (
            f"Natureza '{natureza or '(vazia)'}' desconhecida: sem ela nao da "
            "para dizer se a posicao e propria (Doador) ou devida (Tomador)"
        )

    valor = _num(hdr, linha, "Valor Atualizado")
    pos = _base(hdr, linha, ticker, nome, "stock")
    pos.update({
        "quantity": sinal * qtd,
        # Preco e preco: nao leva sinal. O sinal mora na quantidade e no valor.
        "market_price": _num(hdr, linha, "Preco de Fechamento"),
        "market_value": None if valor is None else sinal * valor,
        "is_loaned": True,
        "_natureza": natureza,
        "_disc": [
            pos["institution"],
            chave,
            _txt(hdr, linha, "Numero de Contrato"),
        ],
    })
    return pos, ""


def _ler_renda_fixa(hdr: dict[str, int], linha: tuple):
    produto = _txt(hdr, linha, "Produto")
    if not produto:
        return None, "sem a coluna Produto"
    tipo = produto.split(" - ", 1)[0].strip() if " - " in produto else produto
    emissor = _txt(hdr, linha, "Emissor")
    codigo = _txt(hdr, linha, "Codigo")
    # Sem codigo o ticker viraria "CDB" para todo CDB de todo banco, e tres
    # CDBs colidiriam num ativo so. O codigo da B3 e a identidade do papel.
    ticker = (codigo or tipo).upper()
    nome = f"{tipo} - {emissor}" if emissor else produto

    qtd = _num(hdr, linha, "Quantidade")
    if qtd is None:
        return None, "sem a coluna Quantidade"

    marcacao, preco, valor = "", None, None
    for nome_marcacao, rot_preco, rot_valor in _MARCACOES:
        candidato = _num(hdr, linha, rot_valor)
        if candidato is None:
            continue
        marcacao = nome_marcacao
        preco = _num(hdr, linha, rot_preco)
        valor = candidato
        break
    if valor is None:
        return None, (
            "nenhuma das marcacoes (MTM, CURVA, FECHAMENTO) traz valor "
            "atualizado"
        )

    pos = _base(hdr, linha, ticker, nome, "fixed_income")
    pos.update({
        "quantity": qtd,
        "market_price": preco,
        "market_value": valor,
        # CFF e classe de FIP. Classificacao herdada do parser da XP para o
        # mesmo papel nao trocar de classe conforme a origem do arquivo.
        "asset_type": (
            "fundo_rf" if tipo.upper().startswith("CFF") else "fixed_income"
        ),
        "_marcacao": marcacao,
        "_disc": [pos["institution"], codigo, _txt(hdr, linha, "Vencimento")],
    })
    return pos, ""


def _ler_tesouro(hdr: dict[str, int], linha: tuple):
    produto = _txt(hdr, linha, "Produto")
    if not produto:
        return None, "sem a coluna Produto"
    vencimento = _txt(hdr, linha, "Vencimento")
    ticker, nome = _tesouro_ticker(produto, vencimento)

    qtd = _num(hdr, linha, "Quantidade")
    if qtd is None:
        return None, "sem a coluna Quantidade"

    # "Valor Atualizado" repete "Valor bruto" no arquivo observado. O liquido
    # desconta IR e taxa que so existem no resgate: ele nao e o valor do
    # titulo hoje, e usa-lo subavaliaria a carteira de quem nao vai resgatar.
    mercado = _num(hdr, linha, "Valor Atualizado")
    if mercado is None:
        mercado = _num(hdr, linha, "Valor bruto")
    if mercado is None:
        return None, "sem Valor Atualizado nem Valor bruto"

    pos = _base(hdr, linha, ticker, nome, "tesouro")
    pos.update({
        "quantity": qtd,
        # O extrato nao publica PU do titulo. Dividir valor por quantidade
        # daria um numero derivado passando por numero lido.
        "market_price": None,
        "market_value": mercado,
        "invested_value": _num(hdr, linha, "Valor Aplicado"),
        "_disc": [pos["institution"], vencimento],
    })
    return pos, ""


# Catalogo de abas: nome normalizado -> (categoria da chave, leitor).
# A categoria entra na chave do snapshot (decisao 4) e nao muda quando a B3
# renomeia a aba -- "FUNDOS DE INVESTIMENTO" e "FUNDO DE INVESTIMENTO" caem na
# mesma categoria "fundos", senao a mesma posicao viraria duas linhas no dia
# em que o plural voltasse.
_ABAS: dict[str, tuple[str, Callable[..., tuple]]] = {
    "ACOES": ("acoes", _ler_acoes),
    "ACAO": ("acoes", _ler_acoes),
    "EMPRESTIMOS": ("emprestimos", _ler_emprestimos),
    "EMPRESTIMO": ("emprestimos", _ler_emprestimos),
    "ETF": ("etf", _ler_etf),
    "FUNDO DE INVESTIMENTO": ("fundos", _ler_fundos),
    "FUNDOS DE INVESTIMENTO": ("fundos", _ler_fundos),
    "RENDA FIXA": ("renda_fixa", _ler_renda_fixa),
    "TESOURO DIRETO": ("tesouro", _ler_tesouro),
}

# Nomes de aba que identificam este extrato, usados pelo sniffer. Vive aqui
# porque quem ganha um leitor novo tem que aparecer na deteccao no mesmo
# commit -- lista paralela no sniffer sairia de sincronia calada.
ABAS_CONHECIDAS: frozenset[str] = frozenset(_ABAS)

__all__ = [
    "ABAS_CONHECIDAS",
    "SOURCE",
    "SOURCE_TABLE",
    "cabecalho",
    "chave_snapshot",
    "data_da_foto",
    "ler_aba",
    "parse",
]


# ---------------------------------------------------------------------------
# Data da foto e chave
# ---------------------------------------------------------------------------

def data_da_foto(nome: str, file_bytes: bytes) -> date | None:
    """Data de referencia do extrato. None quando o arquivo nao declara uma.

    Nome primeiro porque e o que o usuario ve e pode conferir; docProps so
    como reserva, para o arquivo renomeado ("carteira.xlsx") nao ser recusado
    tendo a data dentro. Nunca `date.today()` (decisao 1).
    """
    achado = _RE_DATA_NOME.search(nome or "")
    if achado:
        encontrada = _data_valida(achado)
        if encontrada:
            return encontrada

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            bruto = zf.read("docProps/core.xml").decode("utf-8", "ignore")
    except (KeyError, OSError, zipfile.BadZipFile, ValueError):
        return None
    achado = _RE_DATA_DOCPROPS.search(bruto)
    return _data_valida(achado) if achado else None


def _data_valida(achado: re.Match) -> date | None:
    try:
        return date(
            int(achado.group(1)), int(achado.group(2)), int(achado.group(3))
        )
    except ValueError:
        return None


def chave_snapshot(
    report_date: date,
    categoria: str,
    ticker: str,
    discriminador: list[str],
) -> str:
    """Identidade da foto de um ativo numa data. Sem quantidade nem valor."""
    return make_external_id("b3posi-snap", [
        report_date.isoformat(),
        categoria,
        ticker,
        "|".join(_norm(p) for p in discriminador if p),
    ])


def ler_aba(aba_nome: str, linhas: list[tuple]) -> tuple[list[dict], list[str]]:
    """Posicoes de uma aba e os motivos de recusa. Pura: nao toca o banco.

    `linhas` inclui o cabecalho. O bloco "Total" no pe da aba sai sozinho: ele
    preenche so a ultima coluna, e linha sem Produto nao e posicao.
    """
    chave = _norm(aba_nome)
    if chave not in _ABAS:
        return [], []
    categoria, leitor = _ABAS[chave]
    if not linhas:
        return [], []

    hdr = cabecalho(linhas[0])
    posicoes: list[dict] = []
    recusas: list[str] = []
    for linha in linhas[1:]:
        if not linha or not _texto(linha[0]):
            continue
        pos, motivo = leitor(hdr, linha)
        if pos is None:
            recusas.append(f"[{aba_nome}] {_texto(linha[0])[:60]}: {motivo}.")
            continue
        pos["_categoria"] = categoria
        posicoes.append(pos)
    return posicoes, recusas


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse(payload: bytes | tuple[str, bytes], engine: Engine) -> dict[str, Any]:
    """Importa um extrato "Posicao". Aceita (nome, bytes) ou bytes.

    O nome importa: e dele que sai a data da foto (decisao 1).
    """
    summary = make_summary(SOURCE)

    user_id = settings.OWNER_USER_ID
    if not user_id:
        summary["errors"].append("OWNER_USER_ID nao configurado.")
        return finalize_summary(summary)

    if isinstance(payload, (tuple, list)) and len(payload) == 2:
        nome_arquivo, file_bytes = str(payload[0]), bytes(payload[1])
    else:
        nome_arquivo, file_bytes = "", bytes(payload)

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

    conhecidas = [ws for ws in wb.worksheets if _norm(ws.title) in _ABAS]
    if not conhecidas:
        summary["errors"].append(
            "Nenhuma aba conhecida (Acoes, Emprestimos, ETF, Fundo de "
            "Investimento, Renda Fixa, Tesouro Direto): nao e um extrato "
            "Posicao da B3."
        )
        return finalize_summary(summary)

    report_date = data_da_foto(nome_arquivo, file_bytes)
    if report_date is None:
        summary["errors"].append(
            "Extrato sem data: o nome do arquivo nao traz AAAA-MM-DD (como em "
            "'posicao-2026-10-27-08-15-29.xlsx') e o arquivo nao declara a "
            "data de exportacao. Sem ela a foto nao tem identidade e nao pode "
            "ser gravada."
        )
        return finalize_summary(summary)

    # Abas que o arquivo traz e este parser nao le. Nomeadas, nunca sumidas:
    # uma classe nova da B3 (BDR, Ouro, Conta Corrente) tem layout proprio e
    # chutar colunas gravaria numero errado -- mas o usuario precisa saber que
    # aquela parte do patrimonio nao entrou.
    ignoradas = [
        ws.title for ws in wb.worksheets if _norm(ws.title) not in _ABAS
    ]
    if ignoradas:
        summary["files_skipped_notes"].append(
            "Abas sem leitor neste extrato, nao importadas: "
            + ", ".join(ignoradas)
            + " -- cada classe tem colunas proprias e nao da para ler por "
              "analogia."
        )

    ensure_external_id_columns(engine)
    marcacoes_usadas: set[str] = set()

    with engine.connect() as conn, conn.begin():
        try:
            portfolio_id = _ensure_portfolio(conn, user_id)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                f"Falha ao preparar carteira: {safe_error(exc)}"
            )
            return finalize_summary(summary)

        for ws in conhecidas:
            try:
                linhas = list(ws.iter_rows(values_only=True))
                posicoes, recusas = ler_aba(ws.title, linhas)
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(f"Aba '{ws.title}': {safe_error(exc)}")
                continue

            summary["rows_skipped"] += len(recusas)
            summary["files_skipped_notes"].extend(recusas)

            for pos in posicoes:
                if pos.get("_marcacao"):
                    marcacoes_usadas.add(pos["_marcacao"])
                _gravar(
                    conn, pos, summary,
                    user_id=user_id,
                    portfolio_id=portfolio_id,
                    report_date=report_date,
                )

    if marcacoes_usadas:
        summary["files_skipped_notes"].append(
            "Renda Fixa: valor lido da marcacao "
            + ", ".join(sorted(marcacoes_usadas))
            + " -- o extrato preenche uma das tres (MTM, CURVA, FECHAMENTO) e "
              "as outras vem vazias."
        )

    summary["_report_date"] = report_date.isoformat()
    summary["_institution"] = INSTITUTION
    return finalize_summary(summary)


def _gravar(
    conn: Connection,
    pos: dict,
    summary: dict,
    *,
    user_id: str,
    portfolio_id: str,
    report_date: date,
) -> None:
    try:
        with conn.begin_nested():
            asset_id = get_or_create_asset(
                conn,
                ticker=pos["ticker"],
                name=pos.get("name") or pos["ticker"],
                asset_class=_map_asset_type(pos["asset_type"]),
                currency=pos.get("currency", "BRL"),
            )
            _insert_snapshot(
                conn,
                user_id=user_id,
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                pos=pos,
                report_date=report_date,
                institution=pos.get("institution") or INSTITUTION,
                source_id=chave_snapshot(
                    report_date,
                    pos["_categoria"],
                    pos["ticker"],
                    pos.get("_disc") or [],
                ),
                source_table=SOURCE_TABLE,
            )
            summary["positions_imported"] += 1
            if pos["quantity"] < 0:
                # Posicao tomada em aluguel e divida, nao patrimonio. Fica
                # visivel no resumo porque e o unico numero negativo que o
                # importador grava de proposito.
                summary["files_skipped_notes"].append(
                    f"{pos['ticker']}: tomado em aluguel "
                    f"({pos.get('_natureza') or 'Tomador'}) -- gravado "
                    "negativo, e devido e nao soma ao patrimonio."
                )
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(
            f"[{pos.get('_categoria', '?')}] {pos.get('ticker', '?')}: "
            f"{safe_error(exc)}"
        )
