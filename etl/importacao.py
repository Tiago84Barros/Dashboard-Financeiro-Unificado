"""
etl/importacao.py
Camada de importação de dados de fontes externas para o schema unificado.

Fontes suportadas:
  1. CSV / Excel  — exportação manual de qualquer app ou planilha
  2. PostgreSQL   — conexão direta ao banco dos apps originais

Segurança:
  - dry_run=True  (padrão) simula sem gravar nenhum dado
  - Toda gravação real ocorre dentro de uma transação (rollback automático em erro)
  - Nenhuma credencial de fonte é logada, exibida ou gravada no banco
  - ON CONFLICT DO NOTHING evita duplicatas em re-importações

Uso rápido:
    from etl.importacao import ImportadorCSV, ImportadorPostgres, ResultadoImportacao

    # --- CSV ---
    import pandas as pd
    df  = pd.read_csv("transacoes.csv")
    imp = ImportadorCSV()
    res = imp.importar_transacoes(df, usuario_id="uuid", conta_id="uuid", dry_run=True)
    print(res.resumo())

    # --- PostgreSQL ---
    imp2 = ImportadorPostgres(source_url="postgresql://...")
    if imp2.conectado:
        print(imp2.listar_tabelas())

Mapeamentos dos apps originais (App 1, 2 e 3):
  Implementados como placeholder — preencher após auditar cada repositório.
  Ver métodos: importar_app1_dashboard(), importar_app2_investimentos(), importar_app3_controle()
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import inspect as sa_inspect

from core.database import get_engine

# Tabelas permitidas como destino (evita injeção via nomes de tabela)
_TABELAS_VALIDAS = {
    "usuarios", "contas", "categorias", "transacoes",
    "orcamentos", "metas", "ativos", "operacoes", "proventos", "cotacoes",
}

# Palavras-chave proibidas no filtro_sql para prevenir operacoes destrutivas
_PALAVRAS_PROIBIDAS_FILTRO: frozenset = frozenset({
    "drop", "delete", "truncate", "insert", "update", "--", ";",
})


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


# ── Importador PostgreSQL → PostgreSQL ────────────────────────────────────────

class ImportadorPostgres:
    """
    Importa dados de outro banco PostgreSQL para o schema unificado.

    Conecta à fonte (apps originais) via SQLAlchemy e copia para o destino
    (banco configurado em DATABASE_URL).

    Uso:
        imp = ImportadorPostgres("postgresql://user:pass@host/db")
        if imp.conectado:
            tabelas = imp.listar_tabelas()
            res = imp.importar_tabela_generica(
                tabela_fonte="lancamentos",
                tabela_destino="transacoes",
                mapeamento={
                    # destino       : fonte
                    "descricao"     : "descricao",
                    "valor"         : "valor",
                    "data_competencia": "data",
                    "tipo"          : "tipo",
                },
                dry_run=True,
            )
    """

    def __init__(self, source_url: str) -> None:
        self.source_url = source_url
        self._engine_fonte = None
        self.conectado: bool = False
        self.erro_conexao: str = ""
        self._conectar()

    def _conectar(self) -> None:
        if not self.source_url:
            self.erro_conexao = "URL da fonte não configurada."
            return
        try:
            # connect_timeout e especifico de PostgreSQL — omitir para SQLite
            is_sqlite = self.source_url.startswith("sqlite")
            connect_args = {} if is_sqlite else {"connect_timeout": 10}
            engine = create_engine(
                self.source_url,
                connect_args=connect_args,
                pool_pre_ping=True,
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            self._engine_fonte = engine
            self.conectado = True
        except Exception as exc:
            self.erro_conexao = str(exc)
            self.conectado = False

    def listar_tabelas(self) -> list[str]:
        """Lista as tabelas disponíveis no banco de origem."""
        if not self.conectado or self._engine_fonte is None:
            return []
        return sa_inspect(self._engine_fonte).get_table_names()

    def listar_colunas(self, tabela: str) -> list[str]:
        """Lista as colunas de uma tabela no banco de origem."""
        if not self.conectado or self._engine_fonte is None:
            return []
        return [
            c["name"]
            for c in sa_inspect(self._engine_fonte).get_columns(tabela)
        ]

    def importar_tabela_generica(
        self,
        tabela_fonte: str,
        tabela_destino: str,
        mapeamento: dict[str, str],
        filtro_sql: str = "",
        dry_run: bool = True,
        limite: int = 50_000,
    ) -> ResultadoImportacao:
        """
        Copia dados de qualquer tabela da fonte para o destino.

        Args:
            tabela_fonte:   Nome da tabela no banco de origem
            tabela_destino: Nome da tabela no schema unificado (deve ser válida)
            mapeamento:     {coluna_destino: coluna_fonte} — mapeamento de campos
            filtro_sql:     Cláusula WHERE opcional (ex: "WHERE ativo = true")
            dry_run:        True = conta registros sem gravar
            limite:         Máximo de registros por execução (proteção)
        """
        res = ResultadoImportacao(
            fonte=f"PostgreSQL/{tabela_fonte}",
            tabela_destino=tabela_destino,
            dry_run=dry_run,
        )

        if tabela_destino not in _TABELAS_VALIDAS:
            res.erros.append(
                f"Tabela destino inválida: '{tabela_destino}'. "
                f"Permitidas: {sorted(_TABELAS_VALIDAS)}"
            )
            return res

        if filtro_sql and any(
            p in filtro_sql.lower() for p in _PALAVRAS_PROIBIDAS_FILTRO
        ):
            res.erros.append(
                "filtro_sql contém palavra-chave não permitida. "
                "Use apenas cláusulas WHERE de leitura (ex: WHERE ativo = true)."
            )
            return res

        if not self.conectado or self._engine_fonte is None:
            res.erros.append(f"Fonte não conectada. {self.erro_conexao}")
            return res

        engine_destino = get_engine()
        if engine_destino is None:
            res.erros.append("Sem conexão com banco de destino.")
            return res

        colunas_fonte = list(mapeamento.values())
        cols_select = ", ".join(f'"{c}"' for c in colunas_fonte)
        query_sql = f'SELECT {cols_select} FROM "{tabela_fonte}" {filtro_sql} LIMIT {limite}'

        try:
            with self._engine_fonte.connect() as conn_fonte:
                result = conn_fonte.execute(text(query_sql))
                linhas = result.fetchall()
                colunas_resultado = list(result.keys())

            res.total_lidos = len(linhas)

            if dry_run:
                res.total_inseridos = len(linhas)
                return res

            # Transforma (renomeia colunas conforme mapeamento) e insere
            cols_destino = list(mapeamento.keys())
            registros = [
                {dest: dict(zip(colunas_resultado, linha)).get(src)
                 for dest, src in mapeamento.items()}
                for linha in linhas
            ]

            _inserir_em_lote(
                engine=engine_destino,
                tabela=tabela_destino,
                colunas=cols_destino,
                registros=registros,
                resultado=res,
            )

        except SQLAlchemyError as exc:
            res.erros.append(str(exc))

        return res

    # ── Importadores por app (placeholder — mapeamento pendente de auditoria) ─

    def importar_app1_dashboard(
        self,
        usuario_id_destino: str,
        dry_run: bool = True,
    ) -> list[ResultadoImportacao]:
        """
        Importa dados do repositório Tiago84Barros/Dashboard.

        STATUS: Placeholder — requer auditoria do schema original.
        TODO:   Auditar tabelas do repositório e preencher o mapeamento abaixo.

        Estimativa das tabelas a mapear:
          → transacoes (ou equivalente) → transacoes
          → contas (ou equivalente)     → contas
        """
        raise NotImplementedError(
            "Mapeamento do App 1 (Dashboard) pendente de auditoria. "
            "Execute: ImportadorPostgres(url).listar_tabelas() e listar_colunas() "
            "para inspecionar o schema do repositório Tiago84Barros/Dashboard."
        )

    def importar_app2_investimentos(
        self,
        usuario_id_destino: str,
        dry_run: bool = True,
    ) -> list[ResultadoImportacao]:
        """
        Importa dados do repositório Tiago84Barros/Dashboard-Investimentos.

        STATUS: Placeholder — requer auditoria do schema original.
        TODO:   Auditar tabelas do repositório e preencher o mapeamento abaixo.

        Estimativa das tabelas a mapear:
          → ativos (ou equivalente)    → ativos
          → operacoes (ou equivalente) → operacoes
          → proventos (ou equivalente) → proventos
        """
        raise NotImplementedError(
            "Mapeamento do App 2 (Investimentos) pendente de auditoria. "
            "Execute: ImportadorPostgres(url).listar_tabelas() e listar_colunas() "
            "para inspecionar o schema do repositório Tiago84Barros/Dashboard-Investimentos."
        )

    def importar_app3_controle(
        self,
        usuario_id_destino: str,
        dry_run: bool = True,
    ) -> list[ResultadoImportacao]:
        """
        Importa dados do repositório Tiago84Barros/Controle_Financeiro.

        STATUS: Placeholder — requer auditoria do schema original.
        TODO:   Auditar tabelas do repositório e preencher o mapeamento abaixo.

        Estimativa das tabelas a mapear:
          → lançamentos (ou equivalente) → transacoes
          → categorias (ou equivalente)  → categorias
          → orçamentos (ou equivalente)  → orcamentos
          → metas (ou equivalente)       → metas
        """
        raise NotImplementedError(
            "Mapeamento do App 3 (Controle Financeiro) pendente de auditoria. "
            "Execute: ImportadorPostgres(url).listar_tabelas() e listar_colunas() "
            "para inspecionar o schema do repositório Tiago84Barros/Controle_Financeiro."
        )


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
            novo_id = str(uuid.uuid4())
            conn.execute(
                text("""
                    INSERT INTO ativos (id, ticker, nome, classe)
                    VALUES (:id, :ticker, :nome, 'indefinida')
                    ON CONFLICT (ticker) DO NOTHING
                """),
                {"id": novo_id, "ticker": ticker, "nome": ticker},
            )
            mapa[ticker] = novo_id

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
            conn.execute(
                text(f"""
                    INSERT INTO "{tabela}" ({cols_sql})
                    VALUES ({vals_sql})
                    ON CONFLICT DO NOTHING
                """),
                registros,
            )
        resultado.total_inseridos = len(registros)
    except SQLAlchemyError as exc:
        resultado.erros.append(f"Erro ao inserir em {tabela}: {exc}")
