import streamlit as st

st.set_page_config(page_title='Dashboard Financeiro Unificado', layout='wide')

st.title('Dashboard Financeiro Unificado')
st.markdown('Plataforma central para controle financeiro, investimentos e análise de dados financeiros.')

menu = st.sidebar.radio(
    'Navegação',
    [
        'Dashboard Geral',
        'Controle Financeiro',
        'Investimentos',
        'Carteira',
        'Proventos',
        'Empresas B3',
        'Empresas EUA',
        'Cenário Macroeconômico',
        'Configurações'
    ]
)

st.subheader(menu)
st.info('Estrutura inicial criada. Funcionalidades serão migradas gradualmente.')
