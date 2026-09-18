"""Preview sintético de aparência; sem login, banco ou serviços externos."""
import pandas as pd
import plotly.express as px
import streamlit as st

from design.tema import aplicar_tema

st.set_page_config(layout="wide", page_title="App4 · temas sintéticos")
with st.sidebar:
    st.title("Prévia sintética")
    theme = st.selectbox("Tema", ("dark", "light"))
    st.radio("Navegação", ["Controle", "Investimentos", "Conversas"])
aplicar_tema(theme)
st.title("Aparência da minha conta")
st.caption("Somente dados sintéticos — nenhuma informação pessoal.")
st.markdown('<div class="app-page-hero"><div class="app-page-title-row"><h1>Resumo de exemplo</h1></div></div>', unsafe_allow_html=True)
st.metric("Saldo sintético", "R$ 100", "+10%")
st.text_input("Nome de exemplo", "Pessoa sintética")
st.selectbox("Conta de exemplo", ["Conta A", "Conta B"])
st.button("Ação de exemplo", type="primary")
with st.expander("Detalhes", expanded=True):
    st.write("Texto de exemplo para leitura e contraste.")
st.info("Mensagem informativa sintética")
with st.chat_message("assistant"):
    st.write("Esta é uma conversa fictícia.")
data = pd.DataFrame({"Mês": ["Jan", "Fev"], "Valor": [100, 120]})
st.dataframe(data)
st.plotly_chart(px.bar(data, x="Mês", y="Valor"))
