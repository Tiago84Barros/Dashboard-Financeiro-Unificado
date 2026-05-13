"""
pages/configuracoes.py
Módulo de configurações do app: status do banco, variáveis de ambiente e preferências.
Status: funcional parcial — exibe status de conexão (Fase 1)
"""
import streamlit as st


def render():
    st.title("Configurações")

    # ── Status do banco ───────────────────────────────────────────────
    st.subheader("Status da Conexão")

    from core.database import get_db_status
    status = get_db_status()

    col1, col2, col3 = st.columns(3)

    with col1:
        if status["configurado"]:
            st.success("DATABASE_URL configurada")
        else:
            st.error("DATABASE_URL ausente")

    with col2:
        if status["conectado"]:
            st.success("Banco conectado")
        else:
            st.warning("Banco não conectado")

    with col3:
        if status["mock_mode"]:
            st.warning("Modo mock ativo")
        else:
            st.success("Dados reais")

    st.divider()

    # ── Instruções ────────────────────────────────────────────────────
    st.subheader("Configurar Banco de Dados")
    st.markdown(
        """
        1. Copie o arquivo `.env.example` para `.env` na raiz do projeto.
        2. Preencha `DATABASE_URL` com a string de conexão PostgreSQL.
        3. Reinicie o app: `streamlit run app.py`

        ```bash
        cp .env.example .env
        # edite .env com suas credenciais
        streamlit run app.py
        ```
        """
    )

    st.divider()
    st.caption("Dashboard Financeiro Unificado — v0.1.0")
