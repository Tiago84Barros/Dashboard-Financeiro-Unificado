# Arquitetura do Projeto

## Objetivo

Centralizar funcionalidades financeiras, patrimoniais e analíticas em uma única plataforma Streamlit.

## Estrutura Base

- app.py → ponto de entrada principal
- pages/ → páginas Streamlit
- core/ → lógica de negócio
- etl/ → importação e tratamento de dados
- design/ → identidade visual e layout
- docs/ → documentação técnica

## Estratégia de Migração

1. Migrar Dashboard como núcleo principal.
2. Integrar Controle_Financeiro como módulo.
3. Integrar Dashboard-Investimentos como módulo.
4. Refatorar banco de dados e dependências.
