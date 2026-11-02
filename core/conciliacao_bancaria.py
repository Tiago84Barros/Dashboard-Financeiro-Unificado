"""
Conciliação bancária do extrato importado.

Duas perguntas que o importador sozinho não responde:

1. **Este movimento do banco já foi lançado à mão?** Antes, cada linha do
   extrato virava um lançamento novo em ``transactions``. Quem lança à mão e
   também importa o extrato ficava com a despesa duas vezes, e o saldo da
   conta (``v_account_balance``) inflava em silêncio. Aqui cada linha procura
   um lançamento manual da mesma conta com o mesmo valor, até ``JANELA_DIAS``
   de distância, e ainda sem par. Achou: o movimento se vincula a ele em vez
   de criar outro.

2. **O saldo do app bate com o do banco?** O saldo final impresso no extrato é
   a prova da conciliação. Antes, o parser jogava essas linhas fora. Agora o
   saldo é lido (ou digitado), guardado em ``bank_statement_balances`` e
   comparado com ``saldo inicial da conta + lançamentos pagos até a data``.
   Diferença diferente de zero quer dizer que algum lançamento falta ou sobra.

As funções puras (sem banco) ficam em cima, testáveis isoladamente; o acesso
ao Postgres fica embaixo.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text

from core.bank_statement_import import (
    _DATE_ANY_RE,
    _MONEY_RE,
    _ensure_tables,
    _infer_statement_year,
    _maybe_owner_id,
    _norm,
    _parse_date,
    _parse_money,
    _resolve_bank_statement_account,
)
from core.config import settings
from core.database import get_engine

logger = logging.getLogger(__name__)

JANELA_DIAS = 3
SIMILARIDADE_FORTE = 0.5
TOLERANCIA_SALDO = 0.01

# Palavras que o banco põe em toda linha e que não dizem nada sobre o que foi
# pago. Sem tirá-las, "Pix enviado para JOAO" e "Pix enviado para MARIA"
# pareceriam a mesma coisa.
_PALAVRAS_GENERICAS = frozenset({
    "pix", "enviado", "enviada", "recebido", "recebida", "para", "de", "do",
    "da", "dos", "das", "pagamento", "pagto", "pgto", "compra", "debito",
    "credito", "transferencia", "ted", "doc", "boleto", "saida", "entrada",
    "conta", "c6", "bank", "ltda", "me", "sa", "com", "br",
})

_SALDO_INICIAL = ("saldo anterior", "saldo inicial")
_SALDO_FINAL = ("saldo final", "saldo do dia", "saldo em conta",
                "saldo disponivel", "saldo atual")


# ─────────────────────────────────────────────────────────────────────────────
# Funções puras
# ─────────────────────────────────────────────────────────────────────────────

def extrair_saldos_extrato(texto: str) -> dict:
    """Lê do texto do extrato o saldo inicial e o final, com a data de cada um.

    O parser de movimentos descarta as linhas de saldo de propósito (não são
    movimento). Esta função lê justamente elas. Saldo final é o de data mais
    recente; sem data na linha, vale a ordem em que aparece. Devolve
    ``{"inicial": {"data", "valor"} | None, "final": {...} | None}``.
    """
    ano_ref = _infer_statement_year(texto or "")
    iniciais: list[tuple[int, date | None, float]] = []
    finais: list[tuple[int, date | None, float]] = []
    for ordem, linha in enumerate(str(texto or "").splitlines()):
        norm = _norm(linha)
        if "saldo" not in norm:
            continue
        valores = list(_MONEY_RE.finditer(linha))
        if not valores:
            continue
        valor = round(_parse_money(valores[-1].group(0)), 2)
        # "R$ 1.000,00 D" ou "-R$ 1.000,00": saldo devedor.
        if re.search(r"\d\s*D\b", linha[valores[-1].start():]):
            valor = -abs(valor)
        datas = [_parse_date(d, ano_ref) for d in _DATE_ANY_RE.findall(linha)]
        datas = [d for d in datas if d is not None]
        item = (ordem, datas[-1] if datas else None, valor)
        if any(t in norm for t in _SALDO_INICIAL):
            iniciais.append(item)
        elif any(t in norm for t in _SALDO_FINAL):
            finais.append(item)

    def _chave(item: tuple[int, date | None, float]) -> tuple:
        ordem, data, _ = item
        return (data or date.min, ordem)

    def _como_dict(item):
        return {"data": item[1], "valor": item[2]} if item else None

    inicial = min(iniciais, key=_chave) if iniciais else None
    final = max(finais, key=_chave) if finais else None
    return {"inicial": _como_dict(inicial), "final": _como_dict(final)}


def _tokens(descricao: object) -> set[str]:
    return {t for t in _norm(descricao).split()
            if len(t) >= 3 and t not in _PALAVRAS_GENERICAS and not t.isdigit()}


def similaridade_descricao(a: object, b: object) -> float:
    """0 a 1. Maior entre a sobreposição de palavras e a semelhança de texto,
    ambas sem as palavras genéricas do banco."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    texto = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(jaccard, texto), 3)


def _como_data(valor: object) -> date | None:
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        return None


def sugerir_vinculos(
    movimentos: list[dict],
    lancamentos: list[dict],
    janela_dias: int = JANELA_DIAS,
) -> dict[int, dict]:
    """Casa movimentos do extrato com lançamentos manuais, um para um.

    Par possível: mesmo valor com sinal (ao centavo) e datas a no máximo
    ``janela_dias`` uma da outra. Entre os possíveis, ganha quem tem descrição
    mais parecida e data mais próxima. A atribuição é gulosa pela melhor nota,
    e nenhum lançamento serve a dois movimentos.

    ``movimentos``: dicts com ``data_movimento``, ``valor`` e
    ``descricao_original``. ``lancamentos``: dicts com ``id``, ``due_date``,
    ``amount``, ``description`` e ``category_id``. Devolve
    ``{índice do movimento: vínculo}``.
    """
    candidatos: list[tuple[float, int, int, int, float]] = []
    for i, mov in enumerate(movimentos):
        d_mov = _como_data(mov.get("data_movimento"))
        if d_mov is None:
            continue
        valor = round(float(mov.get("valor") or 0.0), 2)
        for j, tx in enumerate(lancamentos):
            if round(float(tx.get("amount") or 0.0), 2) != valor:
                continue
            d_tx = _como_data(tx.get("due_date"))
            if d_tx is None:
                continue
            dias = abs((d_tx - d_mov).days)
            if dias > janela_dias:
                continue
            sim = similaridade_descricao(mov.get("descricao_original"), tx.get("description"))
            nota = sim - 0.15 * dias
            candidatos.append((nota, i, j, dias, sim))

    candidatos.sort(key=lambda c: (-c[0], c[3], c[1], c[2]))
    vinculos: dict[int, dict] = {}
    usados: set[int] = set()
    for _nota, i, j, dias, sim in candidatos:
        if i in vinculos or j in usados:
            continue
        tx = lancamentos[j]
        usados.add(j)
        vinculos[i] = {
            "transaction_id": str(tx.get("id")),
            "descricao": str(tx.get("description") or ""),
            "data": _como_data(tx.get("due_date")),
            "valor": round(float(tx.get("amount") or 0.0), 2),
            "categoria_id": (str(tx["category_id"]) if tx.get("category_id") else None),
            "dias": dias,
            "similaridade": sim,
            "forca": "forte" if dias == 0 or sim >= SIMILARIDADE_FORTE else "possivel",
        }
    return vinculos


def conferir_saldo(
    saldo_extrato: float,
    saldo_app: float,
    a_lancar: float = 0.0,
    pendente: float = 0.0,
) -> dict:
    """Compara o saldo do banco com o do app depois da importação.

    ``saldo_app``: o que o app já tem na data do saldo. ``a_lancar``: soma das
    linhas que esta importação vai gravar como lançamento novo. ``pendente``:
    soma das linhas sem categoria, que entram no extrato mas não no saldo até
    serem classificadas -- a causa mais comum de diferença, por isso é nomeada.
    """
    projetado = round(float(saldo_app) + float(a_lancar), 2)
    diferenca = round(float(saldo_extrato) - projetado, 2)
    fecha = abs(diferenca) < TOLERANCIA_SALDO
    return {
        "saldo_extrato": round(float(saldo_extrato), 2),
        "saldo_app": round(float(saldo_app), 2),
        "a_lancar": round(float(a_lancar), 2),
        "saldo_projetado": projetado,
        "pendente": round(float(pendente), 2),
        "diferenca": diferenca,
        "fecha": fecha,
        "explicada_por_pendentes": (
            not fecha and abs(pendente) >= TOLERANCIA_SALDO
            and abs(diferenca - pendente) < TOLERANCIA_SALDO
        ),
    }


def impacto_no_saldo(rows: list[dict], data_saldo: date | None) -> tuple[float, float]:
    """(a lançar, pendente) das linhas que esta importação grava até a data.

    Linha vinculada a lançamento manual não soma: o valor já está no app. Linha
    já importada antes (``ja_importado``) também não: o banco a recusa.
    """
    a_lancar = pendente = 0.0
    for row in rows:
        if row.get("ja_importado") or row.get("vincular_transaction_id"):
            continue
        d = _como_data(row.get("data_movimento"))
        if data_saldo is not None and (d is None or d > data_saldo):
            continue
        valor = float(row.get("valor") or 0.0)
        if row.get("categoria_id"):
            a_lancar += valor
        else:
            pendente += valor
    return round(a_lancar, 2), round(pendente, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Banco
# ─────────────────────────────────────────────────────────────────────────────

_SQL_SALDO_APP_ATE = """
    SELECT a.initial_balance
           + COALESCE(SUM(t.amount) FILTER (
                 WHERE t.status = 'settled' AND t.due_date <= :data), 0)
           AS saldo
    FROM accounts a
    LEFT JOIN transactions t
           ON t.account_id = a.id AND t.user_id = a.user_id
    WHERE a.id = CAST(:account_id AS uuid)
      AND a.user_id = CAST(:uid AS uuid)
    GROUP BY a.initial_balance
"""

# Candidatos ao vínculo: lançamento manual pago da mesma conta, na janela de
# datas do extrato, que nenhum movimento importado ainda reclamou.
_SQL_LANCAMENTOS_MANUAIS = """
    SELECT t.id::text, t.due_date, t.amount, t.description, t.category_id::text
    FROM transactions t
    WHERE t.user_id = CAST(:uid AS uuid)
      AND t.account_id = CAST(:account_id AS uuid)
      AND COALESCE(t.source, '') = 'manual'
      AND t.status = 'settled'
      AND t.due_date BETWEEN :inicio AND :fim
      AND NOT EXISTS (
          SELECT 1 FROM bank_statement_movements m
          WHERE m.transaction_id = t.id AND m.user_id = t.user_id
      )
    ORDER BY t.due_date
"""

_SQL_HASHES_EXISTENTES = """
    SELECT hash_lancamento FROM bank_statement_movements
    WHERE user_id = CAST(:uid AS uuid) AND hash_lancamento = ANY(:hashes)
"""

_SQL_REGISTRAR_SALDO = """
    INSERT INTO bank_statement_balances
        (user_id, account_id, banco, data_saldo, saldo_extrato, origem_arquivo)
    VALUES (CAST(:uid AS uuid), CAST(:account_id AS uuid), :banco, :data,
            :valor, :arquivo)
    ON CONFLICT (user_id, account_id, data_saldo) DO UPDATE
       SET saldo_extrato = EXCLUDED.saldo_extrato,
           banco = EXCLUDED.banco,
           origem_arquivo = EXCLUDED.origem_arquivo,
           updated_at = NOW()
"""

_SQL_ULTIMOS_SALDOS = """
    SELECT b.account_id::text, a.name AS conta, b.banco, b.data_saldo,
           b.saldo_extrato, b.origem_arquivo
    FROM bank_statement_balances b
    JOIN accounts a ON a.id = b.account_id
    WHERE b.user_id = CAST(:uid AS uuid)
    ORDER BY b.data_saldo DESC
    LIMIT :limit
"""


def saldo_app_ate(conn, owner: str, account_id: str, data: date) -> float:
    """Saldo inicial da conta + lançamentos pagos com data até ``data``."""
    row = conn.execute(text(_SQL_SALDO_APP_ATE),
                       {"uid": owner, "account_id": account_id, "data": data}).fetchone()
    return round(float(row.saldo or 0.0), 2) if row else 0.0


def lancamentos_manuais_candidatos(conn, owner: str, account_id: str,
                                   inicio: date, fim: date) -> list[dict]:
    rows = conn.execute(text(_SQL_LANCAMENTOS_MANUAIS), {
        "uid": owner, "account_id": account_id,
        "inicio": inicio - timedelta(days=JANELA_DIAS),
        "fim": fim + timedelta(days=JANELA_DIAS),
    }).fetchall()
    return [dict(r._mapping) for r in rows]


def registrar_saldo_extrato(conn, owner: str, account_id: str, banco: str,
                            data: date, valor: float, arquivo: str | None) -> None:
    conn.execute(text(_SQL_REGISTRAR_SALDO), {
        "uid": owner, "account_id": account_id, "banco": banco, "data": data,
        "valor": round(float(valor), 2), "arquivo": arquivo,
    })


def preparar_conciliacao(rows: list[dict], data_saldo: date | None = None) -> dict:
    """Tudo o que a prévia precisa para conciliar, numa conexão só.

    Marca ``ja_importado`` nas linhas que o banco já tem, sugere o vínculo das
    demais com lançamentos manuais e, com ``data_saldo``, devolve o saldo do
    app naquela data. Sem banco, devolve a estrutura vazia com ``ok=False`` --
    a prévia segue funcionando sem conciliação.
    """
    vazio: dict[str, Any] = {"ok": False, "conta": None, "vinculos": {},
                             "ja_importados": set(), "saldo_app": None}
    if settings.MOCK_MODE or not rows:
        return vazio
    engine = get_engine()
    owner = _maybe_owner_id()
    if engine is None or not owner:
        return vazio

    datas = [d for d in (_como_data(r.get("data_movimento")) for r in rows) if d]
    if not datas:
        return vazio
    from core.bank_statement_import import build_bank_statement_hash

    try:
        with engine.begin() as conn:
            _ensure_tables(conn)
            conta = _resolve_bank_statement_account(conn, owner, None)
            if not conta:
                return vazio
            hashes = [r.get("hash_lancamento") or build_bank_statement_hash(r) for r in rows]
            existentes = {
                r.hash_lancamento for r in conn.execute(
                    text(_SQL_HASHES_EXISTENTES), {"uid": owner, "hashes": hashes})
            }
            ja_importados = {i for i, h in enumerate(hashes) if h in existentes}
            lancamentos = lancamentos_manuais_candidatos(
                conn, owner, conta.id, min(datas), max(datas))
            novos = [i for i in range(len(rows)) if i not in ja_importados]
            vinc_novos = sugerir_vinculos([rows[i] for i in novos], lancamentos)
            vinculos = {novos[k]: v for k, v in vinc_novos.items()}
            saldo_app = (saldo_app_ate(conn, owner, conta.id, data_saldo)
                         if data_saldo else None)
    except Exception as exc:  # noqa: BLE001 -- conciliação é ajuda, não trava a prévia
        logger.warning("[conciliacao] falha ao preparar: %s", exc)
        return {**vazio, "erro": str(exc)}

    return {
        "ok": True,
        "conta": {"id": conta.id, "nome": conta.name},
        "vinculos": vinculos,
        "ja_importados": ja_importados,
        "saldo_app": saldo_app,
    }


def saldo_app_na_data(data: date) -> float | None:
    """Saldo do app na conta do extrato, numa data. ``None`` sem banco."""
    if settings.MOCK_MODE:
        return None
    engine = get_engine()
    owner = _maybe_owner_id()
    if engine is None or not owner:
        return None
    try:
        with engine.begin() as conn:
            conta = _resolve_bank_statement_account(conn, owner, None)
            if not conta:
                return None
            return saldo_app_ate(conn, owner, conta.id, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[conciliacao] falha ao ler saldo: %s", exc)
        return None


def ultimas_conferencias(limit: int = 6) -> list[dict]:
    """Saldos de extrato guardados, cada um comparado ao saldo do app AGORA.

    O saldo do app muda quando um pendente é classificado ou um lançamento
    manual é corrigido, então a diferença é recalculada a cada leitura, nunca
    gravada.
    """
    if settings.MOCK_MODE:
        return []
    engine = get_engine()
    owner = _maybe_owner_id()
    if engine is None or not owner:
        return []
    try:
        with engine.begin() as conn:
            _ensure_tables(conn)
            saldos = conn.execute(text(_SQL_ULTIMOS_SALDOS),
                                  {"uid": owner, "limit": int(limit)}).fetchall()
            out = []
            for s in saldos:
                app = saldo_app_ate(conn, owner, s.account_id, s.data_saldo)
                conf = conferir_saldo(float(s.saldo_extrato), app)
                out.append({"conta": s.conta, "banco": s.banco,
                            "data_saldo": s.data_saldo,
                            "origem_arquivo": s.origem_arquivo, **conf})
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[conciliacao] falha ao listar conferências: %s", exc)
        return []
