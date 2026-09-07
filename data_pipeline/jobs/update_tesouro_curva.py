"""
data_pipeline/jobs/update_tesouro_curva.py

Ingere a curva diária oficial do Tesouro Direto (taxas e PUs de compra e venda
de cada título) a partir do dataset aberto do Tesouro Transparente.

Por que este job existe
-----------------------
O Extrato Analítico traz a taxa *contratada* de cada lote e a data de
liquidação. Falta o outro lado da marcação a mercado: a taxa que o Tesouro
oferece **hoje** para o mesmo vencimento. Sem ela o app só saberia repetir a
rentabilidade acumulada que o próprio extrato já imprime.

Com as duas pontas vale a identidade

    MtM = ((1 + i_contratada) / (1 + i_mercado)) ** (du_restante / 252) - 1

(ver `core/tesouro_mtm.mtm_por_taxa`), e o VNA — desconhecido para IPCA+ e
Selic — cancela. Por isso este job guarda **taxa**, não só preço.

Convenção de ponta (verificada contra o próprio CSV)
----------------------------------------------------
- `Taxa Compra` <-> `PU Compra`: o investidor **compra** o título.
- `Taxa Venda`  <-> `PU Venda` = `PU Base`: o investidor **resgata**.

A marcação de quem já tem o título usa a ponta de **venda** (resgate); a
avaliação de uma alternativa a comprar usa a ponta de **compra**. Guardar as
duas é o que permite ao veredito descontar o spread em vez de ignorá-lo.

Retenção
--------
O Supabase opera perto do teto do plano free, então o job guarda duas janelas
diferentes, porque as duas leituras têm necessidades diferentes:

- **Títulos que o usuário tem** (lidos de `tesouro_lots`): janela longa, para
  desenhar a trajetória da taxa desde a data de aplicação.
- **Todos os demais**: apenas os últimos dias, o bastante para listar e
  precificar as alternativas ofertadas hoje.

Guardar a série longa dos 62 títulos custaria ordem de dezenas de milhares de
linhas para um usuário que tem um. O excedente é podado na mesma transação —
sem a poda, a janela só cresce.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

TABLE_NAME = "tesouro_market_rates"
SOURCE_NAME = "Tesouro Transparente (dados abertos)"
JOB_NAME = "update_tesouro_curva"

CSV_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
    "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv"
)

# Janelas mantidas no banco, em dias corridos. Ajustáveis por ambiente para o
# caso de o usuário precisar de série mais longa num backfill pontual.
RETENCAO_DIAS = int(os.getenv("TESOURO_CURVA_RETENCAO_DIAS", "1095"))
RETENCAO_DIAS_OUTROS = int(os.getenv("TESOURO_CURVA_RETENCAO_DIAS_OUTROS", "30"))

DDL_TESOURO_MARKET_RATES = """
CREATE TABLE IF NOT EXISTS tesouro_market_rates (
    id            SERIAL PRIMARY KEY,
    security_key  TEXT        NOT NULL,
    title_name    TEXT        NOT NULL,
    maturity_date DATE        NOT NULL,
    base_date     DATE        NOT NULL,
    buy_rate      NUMERIC(12, 6),
    sell_rate     NUMERIC(12, 6),
    buy_pu        NUMERIC(18, 6),
    sell_pu       NUMERIC(18, 6),
    base_pu       NUMERIC(18, 6),
    updated_at    TIMESTAMP   DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tesouro_market_rates_key_date
    ON tesouro_market_rates (security_key, base_date);
CREATE INDEX IF NOT EXISTS ix_tesouro_market_rates_date
    ON tesouro_market_rates (base_date);
"""


# ---------------------------------------------------------------------------
# Helpers puros (testáveis sem rede e sem banco)
# ---------------------------------------------------------------------------

def _num(valor: str | None) -> float | None:
    """Converte número no formato brasileiro do CSV ('1.234,56')."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _data(valor: str | None) -> date | None:
    """Converte data dd/mm/aaaa."""
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor).strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _coluna(linha: dict, *nomes: str) -> str | None:
    """Lê a primeira coluna existente entre `nomes`, ignorando caixa e espaços.

    O cabeçalho do CSV já variou entre 'Taxa Compra Manha' e formas com acento
    ao longo dos anos; casar por prefixo normalizado evita quebrar a cada
    revisão do dataset.
    """
    for nome in nomes:
        alvo = nome.lower().replace(" ", "")
        for chave, valor in linha.items():
            if chave is None:
                continue
            if chave.lower().replace(" ", "").startswith(alvo):
                return valor
    return None


def parse_csv(
    conteudo: bytes | str,
    *,
    desde: date | None = None,
    desde_outros: date | None = None,
    chaves_longas: frozenset[str] | set[str] = frozenset(),
) -> list[dict]:
    """Converte o CSV do Tesouro Transparente em registros normalizados.

    O arquivo completo tem mais de dez anos e ~14 MB; só a janela útil é
    devolvida. `desde` é o corte para os títulos em `chaves_longas` (os que o
    usuário tem) e `desde_outros`, o corte — mais curto — para todo o resto.
    Sem `chaves_longas`, `desde` vale para todos.
    """
    from data_pipeline.importers.investments.tesouro_direto import tesouro_security_key

    if isinstance(conteudo, bytes):
        conteudo = conteudo.decode("latin-1", errors="replace")

    leitor = csv.DictReader(io.StringIO(conteudo), delimiter=";")
    registros: list[dict] = []

    for linha in leitor:
        base = _data(_coluna(linha, "Data Base"))
        if base is None:
            continue
        if desde is not None and base < desde:
            continue

        vencimento = _data(_coluna(linha, "Data Vencimento"))
        titulo = (_coluna(linha, "Tipo Titulo", "Tipo Título") or "").strip()
        if vencimento is None or not titulo:
            continue

        chave = tesouro_security_key(titulo, vencimento)
        if (
            desde_outros is not None
            and base < desde_outros
            and chave not in chaves_longas
        ):
            continue

        registros.append({
            "security_key":  chave,
            "title_name":    titulo,
            "maturity_date": vencimento,
            "base_date":     base,
            "buy_rate":      _num(_coluna(linha, "Taxa Compra")),
            "sell_rate":     _num(_coluna(linha, "Taxa Venda")),
            "buy_pu":        _num(_coluna(linha, "PU Compra")),
            "sell_pu":       _num(_coluna(linha, "PU Venda")),
            "base_pu":       _num(_coluna(linha, "PU Base")),
        })

    return registros


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

def ensure_schema(engine) -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        for comando in DDL_TESOURO_MARKET_RATES.strip().split(";"):
            if comando.strip():
                conn.execute(text(comando))


_UPSERT = """
INSERT INTO tesouro_market_rates
    (security_key, title_name, maturity_date, base_date,
     buy_rate, sell_rate, buy_pu, sell_pu, base_pu, updated_at)
VALUES
    (:security_key, :title_name, :maturity_date, :base_date,
     :buy_rate, :sell_rate, :buy_pu, :sell_pu, :base_pu, NOW())
ON CONFLICT (security_key, base_date) DO UPDATE SET
    title_name    = EXCLUDED.title_name,
    maturity_date = EXCLUDED.maturity_date,
    buy_rate      = EXCLUDED.buy_rate,
    sell_rate     = EXCLUDED.sell_rate,
    buy_pu        = EXCLUDED.buy_pu,
    sell_pu       = EXCLUDED.sell_pu,
    base_pu       = EXCLUDED.base_pu,
    updated_at    = NOW()
"""


def chaves_detidas(engine) -> frozenset[str]:
    """Títulos que o usuário tem em carteira, segundo o Extrato Analítico.

    Se `tesouro_lots` ainda não existe (nenhum extrato importado), devolve
    vazio — a curva curta de todos os títulos já basta para a tela.
    """
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            linhas = conn.execute(text(
                "SELECT DISTINCT security_key FROM tesouro_lots "
                "WHERE security_key IS NOT NULL"
            )).fetchall()
        return frozenset(str(linha[0]) for linha in linhas)
    except Exception as exc:  # tabela ausente é o caso esperado, não um erro
        logger.info("update_tesouro_curva: sem lotes importados (%s)", exc)
        return frozenset()


def gravar(
    engine,
    registros: list[dict],
    *,
    corte: date | None = None,
    corte_outros: date | None = None,
    chaves_longas: frozenset[str] | set[str] = frozenset(),
) -> tuple[int, int]:
    """Grava as janelas e poda o que ficou fora delas. Devolve (gravados, podados)."""
    from sqlalchemy import bindparam, text

    gravados = 0
    podados = 0
    with engine.begin() as conn:
        for inicio in range(0, len(registros), 500):
            lote = registros[inicio:inicio + 500]
            conn.execute(text(_UPSERT), lote)
            gravados += len(lote)

        if corte is not None:
            resultado = conn.execute(
                text("DELETE FROM tesouro_market_rates WHERE base_date < :corte"),
                {"corte": corte},
            )
            podados += resultado.rowcount or 0

        if corte_outros is not None:
            # Poda a janela curta dos títulos que o usuário não tem. A lista
            # vazia precisa de tratamento próprio: `NOT IN ()` é erro de sintaxe.
            if chaves_longas:
                sql = (
                    "DELETE FROM tesouro_market_rates "
                    "WHERE base_date < :corte AND security_key NOT IN :chaves"
                )
                stmt = text(sql).bindparams(bindparam("chaves", expanding=True))
                resultado = conn.execute(
                    stmt, {"corte": corte_outros, "chaves": sorted(chaves_longas)}
                )
            else:
                resultado = conn.execute(
                    text("DELETE FROM tesouro_market_rates WHERE base_date < :corte"),
                    {"corte": corte_outros},
                )
            podados += resultado.rowcount or 0

    return gravados, podados


def run() -> dict:
    """Baixa a curva oficial e faz UPSERT em tesouro_market_rates."""
    result = {
        "status":           "success",
        "table_name":       TABLE_NAME,
        "source_name":      SOURCE_NAME,
        "job_name":         JOB_NAME,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    None,
    }

    try:
        import requests
    except ImportError as exc:
        result["status"] = "failed"
        result["error_message"] = f"Dependência ausente: {exc}"
        return result

    from data_pipeline.utils.db_utils import get_pipeline_engine
    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    hoje = date.today()
    corte = hoje - timedelta(days=RETENCAO_DIAS)
    corte_outros = hoje - timedelta(days=RETENCAO_DIAS_OUTROS)

    try:
        ensure_schema(engine)
        longas = chaves_detidas(engine)

        resposta = requests.get(CSV_URL, timeout=180)
        resposta.raise_for_status()
        registros = parse_csv(
            resposta.content,
            desde=corte,
            desde_outros=corte_outros,
            chaves_longas=longas,
        )

        if not registros:
            result["status"] = "failed"
            result["error_message"] = (
                f"CSV do Tesouro sem linhas a partir de {corte_outros.isoformat()}"
            )
            return result

        gravados, podados = gravar(
            engine,
            registros,
            corte=corte,
            corte_outros=corte_outros,
            chaves_longas=longas,
        )

        result["records_inserted"] = gravados
        # Linha podada não é linha atualizada. Contar poda em `records_updated`
        # faria o painel de atualizações reportar trabalho que não houve.
        result["records_deleted"] = podados
        logger.info(
            "update_tesouro_curva: %d linhas gravadas, %d podadas "
            "(janela longa desde %s para %d títulos em carteira; curta desde %s)",
            gravados, podados, corte.isoformat(), len(longas), corte_outros.isoformat(),
        )

    except Exception as exc:
        logger.exception("update_tesouro_curva falhou")
        result["status"] = "failed"
        result["error_message"] = str(exc)

    return result
