# AGENTS.md

## Objetivo do Projeto

Aplicação Streamlit unificada para:
- controle financeiro;
- investimentos;
- carteira;
- proventos;
- análise de empresas;
- indicadores econômicos;
- inteligência artificial aplicada.

## Repositórios Originais

- Tiago84Barros/Dashboard
- Tiago84Barros/Controle_Financeiro
- Tiago84Barros/Dashboard-Investimentos

## Regras

- Não apagar funcionalidades existentes sem validação.
- Migrar funcionalidades por módulos.
- Não duplicar conexão de banco.
- Priorizar organização modular.
- Manter padrão Streamlit.
- Separar interface, lógica de negócio e ETL.

## Estrutura desejada

- app.py
- pages/
- core/
- etl/
- design/
- docs/

## Execução

```bash
pip install -r requirements.txt
streamlit run app.py
```
