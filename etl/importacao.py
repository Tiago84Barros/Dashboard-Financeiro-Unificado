"""
etl/importacao.py
Camada de importação de dados de fontes externas para o schema unificado.

Fonte suportada: CSV / Excel — exportação manual de qualquer app ou planilha.

A importação direta dos bancos dos apps originais (ImportadorPostgres) foi
aposentada em 11/2026: a carga inicial já foi feita e nenhum ambiente
configura mais SOURCE_DB_APP1/2/3.

Segurança:
  - dry_run=True  (padrão) simula sem gravar nenhum dado
  - Toda gravação real ocorre dentro de uma transação (rollback automático em erro)
  - Nenhuma credencial de fonte é logada, exibida ou gravada no banco
  - ON CONFLICT DO NOTHING evita duplicatas em re-importações

Uso rápido:
    from etl.importacao import ImportadorCSV, ResultadoImportacao

    import pandas as pd
    df  = pd.read_csv("transacoes.csv")
    imp = ImportadorCSV()
    res = imp.importar_transacoes(df, usuario_id="uuid", conta_id="uuid", dry_run=True)
    print(res.resumo())
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.database import get_engine
from core.import_guard import (
    acquire_transaction_import_lock,
    import_payload_digest,
)

# ── Resultado de importação ───────────────────────────────────────────────────

@dataclass
class ResultadoImportacao:
    """Encapsula o resultado de uma operação de importação."""

    fonte: str
    tabela_destino: str
    total_lidos: int = 0
    total_inseridos: int = 0
    total_ignorados: int = 0
    erros: list[str] = field(default_factory=list)
    dry_run: bool = True

    @property
    def ok(self) -> bool:
        return len(self.erros) == 0

    def resumo(self) -> str:
        modo = " [DRY RUN — nada gravado]" if self.dry_run else " [GRAVADO]"
        erros_str = f", {len(self.erros)} erro(s)" if self.erros else ""
        return (
            f"{self.fonte} → {self.tabela_destino}{modo}: "
            f"{self.total_lidos} lidos, "
            f"{self.total_inseridos} válidos, "
            f"{self.total_ignorados} ignorados"
            f"{erros_str}"
        )


# ── Importador CSV / DataFrame ────────────────────────────────────────────────

class ImportadorCSV:
    """
    Importa dados a partir de arquivos CSV/Excel ou DataFrames pandas.

    Colunas mínimas por método
    ─────────────────────────────────────────────────────────────────
    importar_transacoes : descricao, valor, data_competencia, tipo
    importar_operacoes  : ticker, tipo, quantidade, preco_unitario, data_operacao
    importar_proventos  : ticker, tipo, valor_por_cota, quantidade, valor_total, data_pagamento
    """

    # ── Transações ────────────────────────────────────────────────────────────

    def importar_transacoes(
        self,
        df: pd.DataFrame,
        usuario_id: str,
        conta_id: str,
        categoria_id: Optional[str] = None,
        dry_run: bool = True,
    ) -> ResultadoImportacao:
        """
        Importa lançamentos financeiros para a tabela `transacoes`.

        Args:
            df:           DataFrame — colunas obrigatórias: descricao, valor,
                          data_competencia, tipo ('receita'|'despesa'|'transferencia')
                          Opcionais: data_pagamento, status, recorrente, origem
            usuario_id:   UUID do proprietário dos dados
            conta_id:     UUID da conta de destino
            categoria_id: UUID da categoria (None = sem categoria)
            dry_run:      True = simula sem gravar (padrão de segurança)
        """
        res = ResultadoImportacao(
            fonte="CSV", tabela_destino="transacoes", dry_run=dry_run
        )

        obrigatorias = {"descricao", "valor", "data_competencia", "tipo"}
        if not obrigatorias.issubset(df.columns):
            res.erros.append(f"Colunas ausentes no CSV: {obrigatorias - set(df.columns)}")
            return res

        engine = get_engine()
        if engine is None:
            res.erros.append(
                "Sem conexao com banco. Configure SUPABASE_UNIFICADO_URL "
                "no .env local ou em Streamlit Secrets (Settings > Secrets)."
            )
            return res

        registros: list[dict] = []
        for idx, row in df.iterrows():
            try:
                registros.append({
                    "id":               str(uuid.uuid4()),
                    "usuario_id":       usuario_id,
                    "conta_id":         conta_id,
                    "categoria_id":     categoria_id,
                    "descricao":        str(row["descricao"])[:255],
                    "valor":            float(row["valor"]),
                    "data_competencia": _parse_data(row["data_competencia"]),
                    "data_pagamento":   _parse_data(row.get("data_pagamento")),
                    "tipo":             str(row["tipo"]).lower(),
                    "status":           str(row.get("status", "liquidado")).lower(),
                    "recorrente":       bool(row.get("recorrente", False)),
                    "origem":           str(row.get("origem", "csv")),
                })
                res.total_lidos += 1
            except Exception as exc:
                res.erros.append(f"Linha {idx}: {exc}")
                res.total_ignorados += 1

        _assign_deterministic_ids("transacoes", registros)
        if not dry_run and registros:
            _inserir_em_lote(
                engine=engine,
                tabela="transacoes",
                colunas=[
                    "id", "usuario_id", "conta_id", "categoria_id", "descricao",
                    "valor", "data_competencia", "data_pagamento", "tipo",
                    "status", "recorrente", "origem",
                ],
                registros=registros,
                resultado=res,
            )
        elif dry_run:
            res.total_inseridos = len(registros)

        return res

    # ── Operações de investimento ─────────────────────────────────────────────

    def importar_operacoes(
        self,
        df: pd.DataFrame,
        usuario_id: str,
        dry_run: bool = True,
    ) -> ResultadoImportacao:
        """
        Importa compras e vendas de ativos para a tabela `operacoes`.

        Colunas obrigatórias: ticker, tipo ('compra'|'venda'), quantidade,
                              preco_unitario, data_operacao
        Opcionais: taxas, corretora
        """
        res = ResultadoImportacao(
            fonte="CSV", tabela_destino="operacoes", dry_run=dry_run
        )

        obrigatorias = {"ticker", "tipo", "quantidade", "preco_unitario", "data_operacao"}
        if not obrigatorias.issubset(df.columns):
            res.erros.append(f"Colunas ausentes: {obrigatorias - set(df.columns)}")
            return res

        engine = get_engine()
        if engine is None:
            res.erros.append("Sem conexão com banco.")
            return res

        # Resolve/cria ativos em lote antes de inserir (evita transações aninhadas)
        tickers_unicos = df["ticker"].str.upper().unique().tolist()
        mapa_ativo = _resolver_ativos_em_lote(engine, tickers_unicos, dry_run)

        registros: list[dict] = []
        for idx, row in df.iterrows():
            try:
                ticker = str(row["ticker"]).upper()
                registros.append({
                    "id":              str(uuid.uuid4()),
                    "usuario_id":      usuario_id,
                    "ativo_id":        mapa_ativo[ticker],
                    "tipo":            str(row["tipo"]).lower(),
                    "quantidade":      float(row["quantidade"]),
                    "preco_unitario":  float(row["preco_unitario"]),
                    "taxas":           float(row.get("taxas", 0)),
                    "data_operacao":   _parse_data(row["data_operacao"]),
                    "corretora":       str(row.get("corretora", ""))[:100] or None,
                })
                res.total_lidos += 1
            except Exception as exc:
                res.erros.append(f"Linha {idx}: {exc}")
                res.total_ignorados += 1

        _assign_deterministic_ids("operacoes", registros)
        if not dry_run and registros:
            _inserir_em_lote(
                engine=engine,
                tabela="operacoes",
                colunas=[
                    "id", "usuario_id", "ativo_id", "tipo",
                    "quantidade", "preco_unitario", "taxas",
                    "data_operacao", "corretora",
                ],
                registros=registros,
                resultado=res,
            )
        elif dry_run:
            res.total_inseridos = len(registros)

        return res

    # ── Proventos (dividendos, JCP, rendimentos) ──────────────────────────────

    def importar_proventos(
        self,
        df: pd.DataFrame,
        usuario_id: str,
        dry_run: bool = True,
    ) -> ResultadoImportacao:
        """
        Importa dividendos, JCP e rendimentos de FII para a tabela `proventos`.

        Colunas obrigatórias: ticker, tipo, valor_por_cota, quantidade,
                              valor_total, data_pagamento
        Opcionais: data_com
        """
        res = ResultadoImportacao(
            fonte="CSV", tabela_destino="proventos", dry_run=dry_run
        )

        obrigatorias = {
            "ticker", "tipo", "valor_por_cota",
            "quantidade", "valor_total", "data_pagamento",
        }
        if not obrigatorias.issubset(df.columns):
            res.erros.append(f"Colunas ausentes: {obrigatorias - set(df.columns)}")
            return res

        engine = get_engine()
        if engine is None:
            res.erros.append("Sem conexão com banco.")
            return res

        tickers_unicos = df["ticker"].str.upper().unique().tolist()
        mapa_ativo = _resolver_ativos_em_lote(engine, tickers_unicos, dry_run)

        registros: list[dict] = []
        for idx, row in df.iterrows():
            try:
                ticker = str(row["ticker"]).upper()
                registros.append({
                    "id":             str(uuid.uuid4()),
                    "usuario_id":     usuario_id,
                    "ativo_id":       mapa_ativo[ticker],
                    "tipo":           str(row["tipo"]).lower(),
                    "valor_por_cota": float(row["valor_por_cota"]),
                    "quantidade":     float(row["quantidade"]),
                    "valor_total":    float(row["valor_total"]),
                    "data_com":       _parse_data(row.get("data_com")),
                    "data_pagamento": _parse_data(row["data_pagamento"]),
                })
                res.total_lidos += 1
            except Exception as exc:
                res.erros.append(f"Linha {idx}: {exc}")
                res.total_ignorados += 1

        _assign_deterministic_ids("proventos", registros)
        if not dry_run and registros:
            _inserir_em_lote(
                engine=engine,
                tabela="proventos",
                colunas=[
                    "id", "usuario_id", "ativo_id", "tipo",
                    "valor_por_cota", "quantidade", "valor_total",
                    "data_com", "data_pagamento",
                ],
                registros=registros,
                resultado=res,
            )
        elif dry_run:
            res.total_inseridos = len(registros)

        return res


# ── Helpers internos ──────────────────────────────────────────────────────────

def _parse_data(valor: object) -> Optional[date]:
    """Converte string, datetime ou date para date. Retorna None em caso de falha."""
    if valor is None:
        return None
    if isinstance(valor, float) and pd.isna(valor):
        return None
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    if isinstance(valor, datetime):
        return valor.date()
    try:
        return pd.to_datetime(str(valor)).date()
    except Exception:
        return None


def _assign_deterministic_ids(tabela: str, registros: list[dict]) -> None:
    """
    Gera UUIDs estáveis a partir do conteúdo lógico da linha.

    O contador preserva ocorrências realmente repetidas dentro do mesmo
    arquivo, enquanto uma reimportação do mesmo conjunto recria os mesmos IDs.
    """
    occurrences: dict[str, int] = {}
    for registro in registros:
        logical = {key: value for key, value in registro.items() if key != "id"}
        row_digest = import_payload_digest(tabela, logical)
        occurrences[row_digest] = occurrences.get(row_digest, 0) + 1
        occurrence = occurrences[row_digest]
        registro["id"] = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"app4-generic-import|{tabela}|{row_digest}|{occurrence}",
            )
        )


def _resolver_ativos_em_lote(
    engine,
    tickers: list[str],
    dry_run: bool,
) -> dict[str, str]:
    """
    Retorna {ticker: ativo_id} para todos os tickers informados.
    Cria ativos ausentes com classe='indefinida' se não estiverem em dry_run.
    Em dry_run, retorna UUIDs fictícios sem gravar no banco.
    """
    if dry_run:
        return {t.upper(): str(uuid.uuid4()) for t in tickers}

    mapa: dict[str, str] = {}
    tickers_upper = [t.upper() for t in tickers]

    with engine.begin() as conn:
        # Busca todos de uma vez
        placeholders = ", ".join(f":t{i}" for i in range(len(tickers_upper)))
        params = {f"t{i}": t for i, t in enumerate(tickers_upper)}
        resultado = conn.execute(
            text(f"SELECT id, ticker FROM ativos WHERE ticker IN ({placeholders})"),
            params,
        ).fetchall()

        for row in resultado:
            mapa[row[1]] = str(row[0])

        # Cria os que não existem
        ausentes = [t for t in tickers_upper if t not in mapa]
        for ticker in ausentes:
            created = conn.execute(
                text("""
                    INSERT INTO ativos (ticker, nome, classe)
                    VALUES (:ticker, :nome, 'indefinida')
                    ON CONFLICT (ticker) DO UPDATE SET nome = EXCLUDED.nome
                    RETURNING id
                """),
                {"ticker": ticker, "nome": ticker},
            ).fetchone()
            if created:
                mapa[ticker] = str(created.id)

    return mapa


def _inserir_em_lote(
    engine,
    tabela: str,
    colunas: list[str],
    registros: list[dict],
    resultado: ResultadoImportacao,
) -> None:
    """
    Insere registros em lote dentro de uma transação.
    ON CONFLICT DO NOTHING evita erros em re-importações.
    Atualiza resultado.total_inseridos e resultado.erros.
    """
    if not registros:
        return

    cols_sql = ", ".join(f'"{c}"' for c in colunas)
    vals_sql = ", ".join(f":{c}" for c in colunas)

    try:
        with engine.begin() as conn:
            acquire_transaction_import_lock(
                conn,
                f"generic-table-{tabela}",
                [
                    {key: value for key, value in registro.items() if key != "id"}
                    for registro in registros
                ],
            )
            result = conn.execute(
                text(f"""
                    INSERT INTO "{tabela}" ({cols_sql})
                    VALUES ({vals_sql})
                    ON CONFLICT DO NOTHING
                """),
                registros,
            )
        inserted = result.rowcount
        if inserted is None or inserted < 0:
            inserted = len(registros)
        resultado.total_inseridos = int(inserted)
        resultado.total_ignorados += max(len(registros) - int(inserted), 0)
    except SQLAlchemyError as exc:
        resultado.erros.append(f"Erro ao inserir em {tabela}: {exc}")
