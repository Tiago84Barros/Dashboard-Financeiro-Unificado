@echo off
REM Abre o Dashboard Financeiro Unificado na propria maquina lendo o armazem
REM local (Docker dfu_warehouse, porta 5433) em vez do Supabase. E o modo mais
REM rapido e nao gasta egress do Supabase, mas e so para CONSULTA: o armazem e
REM uma copia de mao unica do Supabase (ate 1 dia atrasada). Lancamento feito
REM aqui nao chega a nuvem e trava o espelho noturno.
REM
REM Sobe o Docker e o armazem se precisar. Se o app ja estiver no ar em 8502,
REM so abre o navegador. Fechar esta janela encerra o app.

if "%~1"==":esperar" goto esperar

title Dashboard Financeiro (local - armazem, so consulta)
cd /d "%~dp0.."

curl -s -o nul -m 2 http://localhost:8502/_stcore/health
if not errorlevel 1 (
    echo O app ja esta rodando. Abrindo o navegador...
    start "" http://localhost:8502
    exit /b 0
)

py -3.12 -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Python 3.12 com Streamlit nao encontrado.
    echo Instale as dependencias com:  py -3.12 -m pip install -r requirements.txt
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo Iniciando o Docker Desktop...
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    for /l %%i in (1,1,90) do (
        docker info >nul 2>&1 && goto docker_ok
        ping -n 3 127.0.0.1 >nul
    )
    echo O Docker nao respondeu em 3 minutos.
    pause
    exit /b 1
)
:docker_ok

docker start dfu_warehouse >nul 2>&1
for /l %%i in (1,1,60) do (
    docker inspect -f "{{.State.Health.Status}}" dfu_warehouse 2>nul | findstr /b healthy >nul && goto armazem_ok
    ping -n 3 127.0.0.1 >nul
)
echo O armazem local (dfu_warehouse) nao ficou pronto.
pause
exit /b 1
:armazem_ok

echo ==========================================================================
echo  MODO CONSULTA - dados do armazem local, copia do Supabase de ate 1 dia.
echo  Nao lance nem edite nada aqui: nao chega a nuvem e trava o espelho.
echo  Para lancar, use o atalho "Dashboard Financeiro (local)".
echo ==========================================================================
echo Iniciando o app em http://localhost:8502 ...
echo Para encerrar, feche esta janela.
echo.
start "" /b cmd /c ""%~f0" :esperar"
py -3.12 scripts\dev_preview_server.py --port 8502
pause
exit /b 0

REM Roda em segundo plano: abre o navegador quando o servidor responder.
:esperar
for /l %%i in (1,1,120) do (
    curl -s -o nul -m 2 http://localhost:8502/_stcore/health && (
        start "" http://localhost:8502
        exit /b 0
    )
    ping -n 2 127.0.0.1 >nul
)
exit /b 1
