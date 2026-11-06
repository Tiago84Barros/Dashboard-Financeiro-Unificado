"""
data_pipeline/importers/investments/rico_extrato_conta.py
=========================================================
Parser do "Extrato da conta" da Rico (.xlsx) — o extrato de CAIXA da conta
na corretora, não um extrato de ativos.

Por que existe (06/11/2026): o usuário soltou esse arquivo no uploader "Dados
Históricos B3" e levou "1 arquivo não reconhecido — abas encontradas:
Planilha1". O detector da B3 decide pelo nome da aba, e "Planilha1" não diz
nada; o arquivo precisa ser reconhecido pelo conteúdo.

O que o arquivo traz e o que vira dado:

  "RENDIMENTOS DE CLIENTES MANA11 S/ 368"      → dividends (reit_income),
                                                 quantidade 368
  "DIVIDENDOS DE CLIENTES BBAS3 S/ 100"        → dividends (dividend)
  "RENDIMENTO FUNDO FECHADO BALCÃO PIER11 - …" → dividends (reit_income),
                                                 quantidade em branco (ver
                                                 abaixo)

E o que fica de fora, de propósito, contado em `rows_skipped`:

  * "OPERAÇÕES EM BOLSA … NOTA Nº …" — o líquido da nota inteira, sem ativo,
    quantidade nem preço. Compra e venda vêm da Negociação da B3.
  * Juros sobre capital próprio — o extrato de caixa pode lançar o valor
    líquido e o IRRF em linhas separadas, e a Movimentação da B3 lança um
    valor só. Gravar daqui arriscaria o mesmo JCP duas vezes com valores
    diferentes, que a deduplicação por valor não pega. Fica com a B3.
  * Remuneração de aluguel (BTC), taxas de intermediação e o IRRF delas — o
    app não tem tabela de renda de aluguel; `dividends` não é o lugar.
  * TED, retirada e qualquer outra movimentação de caixa.
  * O bloco "Lançamentos futuros" — crédito ainda não pago pode mudar.

Provento sem quantidade (o rendimento de fundo fechado de balcão não diz
quantas cotas) grava `quantity` e `amount_per_unit` NULL — migração 083 —,
nunca 1 cota com o pagamento inteiro por cota. Se a Movimentação da B3 trouxer
a quantidade depois, `common.ajustar_quantidades_dividendos` preenche a linha.
Se o banco ainda não aceitar NULL, o lançamento sai nomeado em `errors`.

Deduplicação: além do `external_id` próprio (prefixo `ricocc`),
`insert_dividend` só grava se não houver provento do mesmo ativo, na mesma
data de pagamento, do mesmo tipo e com o mesmo total — a chave canônica que
já impede o Consolidado da XP de duplicar a Movimentação da B3. O mesmo
rendimento chegando pela Movimentação e por este extrato entra uma vez só,
em qualquer ordem. A data usada é a de LIQUIDAÇÃO, que é quando o dinheiro
entrou e é a "Data" que a Movimentação da B3 publica para o pagamento.

O workbook é aberto em modo normal, não `read_only`: o arquivo da Rico
declara dimensão menor do que o conteúdo (B1:M35 num arquivo de 58 linhas), e
em `read_only` o openpyxl para na dimensão declarada — as últimas linhas do
extrato sumiriam sem erro.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date
from typing import Any

from sqlalchemy.engine import Engine

from core.config import settings

from .common import (
    aviso_sem_quantidade,
    classify_ticker,
    dividends_aceita_quantidade_vazia,
    ensure_external_id_columns,
    finalize_summary,
    get_or_create_asset,
    insert_dividend,
    make_external_id,
    make_summary,
    parse_date_br,
    quantidade_e_valor_unitario,
    safe_error,
    to_float_br,
)
from .xlsx_probe import abrir, fechar, norm, primeiras_linhas

logger = logging.getLogger(__name__)

SOURCE = "rico_extrato_conta"

# Linhas olhadas para reconhecer o arquivo. O cabeçalho da tabela fica na
# linha 14 do export de hoje; 30 dá folga para um bloco de projeções maior.
_LINHAS_SONDA = 30

_TICKER = r"([A-Z]{4}\d{1,2}B?)"
_QTD = r"S/\s*([\d.]+)"

# (padrão sobre o texto normalizado, tipo em dividends, tem quantidade?)
_PROVENTOS: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(rf"^RENDIMENTOS? DE CLIENTES\s+{_TICKER}\s+{_QTD}"),
     "reit_income", True),
    (re.compile(rf"^DIVIDENDOS? DE CLIENTES\s+{_TICKER}\s+{_QTD}"),
     "dividend", True),
    (re.compile(rf"^RENDIMENTOS? FUNDO FECHADO BALCAO\s+{_TICKER}\b"),
     "reit_income", False),
)


# ─────────────────────────────────────────────────────────────────────────────
# Reconhecimento
# ─────────────────────────────────────────────────────────────────────────────

def _celulas(linha) -> list[str]:
    return [norm(str(c)) for c in linha if c is not None and str(c).strip()]


def _e_cabecalho(celulas: list[str]) -> bool:
    return (
        "MOVIMENTACAO" in celulas
        and "LIQUIDACAO" in celulas
        and "LANCAMENTO" in celulas
        and any(c.startswith("VALOR") for c in celulas)
    )


def reconhece(file_bytes: bytes) -> bool:
    """True se o .xlsx é o "Extrato da conta" da Rico.

    Duas marcas, as duas no topo do arquivo: o título "Extrato da conta" e o
    cabeçalho Movimentação | Liquidação | Lançamento | Valor (R$). O nome da
    aba ("Planilha1") não serve para nada. Só o topo é lido, então o modo
    `read_only` de `xlsx_probe` basta aqui — e só aqui (ver docstring).
    """
    wb = abrir(file_bytes)
    if wb is None:
        return False
    try:
        for aba in wb.worksheets:
            try:
                brutas = primeiras_linhas(aba, _LINHAS_SONDA)
            except Exception:  # noqa: BLE001 — aba ilegível não é este extrato
                continue
            linhas = [_celulas(lin) for lin in brutas]
            titulo = any("EXTRATO DA CONTA" in c for lin in linhas for c in lin)
            if titulo and any(_e_cabecalho(lin) for lin in linhas):
                return True
        return False
    finally:
        fechar(wb)


# ─────────────────────────────────────────────────────────────────────────────
# Leitura pura (sem banco)
# ─────────────────────────────────────────────────────────────────────────────

def classificar_lancamento(texto: str) -> dict[str, Any] | None:
    """Provento contido no histórico do lançamento, ou None.

    >>> classificar_lancamento("RENDIMENTOS DE CLIENTES MANA11 S/   368")
    {'ticker': 'MANA11', 'tipo': 'reit_income', 'quantidade': 368.0}
    """
    t = " ".join(norm(texto or "").split())
    for padrao, tipo, tem_qtd in _PROVENTOS:
        m = padrao.match(t)
        if not m:
            continue
        qtd = None
        if tem_qtd:
            qtd = to_float_br(m.group(2).replace(".", ""))
        return {"ticker": m.group(1), "tipo": tipo, "quantidade": qtd}
    return None


def ler_lancamentos(linhas) -> tuple[list[dict[str, Any]], int]:
    """Proventos pagos do extrato e quantos lançamentos ficaram de fora.

    Só a primeira tabela conta: a partir de "Lançamentos futuros" é projeção.
    As colunas saem do cabeçalho, não de posição fixa.
    """
    proventos: list[dict[str, Any]] = []
    ignorados = 0
    idx: dict[str, int] | None = None

    for linha in linhas:
        celulas = [norm(str(c)) if c is not None else "" for c in linha]
        if idx is None:
            if _e_cabecalho([c for c in celulas if c]):
                idx = {
                    "mov": celulas.index("MOVIMENTACAO"),
                    "liq": celulas.index("LIQUIDACAO"),
                    "hist": celulas.index("LANCAMENTO"),
                    "valor": next(i for i, c in enumerate(celulas)
                                  if c.startswith("VALOR")),
                }
            continue
        if any(c.startswith("LANCAMENTOS FUTUROS") for c in celulas):
            break

        def _col(nome: str):
            i = idx[nome]
            return linha[i] if i < len(linha) else None

        data_mov = parse_date_br(_col("mov"))
        if data_mov is None:
            continue  # linha em branco ou rodapé, não é lançamento
        valor = to_float_br(_col("valor"))
        historico = str(_col("hist") or "")
        achado = classificar_lancamento(historico)
        if achado is None or valor is None or valor <= 0:
            ignorados += 1
            continue
        pagamento = parse_date_br(_col("liq")) or data_mov
        # None quando o extrato não diz: ausente fica ausente.
        qtd, por_unidade = quantidade_e_valor_unitario(achado["quantidade"], valor)
        proventos.append({
            "ticker": achado["ticker"],
            "tipo": achado["tipo"],
            "quantidade": qtd,
            "por_unidade": por_unidade,
            "total": round(valor, 2),
            "pagamento": pagamento,
        })
    return proventos, ignorados


# ─────────────────────────────────────────────────────────────────────────────
# Gravação
# ─────────────────────────────────────────────────────────────────────────────

def parse(file_bytes: bytes, engine: Engine) -> dict[str, Any]:
    """Grava em `dividends` os proventos pagos que o extrato traz."""
    summary = make_summary(SOURCE)
    user_id = settings.OWNER_USER_ID
    if not user_id:
        summary["status"] = "failed"
        summary["errors"].append("OWNER_USER_ID nao configurado.")
        return finalize_summary(summary)

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "failed"
        summary["errors"].append(f"Arquivo invalido: {safe_error(exc)}")
        return finalize_summary(summary)

    proventos: list[dict[str, Any]] = []
    for aba in wb.worksheets:
        achados, ignorados = ler_lancamentos(aba.iter_rows(values_only=True))
        proventos.extend(achados)
        summary["rows_skipped"] += ignorados

    if not proventos:
        return finalize_summary(summary)

    ensure_external_id_columns(engine)
    aceita_sem_qtd = (
        any(p["quantidade"] is None for p in proventos)
        and dividends_aceita_quantidade_vazia(engine)
    )

    try:
        with engine.connect() as conn:
            with conn.begin():
                for p in proventos:
                    pagamento: date = p["pagamento"]
                    if p["quantidade"] is None and not aceita_sem_qtd:
                        summary["rows_skipped"] += 1
                        summary["errors"].append(aviso_sem_quantidade(
                            p["ticker"], pagamento, p["total"],
                        ))
                        continue
                    try:
                        with conn.begin_nested():
                            asset_id = get_or_create_asset(
                                conn, ticker=p["ticker"], name=p["ticker"],
                                asset_class=classify_ticker(p["ticker"]),
                            )
                            novo = insert_dividend(
                                conn,
                                user_id=user_id,
                                asset_id=asset_id,
                                div_type=p["tipo"],
                                amount_per_unit=p["por_unidade"],
                                quantity=p["quantidade"],
                                total_amount=p["total"],
                                ex_date=None,
                                payment_date=pagamento,
                                external_id=make_external_id(
                                    "ricocc",
                                    [pagamento.isoformat(), p["ticker"],
                                     p["tipo"], f"{p['total']:.2f}"],
                                ),
                            )
                        if novo is None:
                            summary["duplicates_skipped"] += 1
                        else:
                            summary["incomes_imported"] += 1
                    except Exception as exc:  # noqa: BLE001
                        summary["errors"].append(
                            f"{p['ticker']} {pagamento:%d/%m/%Y}: "
                            f"{safe_error(exc)}"
                        )
    except Exception as exc:  # noqa: BLE001
        # A transação inteira voltou: nada do que foi contado ficou gravado.
        summary["incomes_imported"] = 0
        summary["duplicates_skipped"] = 0
        summary["errors"].append(f"Gravacao falhou: {safe_error(exc)}")

    return finalize_summary(summary)
