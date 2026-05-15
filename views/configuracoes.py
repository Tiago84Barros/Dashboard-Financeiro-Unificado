"""
pages/configuracoes.py
Central de configuracao, schema e importacao de dados.

Secoes:
  1. Seguranca    — status de autenticacao e sessao
  2. Banco        — status de conexao + inicializacao do schema
  3. Importacao   — CSV/Excel e banco de origem (apps originais)
  4. Setup        — instrucoes de configuracao
"""
from __future__ import annotations

import io
import os

import pandas as pd
import streamlit as st

from core.auth import encerrar_sessao, esta_autenticado
from core.config import settings
from core.database import get_db_status
from design.componentes import container_pagina, secao_titulo


def render() -> None:
    container_pagina("Configuracoes", "Banco de dados, importacao e seguranca", "⚙️")

    tab_banco, tab_import, tab_cotacoes, tab_seguranca, tab_setup = st.tabs([
        "🗄️ Banco de Dados",
        "📥 Importacao de Dados",
        "📊 Cotacoes",
        "🔒 Seguranca",
        "📋 Setup",
    ])

    with tab_banco:
        _render_banco()

    with tab_import:
        _render_importacao()

    with tab_cotacoes:
        _render_cotacoes()

    with tab_seguranca:
        _render_seguranca()

    with tab_setup:
        _render_setup()


# ── Tab 1: Banco de Dados ─────────────────────────────────────────────────────

def _render_banco() -> None:
    secao_titulo("Status da Conexao", "🔌")

    status = get_db_status()
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        if status["configurado"]:
            st.success("DATABASE_URL configurada")
        else:
            st.error("DATABASE_URL ausente")

    with c2:
        if status["conectado"]:
            st.success("Banco conectado")
        else:
            st.warning("Banco nao conectado")

    with c3:
        if status["mock_mode"]:
            st.warning("Modo mock ativo")
        else:
            st.success("Dados reais")

    with c4:
        if settings.has_owner:
            st.success("OWNER_USER_ID configurado")
        else:
            st.warning("OWNER_USER_ID ausente")

    st.divider()

    # ── Status do schema ──────────────────────────────────────────────────────
    secao_titulo("Schema do Banco", "📋", "Tabelas esperadas pelo app unificado")

    if not status["configurado"]:
        st.info(
            "Configure `SUPABASE_UNIFICADO_URL` no `.env` local ou em "
            "**Streamlit Secrets** (Settings > Secrets) para verificar o schema.",
            icon="ℹ️",
        )
    else:
        from etl.schema_setup import TABELAS_ESPERADAS, verificar_schema

        with st.spinner("Verificando tabelas..."):
            presenca = verificar_schema()

        total = len(TABELAS_ESPERADAS)
        existentes = sum(1 for v in presenca.values() if v)
        ausentes = total - existentes

        col_prog, col_btn = st.columns([3, 1])
        with col_prog:
            st.progress(existentes / total if total else 0)
            st.caption(f"{existentes}/{total} tabelas presentes")

        with col_btn:
            if ausentes > 0:
                if settings.has_supabase_unificado:
                    with st.expander("⚠️ Banco unificado — expandir para confirmar"):
                        st.warning(
                            "**ATENCAO:** `SUPABASE_UNIFICADO_URL` detectada.\n\n"
                            "Este botao executara DDL diretamente no banco unificado "
                            "(projeto Dashboard Financeiro). "
                            "A estrategia definida (Fases 4.4/4.5) recomenda execucao "
                            "manual no SQL Editor do Supabase apos revisao dos scripts "
                            "em `supabase_unificado/schema/`.\n\n"
                            "**Pre-requisito obrigatorio:** backup verificado.",
                            icon="⚠️",
                        )
                        confirmar = st.checkbox(
                            "Li os scripts em supabase_unificado/schema/, "
                            "fiz o backup e quero criar as tabelas via app.",
                            key="_confirmar_criar_schema",
                        )
                        if confirmar:
                            if st.button(
                                f"Criar {ausentes} tabela(s) no banco unificado",
                                type="primary",
                                use_container_width=True,
                                key="_btn_criar_schema_unificado",
                            ):
                                _executar_criar_schema()
                else:
                    if st.button(
                        f"Criar {ausentes} tabela(s)",
                        type="primary",
                        use_container_width=True,
                    ):
                        _executar_criar_schema()
            else:
                st.success("Schema completo ✓")

        # Tabela de status
        dados_status = [
            {
                "Tabela": t,
                "Status": "✅ Existe" if presenca[t] else "❌ Ausente",
            }
            for t in TABELAS_ESPERADAS
        ]
        st.dataframe(
            pd.DataFrame(dados_status),
            use_container_width=True,
            hide_index=True,
        )


def _executar_criar_schema() -> None:
    from etl.schema_setup import criar_schema

    with st.spinner("Criando tabelas..."):
        result = criar_schema()

    if result["ok"]:
        if result["criadas"]:
            st.success(f"Criadas: {', '.join(result['criadas'])}")
        if result["ja_existiam"]:
            st.info(f"Ja existiam: {', '.join(result['ja_existiam'])}")
    else:
        st.error("Erros ao criar schema:")
        for erro in result["erros"]:
            st.code(erro)

    st.rerun()


# ── Tab 2: Importacao de Dados ────────────────────────────────────────────────

def _render_importacao() -> None:
    secao_titulo("Importacao de Dados", "📥", "Importe dados historicos dos apps originais")

    if not settings.has_database:
        st.warning(
            "Configure `SUPABASE_UNIFICADO_URL` no `.env` local ou em "
            "**Streamlit Secrets** (Settings > Secrets) antes de importar dados.",
            icon="⚠️",
        )
        return

    sub_csv, sub_postgres = st.tabs(["📄 CSV / Excel", "🔗 Banco de Origem"])

    with sub_csv:
        _render_import_csv()

    with sub_postgres:
        _render_import_postgres()


def _render_import_csv() -> None:
    st.markdown("#### Importar via arquivo")
    st.caption(
        "Exporte dados do seu app original em CSV e importe aqui. "
        "Use **dry_run** primeiro para visualizar antes de gravar."
    )

    tipo_dados = st.selectbox(
        "Tipo de dados",
        ["transacoes", "operacoes", "proventos"],
        format_func=lambda x: {
            "transacoes": "Transacoes financeiras (receitas/despesas)",
            "operacoes":  "Operacoes de investimento (compra/venda)",
            "proventos":  "Proventos (dividendos, JCP, rendimentos)",
        }[x],
    )

    arquivo = st.file_uploader(
        "Selecione o arquivo",
        type=["csv", "xlsx", "xls"],
        help="Formatos aceitos: CSV (separado por virgula ou ponto-virgula) e Excel.",
    )

    if arquivo is None:
        _mostrar_template_csv(tipo_dados)
        return

    # Le o arquivo
    try:
        if arquivo.name.endswith(".csv"):
            # Tenta detectar separador automaticamente
            conteudo = arquivo.read()
            for sep in [",", ";"]:
                try:
                    df = pd.read_csv(io.BytesIO(conteudo), sep=sep)
                    if len(df.columns) > 1:
                        break
                except Exception:
                    continue
        else:
            df = pd.read_excel(arquivo)
    except Exception as exc:
        st.error(f"Erro ao ler arquivo: {exc}")
        return

    st.success(f"{len(df)} linhas lidas · {len(df.columns)} colunas")
    st.dataframe(df.head(5), use_container_width=True)
    st.caption(f"Colunas detectadas: {', '.join(df.columns.tolist())}")

    # Parametros de importacao
    st.markdown("---")
    usuario_id = settings.OWNER_USER_ID
    if not usuario_id:
        usuario_id = st.text_input(
            "OWNER_USER_ID",
            placeholder="UUID do usuario (configure no .env para nao precisar informar aqui)",
        )

    conta_id = ""
    if tipo_dados == "transacoes":
        conta_id = st.text_input(
            "conta_id",
            placeholder="UUID da conta de destino (obrigatorio para transacoes)",
        )

    dry_run = st.toggle("Modo simulacao (dry run)", value=True)
    if dry_run:
        st.info(
            "Dry run ativado: os dados serao validados mas NAO gravados no banco.",
            icon="ℹ️",
        )

    col_btn, _ = st.columns([1, 3])
    with col_btn:
        rotulo = "Simular importacao" if dry_run else "GRAVAR NO BANCO"
        tipo_btn = "secondary" if dry_run else "primary"
        executar = st.button(rotulo, type=tipo_btn, use_container_width=True)

    if executar:
        if not usuario_id:
            st.error("Informe o OWNER_USER_ID.")
            return
        if tipo_dados == "transacoes" and not conta_id:
            st.error("Informe o conta_id para transacoes.")
            return

        _executar_import_csv(df, tipo_dados, usuario_id, conta_id, dry_run)


def _executar_import_csv(
    df: pd.DataFrame,
    tipo: str,
    usuario_id: str,
    conta_id: str,
    dry_run: bool,
) -> None:
    from etl.importacao import ImportadorCSV

    imp = ImportadorCSV()

    with st.spinner("Processando..."):
        if tipo == "transacoes":
            res = imp.importar_transacoes(df, usuario_id, conta_id, dry_run=dry_run)
        elif tipo == "operacoes":
            res = imp.importar_operacoes(df, usuario_id, dry_run=dry_run)
        else:
            res = imp.importar_proventos(df, usuario_id, dry_run=dry_run)

    if res.ok:
        if dry_run:
            st.success(f"Simulacao OK: {res.resumo()}")
        else:
            st.success(f"Importacao concluida: {res.resumo()}")
    else:
        st.error(f"Importacao com erros: {res.resumo()}")
        with st.expander("Ver erros"):
            for e in res.erros:
                st.code(e)


def _mostrar_template_csv(tipo: str) -> None:
    """Exibe o template de colunas esperadas para cada tipo."""
    templates = {
        "transacoes": pd.DataFrame({
            "descricao":        ["Supermercado Extra", "Salario"],
            "valor":            [-250.00, 8500.00],
            "data_competencia": ["2026-05-01", "2026-05-05"],
            "tipo":             ["despesa", "receita"],
            "status":           ["liquidado", "liquidado"],
            "origem":           ["csv", "csv"],
        }),
        "operacoes": pd.DataFrame({
            "ticker":          ["IVVB11", "PETR4"],
            "tipo":            ["compra", "compra"],
            "quantidade":      [10, 100],
            "preco_unitario":  [290.50, 38.20],
            "taxas":           [0.00, 0.50],
            "data_operacao":   ["2026-01-15", "2026-02-10"],
            "corretora":       ["Clear", "XP"],
        }),
        "proventos": pd.DataFrame({
            "ticker":         ["MXRF11", "ITUB4"],
            "tipo":           ["rendimento", "dividendo"],
            "valor_por_cota": [0.10, 0.50],
            "quantidade":     [200, 150],
            "valor_total":    [20.00, 75.00],
            "data_pagamento": ["2026-05-15", "2026-05-20"],
            "data_com":       ["2026-05-10", "2026-05-12"],
        }),
    }

    df_template = templates[tipo]
    st.markdown("**Template de colunas esperadas:**")
    st.dataframe(df_template, use_container_width=True)

    csv_bytes = df_template.to_csv(index=False).encode("utf-8")
    st.download_button(
        label=f"Baixar template ({tipo}.csv)",
        data=csv_bytes,
        file_name=f"template_{tipo}.csv",
        mime="text/csv",
    )


def _render_import_postgres() -> None:
    st.markdown("#### Importar de banco PostgreSQL")
    st.caption(
        "Conecte ao banco de dados de um app original para inspecionar "
        "o schema e usar como fonte de importacao."
    )

    # Mostra se as conexoes de fonte estao pre-configuradas
    fontes_conf = {
        "App 2 — Dashboard Investimentos (SQLite local)":   settings.SOURCE_DB_APP2,
        "App 3 — Controle Financeiro (origem de migracao)": settings.url_origem_controle,
        "App 1 — Dashboard":                                settings.SOURCE_DB_APP1,
    }
    nomes_variaveis = {
        "App 2 — Dashboard Investimentos (SQLite local)":   "SOURCE_DB_APP2",
        "App 3 — Controle Financeiro (origem de migracao)": "SUPABASE_ORIGEM_CONTROLE_URL / SOURCE_DB_APP3",
        "App 1 — Dashboard":                                "SOURCE_DB_APP1",
    }

    st.info(
        "**App 2 — Investimentos** usa SQLite local "
        "(`sqlite:///caminho/para/investment_dashboard.db`). "
        "**App 3 — Controle Financeiro** usa `SUPABASE_ORIGEM_CONTROLE_URL` "
        "(origem temporaria de migracao — somente leitura).",
        icon="ℹ️",
    )

    fonte_selecionada = st.selectbox(
        "Fonte pre-configurada",
        list(fontes_conf.keys()) + ["Outra (informar manualmente)"],
    )

    url_fonte = ""
    if fonte_selecionada == "Outra (informar manualmente)":
        url_fonte = st.text_input(
            "Connection string da fonte",
            type="password",
            placeholder="postgresql://usuario:senha@host:5432/banco  ou  sqlite:///caminho/db.db",
            help="Nunca salva — usada apenas nesta sessao.",
        )
    else:
        url_conf = fontes_conf[fonte_selecionada]
        if url_conf:
            url_fonte = url_conf
            st.success(f"URL pre-configurada para {fonte_selecionada} ✓")
            if "SQLite" in fonte_selecionada:
                st.caption("Fonte SQLite detectada — conexao direta ao arquivo local.")
        else:
            var_nome = nomes_variaveis.get(fonte_selecionada, "variavel")
            st.warning(
                f"`{var_nome}` nao configurada. "
                "Informe manualmente, adicione ao `.env` local "
                "ou configure em Streamlit Secrets (Settings > Secrets).",
            )
            url_fonte = st.text_input(
                "Connection string",
                type="password",
                placeholder="postgresql://...  ou  sqlite:///caminho/db.db",
            )

    col_test, _ = st.columns([1, 3])
    with col_test:
        if st.button("Testar conexao", use_container_width=True) and url_fonte:
            _testar_conexao_fonte(url_fonte)

    if url_fonte:
        st.markdown("---")
        st.markdown("**Inspecionar schema da fonte:**")
        if st.button("Listar tabelas disponiveis", use_container_width=False):
            _listar_tabelas_fonte(url_fonte)

        st.markdown("---")
        _render_import_generica(url_fonte)


def _testar_conexao_fonte(url: str) -> None:
    from etl.importacao import ImportadorPostgres

    with st.spinner("Conectando..."):
        imp = ImportadorPostgres(url)

    if imp.conectado:
        tabelas = imp.listar_tabelas()
        st.success(f"Conexao OK — {len(tabelas)} tabela(s) encontrada(s)")
    else:
        st.error(f"Falha na conexao: {imp.erro_conexao}")


def _listar_tabelas_fonte(url: str) -> None:
    from etl.importacao import ImportadorPostgres

    imp = ImportadorPostgres(url)
    if not imp.conectado:
        st.error(imp.erro_conexao)
        return

    tabelas = imp.listar_tabelas()
    if not tabelas:
        st.info("Nenhuma tabela encontrada.")
        return

    info_tabelas = []
    for t in sorted(tabelas):
        colunas = imp.listar_colunas(t)
        info_tabelas.append({"Tabela": t, "Colunas": ", ".join(colunas[:8])})

    st.dataframe(pd.DataFrame(info_tabelas), use_container_width=True, hide_index=True)


def _render_import_generica(url_fonte: str) -> None:
    st.markdown("**Importacao generica (mapeamento manual):**")
    st.caption(
        "Informe a tabela de origem, a tabela de destino e o mapeamento de colunas. "
        "Use dry run para verificar antes de gravar."
    )

    col1, col2 = st.columns(2)
    with col1:
        tabela_fonte = st.text_input("Tabela na fonte", placeholder="ex: lancamentos")
    with col2:
        from etl.schema_setup import TABELAS_ESPERADAS
        tabela_destino = st.selectbox("Tabela no destino", TABELAS_ESPERADAS)

    st.markdown("**Mapeamento de colunas** (destino: fonte)")
    st.caption(
        "Exemplo: se a coluna chama 'data' na fonte e 'data_competencia' no destino, "
        "escreva `data_competencia=data`"
    )
    mapeamento_txt = st.text_area(
        "Mapeamento (uma linha por campo: destino=fonte)",
        height=120,
        placeholder="descricao=descricao\nvalor=valor\ndata_competencia=data\ntipo=tipo",
    )

    filtro = st.text_input("Filtro SQL (opcional)", placeholder="WHERE ativo = true")
    dry_run = st.toggle("Modo simulacao (dry run)", value=True, key="dry_run_postgres")

    if st.button("Executar importacao", type="primary" if not dry_run else "secondary"):
        if not tabela_fonte or not mapeamento_txt:
            st.error("Informe a tabela de origem e o mapeamento.")
            return

        mapeamento = {}
        for linha in mapeamento_txt.strip().splitlines():
            if "=" in linha:
                dest, fonte = linha.split("=", 1)
                mapeamento[dest.strip()] = fonte.strip()

        if not mapeamento:
            st.error("Mapeamento invalido. Use o formato: destino=fonte")
            return

        _executar_import_postgres(url_fonte, tabela_fonte, tabela_destino, mapeamento, filtro, dry_run)


def _executar_import_postgres(
    url_fonte: str,
    tabela_fonte: str,
    tabela_destino: str,
    mapeamento: dict[str, str],
    filtro: str,
    dry_run: bool,
) -> None:
    from etl.importacao import ImportadorPostgres

    with st.spinner("Importando..."):
        imp = ImportadorPostgres(url_fonte)
        if not imp.conectado:
            st.error(f"Falha na conexao: {imp.erro_conexao}")
            return

        res = imp.importar_tabela_generica(
            tabela_fonte=tabela_fonte,
            tabela_destino=tabela_destino,
            mapeamento=mapeamento,
            filtro_sql=filtro,
            dry_run=dry_run,
        )

    if res.ok:
        st.success(res.resumo())
    else:
        st.error(res.resumo())
        with st.expander("Ver erros"):
            for e in res.erros:
                st.code(e)


# ── Tab 3: Cotacoes ───────────────────────────────────────────────────────────

def _render_cotacoes() -> None:
    secao_titulo("Atualizacao de Cotacoes", "📊", "Importa historico via yfinance")

    if not settings.has_database:
        st.warning(
            "Configure `SUPABASE_UNIFICADO_URL` antes de atualizar cotacoes.",
            icon="⚠️",
        )
        return

    from core.database import get_engine
    from sqlalchemy import text

    engine = get_engine()
    if engine is None:
        st.error("Banco nao conectado.")
        return

    # ── Status ────────────────────────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            total_ativos = conn.execute(
                text("SELECT COUNT(*) FROM assets")
            ).scalar() or 0
            com_cotacao = conn.execute(
                text("SELECT COUNT(DISTINCT asset_id) FROM asset_quotes")
            ).scalar() or 0
            sem_cotacao = total_ativos - com_cotacao
            ultima_cot  = conn.execute(
                text("SELECT MAX(timestamp) FROM asset_quotes")
            ).scalar()

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Total de Ativos", total_ativos)
        with c2:
            st.metric("Com Cotacoes", com_cotacao)
        with c3:
            st.metric(
                "Sem Cotacoes",
                sem_cotacao,
                delta=f"-{sem_cotacao}" if sem_cotacao > 0 else None,
                delta_color="inverse",
            )
        with c4:
            st.metric(
                "Ultima Atualizacao",
                ultima_cot.strftime("%d/%m/%Y") if ultima_cot else "Nunca",
            )

    except Exception as e:
        st.error(f"Erro ao verificar cotacoes: {e}")
        return

    if sem_cotacao > 0:
        st.warning(
            f"{sem_cotacao} ativo(s) sem cotacoes. "
            "Clique em **Atualizar** para baixar via yfinance.",
            icon="⚠️",
        )
    else:
        st.success("Todos os ativos tem cotacoes atualizadas! ✓")

    st.info(
        "Ativos B3 recebem o sufixo `.SA` automaticamente (ex: `PETR3F` → `PETR3F.SA`). "
        "Ativos USD usam o ticker original. Em caso de erro, verifique o ticker "
        "na tabela `assets`.",
        icon="ℹ️",
    )

    st.divider()

    # ── Parametros ────────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        periodo = st.selectbox(
            "Periodo historico",
            ["1mo", "3mo", "6mo", "1y", "2y", "5y"],
            index=3,
            format_func=lambda x: {
                "1mo": "1 mes",
                "3mo": "3 meses",
                "6mo": "6 meses",
                "1y":  "1 ano",
                "2y":  "2 anos",
                "5y":  "5 anos",
            }[x],
        )
    with col2:
        apenas_sem = st.checkbox(
            "Apenas ativos sem cotacao",
            value=True,
            help="Desmarcado: atualiza todos os ativos (mais demorado).",
        )

    col_btn, _ = st.columns([2, 3])
    with col_btn:
        if st.button(
            f"📊 Atualizar {'ativos sem cotacao' if apenas_sem else 'todos os ativos'}",
            type="primary",
            use_container_width=True,
            disabled=total_ativos == 0,
        ):
            _executar_update_cotacoes(engine, periodo, apenas_sem)


def _executar_update_cotacoes(engine, periodo: str, apenas_sem: bool) -> None:
    """Baixa cotacoes via yfinance e insere em asset_quotes."""
    try:
        import yfinance as yf
        from sqlalchemy import text
    except ImportError:
        st.error(
            "yfinance nao instalado. Adicione `yfinance` ao `requirements.txt` "
            "e reinicie o app."
        )
        return

    try:
        with engine.connect() as conn:
            if apenas_sem:
                rows = conn.execute(text("""
                    SELECT id::text AS id, ticker, currency
                    FROM   assets
                    WHERE  NOT EXISTS (
                        SELECT 1 FROM asset_quotes aq WHERE aq.asset_id = assets.id
                    )
                    ORDER BY ticker
                """)).fetchall()
            else:
                rows = conn.execute(
                    text("SELECT id::text AS id, ticker, currency FROM assets ORDER BY ticker")
                ).fetchall()

        if not rows:
            st.success("Nenhum ativo para atualizar.")
            return

        total = len(rows)
        st.markdown(f"**Atualizando {total} ativo(s)...**")
        prog = st.progress(0, text="Iniciando...")
        log  = st.empty()

        sucesso   = 0
        sem_dados = 0
        erros     = 0
        total_pts = 0
        log_lines: list[str] = []

        _SQL_UPSERT = text("""
            INSERT INTO asset_quotes
                (asset_id, timestamp, open, high, low, close, volume)
            VALUES
                (:asset_id, :ts, :open, :high, :low, :close, :volume)
            ON CONFLICT (asset_id, timestamp) DO UPDATE
                SET close  = EXCLUDED.close,
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    volume = EXCLUDED.volume
        """)

        for i, r in enumerate(rows):
            ticker_yf = f"{r.ticker}.SA" if (r.currency or "BRL") == "BRL" else r.ticker
            prog.progress((i + 1) / total, text=f"{ticker_yf} ({i+1}/{total})")

            try:
                hist = yf.download(
                    ticker_yf,
                    period=periodo,
                    progress=False,
                    auto_adjust=True,
                    actions=False,
                )

                if hist.empty:
                    sem_dados += 1
                    log_lines.append(f"⚠️ {ticker_yf}: sem dados")
                    continue

                records = []
                for ts, row_data in hist.iterrows():
                    close_val = float(row_data.get("Close", 0) or 0)
                    if close_val > 0:
                        records.append({
                            "asset_id": r.id,
                            "ts":       ts.to_pydatetime(),
                            "open":     float(row_data.get("Open",   0) or 0) or None,
                            "high":     float(row_data.get("High",   0) or 0) or None,
                            "low":      float(row_data.get("Low",    0) or 0) or None,
                            "close":    close_val,
                            "volume":   float(row_data.get("Volume", 0) or 0) or None,
                        })

                if records:
                    with engine.begin() as conn:
                        conn.execute(_SQL_UPSERT, records)
                    total_pts += len(records)
                    sucesso   += 1
                    log_lines.append(f"✅ {ticker_yf}: {len(records)} pts")
                else:
                    sem_dados += 1
                    log_lines.append(f"⚠️ {ticker_yf}: nenhum dado valido")

            except Exception as e:
                erros += 1
                log_lines.append(f"❌ {ticker_yf}: {str(e)[:60]}")

            # Exibe as ultimas 5 linhas de log
            log.code("\n".join(log_lines[-5:]))

        prog.empty()
        log.empty()

        # Resumo final
        if sucesso:
            st.success(
                f"✅ {sucesso} ativo(s) atualizados · {total_pts:,} pontos inseridos"
                .replace(",", ".")
            )
        if sem_dados:
            st.warning(f"⚠️ {sem_dados} ativo(s) sem dados no yfinance")
        if erros:
            st.error(f"❌ {erros} erro(s) — verifique o log acima")

        if log_lines:
            with st.expander("Ver log completo"):
                st.code("\n".join(log_lines))

        # Invalida caches de investimentos
        if sucesso > 0:
            from core.investimentos import get_carteira, get_cashflow_mensal
            get_carteira.clear()
            get_cashflow_mensal.clear()
            st.rerun()

    except Exception as e:
        st.error(f"Erro inesperado: {e}")


# ── Tab 4: Seguranca ──────────────────────────────────────────────────────────

def _render_seguranca() -> None:
    secao_titulo("Seguranca", "🔒")

    c1, c2, c3 = st.columns(3)

    with c1:
        if settings.APP_PASSWORD:
            st.success("Senha de acesso configurada")
        else:
            st.warning("Sem senha (dev local)")

    with c2:
        if esta_autenticado():
            st.success("Sessao autenticada")
        else:
            st.info("Sem autenticacao ativa")

    with c3:
        if settings.has_owner:
            st.success("OWNER_USER_ID configurado")
        else:
            st.warning("OWNER_USER_ID ausente")

    st.divider()

    st.markdown("**Checklist de seguranca:**")

    checks = [
        (True,                         ".env no .gitignore — credenciais fora do repositorio"),
        (True,                         "secrets.toml no .gitignore"),
        (not bool(os.getenv("SUPABASE_UNIFICADO_SERVICE_ROLE_KEY", "")),
                                       "service_role_key ausente do ambiente de execucao"),
        (True,                         "get_db_status() nao expoe URL nem credenciais"),
        (bool(settings.APP_PASSWORD),  "Senha de acesso configurada (APP_PASSWORD)"),
        (settings.has_owner,           "Filtro de usuario ativo (OWNER_USER_ID)"),
        (not settings.MOCK_MODE,       "MOCK_MODE=false (dados reais em uso)"),
    ]

    for ok, descricao in checks:
        icone = "✅" if ok else "⚠️"
        st.markdown(f"{icone} {descricao}")

    st.divider()

    if settings.APP_PASSWORD and esta_autenticado():
        st.markdown("**Sessao atual:**")
        if st.button("Encerrar sessao", type="secondary"):
            encerrar_sessao()

    st.markdown("**Gerar hash SHA-256 para APP_PASSWORD:**")
    senha_teste = st.text_input(
        "Senha para gerar hash",
        type="password",
        key="_hash_gen",
        placeholder="Digite para ver o hash",
    )
    if senha_teste:
        import hashlib
        h = hashlib.sha256(senha_teste.encode()).hexdigest()
        st.code(h)
        st.caption("Cole este hash no campo APP_PASSWORD do .env ou secrets.toml")


# ── Tab 5: Setup ──────────────────────────────────────────────────────────────

def _render_setup() -> None:
    secao_titulo("Configuracao Inicial", "📋")

    st.info(
        "**Estrategia de banco:** usar o projeto Supabase **existente** do "
        "Dashboard Financeiro como banco unificado. "
        "Nao criar novo projeto — plano gratuito permite apenas 2 projetos. "
        "Guia completo: `docs/banco_unificado_fases.md`",
        icon="ℹ️",
    )

    st.markdown("""
### Roteiro de ativacao — Banco Unificado (Fases 4.1 a 4.9)

---

#### Fase 4.1 — Auditar os bancos existentes (proxima etapa)

No SQL Editor do Supabase, executar em **ambos** os projetos:

```sql
-- Listar tabelas e colunas existentes
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
```

Salvar os resultados em `supabase_unificado/validation/`.

---

#### Fases 4.2 a 4.4 — Gerar e revisar scripts SQL

O Claude gerara os scripts DDL em `supabase_unificado/schema/`
apos a auditoria. **Revisar cada script antes de executar.**

---

#### Fase 4.5 — Aplicar scripts no Supabase (execucao manual)

Executar os scripts aprovados no SQL Editor do projeto **Dashboard Financeiro**.
Ordem: `001_usuarios.sql` ate `012_rls_policies.sql`.

**Pre-requisito obrigatorio:** backup do projeto Dashboard Financeiro verificado.

---

#### Role de leitura segura (executar na Fase 4.5)

```sql
-- Executar no SQL Editor do projeto Dashboard Financeiro
-- Substituir 'senha_segura' por uma senha real antes de executar
CREATE ROLE app4_reader WITH LOGIN PASSWORD 'senha_segura';
GRANT CONNECT ON DATABASE postgres TO app4_reader;
GRANT USAGE ON SCHEMA public TO app4_reader;
GRANT SELECT ON
    usuarios, contas, categorias, transacoes, orcamentos, metas,
    ativos, operacoes, proventos, cotacoes
TO app4_reader;
-- NAO conceder: INSERT, UPDATE, DELETE, BYPASSRLS
```

---

#### Obter OWNER_USER_ID (apos criar usuario)

```sql
-- Executar no SQL Editor do Dashboard Financeiro apos criar o usuario
SELECT id FROM usuarios LIMIT 1;
-- Copiar o UUID retornado para OWNER_USER_ID no .env
```

---

#### Fase 4.9 — Configurar variaveis e ativar banco

Apos schema criado e dados migrados (Fases 4.5 a 4.8):

**Opcao A — Dev local (arquivo `.env`):**

```bash
cp .env.example .env
```

Preencher no `.env`:

```
SUPABASE_UNIFICADO_URL="postgresql://app4_reader:SENHA@HOST.pooler.supabase.com:6543/postgres"
OWNER_USER_ID="uuid-do-usuario-na-tabela-usuarios"
MOCK_MODE="false"
APP_PASSWORD="hash-sha256-ou-texto-simples"
```

**Opcao B — Streamlit Cloud (Settings > Secrets):**

```toml
SUPABASE_UNIFICADO_URL = "postgresql://app4_reader:SENHA@HOST.pooler.supabase.com:6543/postgres"
OWNER_USER_ID = "uuid-do-usuario-na-tabela-usuarios"
MOCK_MODE = "false"
APP_PASSWORD = "hash-sha256-ou-texto-simples"
```

Depois: aba "Banco de Dados" desta pagina para verificar e criar tabelas.
    """)

    st.divider()
    st.caption(
        "Dashboard Financeiro Unificado — v0.4.0 · Fase 4 "
        "| Ver docs/banco_unificado_fases.md para o roteiro completo"
    )
