# Arquitetura do Projeto

## Objetivo

Centralizar funcionalidades financeiras, patrimoniais e analíticas em uma única plataforma Streamlit.

## Estrutura Base

- app.py → ponto de entrada e roteamento (`_ROTAS`)
- views/ → telas Streamlit
- core/ → lógica de negócio e acesso a dados
- data_pipeline/ e etl/ → coleta, importação e tratamento de dados
- design/ → identidade visual e componentes
- supabase_unificado/schema/ → migrations SQL do banco
- docs/ → documentação técnica

## Bancos

- Supabase (nuvem) → banco único que o app publicado lê.
- Armazém local (Docker `dfu_warehouse`) → dados pesados; gera e publica as vitrines.

Detalhes e regras em `CLAUDE.md`.
