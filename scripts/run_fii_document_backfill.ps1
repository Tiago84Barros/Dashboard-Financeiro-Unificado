<#
.SYNOPSIS
    Backfill dos documentos de FII no armazem local.

.DESCRIPTION
    Tres defeitos corrigidos em 01/09/2026, todos silenciosos:

    1. Chamava `python` cru. Nesta maquina o `python` do PATH resolve para a
       venv do Hermes, que nao tem sqlalchemy -- a tarefa agendada saia com
       codigo 1 a cada logon desde entao. O erro e `ModuleNotFoundError`, o que
       faz parecer problema de dependencia do projeto quando e escolha de
       interpretador.

    2. Esperava o container ficar saudavel mas nunca o subia. No logon, que e
       exatamente quando esta tarefa dispara, o container esta parado: o script
       esperava dez minutos e lancava. A espera sem o start so podia dar errado.

    3. Nao registrava nada. Uma tarefa que falha todo dia sem deixar rastro
       parece uma tarefa que funciona.

    A republicacao da vitrine saiu daqui: quem publica agora e
    `scripts\atualizar_vitrines.py`, que decide por cadencia, verifica pelo
    leitor da tela e avisa no Telegram quando falha.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

$logDir = Join-Path $repo "local_staging\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }
$log = Join-Path $logDir "fii_document_backfill.log"

function Write-Log([string]$mensagem) {
    $linha = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $mensagem
    Add-Content -LiteralPath $log -Value $linha -Encoding utf8
}

$python = "C:\Users\Tiago Barros\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $python)) {
    $encontrado = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $encontrado) { Write-Log "ERRO: nenhum interpretador Python encontrado."; exit 1 }
    $python = $encontrado.Source
}

Write-Log "--- inicio ---"

$estado = docker inspect -f "{{.State.Running}}" dfu_warehouse 2>$null
if ($estado -ne "true") {
    Write-Log "Armazem parado; subindo o container dfu_warehouse."
    docker start dfu_warehouse | Out-Null
}

$deadline = (Get-Date).AddMinutes(10)
do {
    $health = docker inspect -f "{{.State.Health.Status}}" dfu_warehouse 2>$null
    if ($health -eq "healthy") { break }
    Start-Sleep -Seconds 10
} while ((Get-Date) -lt $deadline)

if ($health -ne "healthy") {
    Write-Log "ERRO: armazem nao ficou saudavel em 10 minutos (status=$health)."
    & $python "scripts\notificar.py" --assunto "Dashboard: backfill de documentos FII" `
        "O armazem local nao subiu; o backfill nao rodou."
    exit 1
}

$env:PYTHONPATH = $repo
$env:PYTHONIOENCODING = "utf-8"

# PowerShell 5.1 embrulha cada linha de stderr de executavel nativo num
# ErrorRecord; com ErrorActionPreference=Stop isso derruba o script mesmo com
# exit 0. Manda o stderr para arquivo e afrouxa a preferencia so aqui.
$stderrFile = Join-Path $logDir "fii_document_backfill.stderr.log"
$ErrorActionPreference = "Continue"
& $python "scripts\backfill_fii_documents_local.py" `
    --workers 10 `
    --min-free-gb 5 `
    --max-batch-mb 150 `
    --max-document-mb 30 `
    --download-timeout 20 `
    --download-attempts 2 `
    --max-processing-attempts 3 `
    --checkpoint-every 500 `
    --publish-every-checkpoint `
    --sleep-seconds 0.2 2>$stderrFile
$codigo = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($codigo -ne 0) {
    Write-Log "ERRO: backfill saiu com $codigo."
    $cauda = (Get-Content -LiteralPath $stderrFile -Tail 15 -ErrorAction SilentlyContinue) -join "`n"
    Write-Log $cauda
    & $python "scripts\notificar.py" --assunto "Dashboard: backfill de documentos FII" `
        "O backfill saiu com codigo $codigo.`n`n$cauda"
    exit $codigo
}

Write-Log "OK: backfill concluido."
Write-Log "--- fim ---"
