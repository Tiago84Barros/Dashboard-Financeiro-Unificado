@echo off
REM Abre o Dashboard Financeiro Unificado na propria maquina, sem a Streamlit
REM Cloud. Usa o mesmo banco da nuvem (Supabase, via .env): o que for lancado
REM aqui aparece no app publicado, e vice-versa.
REM
REM Se o app ja estiver no ar, so abre o navegador; senao sobe o servidor e o
REM Streamlit abre o navegador sozinho quando estiver pronto. Fechar esta
REM janela encerra o app.

title Dashboard Financeiro (local)
cd /d "%~dp0.."

curl -s -o nul -m 2 http://localhost:8501/_stcore/health
if not errorlevel 1 (
    echo O app ja esta rodando. Abrindo o navegador...
    start "" http://localhost:8501
    exit /b 0
)

py -3.12 -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Python 3.12 com Streamlit nao encontrado.
    echo Instale as dependencias com:  py -3.12 -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Iniciando o app em http://localhost:8501 ...
echo Para encerrar, feche esta janela.
echo.
py -3.12 -m streamlit run app.py --server.port=8501 --server.headless=false
pause
