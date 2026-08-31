<#
.SYNOPSIS
    Republica a vitrine de FIIs no Supabase a partir do armazem local.

.DESCRIPTION
    Existe porque a vitrine tem prazo de validade e a publicacao era manual.
    Em 31/08/2026 ela chegou a 5 dias, a leitura falhou e a tela creditou a
    falha aos filtros de elegibilidade -- os 394 fundos apareceram como
    inelegiveis por metrica ausente (PR #190). O codigo agora falha de forma
    visivel, mas o remedio de verdade e nao deixar a vitrine envelhecer.

    Roda no computador do usuario porque o armazem local (Docker, porta 5433)
    nao e alcancavel pelo GitHub Actions -- a publicacao NAO pode ser um
    workflow remoto.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

$logDir = Join-Path $repo "local_staging\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }
$log = Join-Path $logDir "republicacao_fii.log"

function Write-Log([string]$mensagem) {
    $linha = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $mensagem
    Add-Content -LiteralPath $log -Value $linha -Encoding utf8
}

# O `python` do PATH cai na venv do Hermes, que nao tem as dependencias deste
# projeto. Resolver o interpretador explicitamente, com fallback.
$python = "C:\Users\Tiago Barros\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $python)) {
    $encontrado = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $encontrado) { Write-Log "ERRO: nenhum interpretador Python encontrado."; exit 1 }
    $python = $encontrado.Source
}

Write-Log "--- inicio ---"

# Armazem parado trava a publicacao; subir e autorizacao permanente do usuario.
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
    Write-Log "ERRO: armazem local nao ficou saudavel em 10 minutos (status=$health)."
    exit 1
}

$env:PYTHONPATH = $repo
$env:PYTHONIOENCODING = "utf-8"

# O publicador escreve ruido do Streamlit no stderr. No PowerShell 5.1, `2>&1`
# sobre executavel nativo embrulha cada linha de stderr num ErrorRecord e, com
# ErrorActionPreference=Stop, derruba o script mesmo com exit 0. Manda o stderr
# para arquivo e afrouxa a preferencia so em volta das chamadas nativas.
$stderrFile = Join-Path $logDir "republicacao_fii.stderr.log"
$ErrorActionPreference = "Continue"
$saida = & $python "scripts/publish_fii_selection_from_local.py" 2>$stderrFile
$codigo = $LASTEXITCODE
$ErrorActionPreference = "Stop"

# A ultima linha do publicador e o resumo JSON da publicacao.
$resumo = ($saida | Where-Object { $_ -match '^\{.*"published_rows"' } | Select-Object -Last 1)
if ($codigo -ne 0) {
    Write-Log "ERRO: publicacao falhou (exit=$codigo)."
    Write-Log ($saida | Select-Object -Last 20 | Out-String).Trim()
    exit $codigo
}

if ($resumo) {
    try {
        $dados = $resumo | ConvertFrom-Json
        Write-Log ("OK: {0} linhas publicadas, validacao={1}, pronta={2}." -f `
            $dados.published_rows, $dados.validation_status, $dados.publication_ready)
        if (-not $dados.publication_ready) {
            Write-Log "ATENCAO: publicacao marcada como NAO pronta; conferir bloqueios."
        }
    } catch {
        Write-Log "OK: publicacao concluida (resumo JSON ilegivel)."
    }
} else {
    Write-Log "OK: publicacao concluida (sem resumo JSON na saida)."
}

# Confere pelo mesmo caminho que a tela usa: republicar sem verificar o frescor
# seria repetir o defeito que originou este script.
$ErrorActionPreference = "Continue"
$verificacao = & $python "scripts/verificar_frescor_vitrine_fii.py" 2>$stderrFile
$codigoVerificacao = $LASTEXITCODE
$ErrorActionPreference = "Stop"
Write-Log ("verificacao: " + (($verificacao | Where-Object { $_ -match "^linhas=" } | Select-Object -Last 1)))
if ($codigoVerificacao -ne 0) {
    Write-Log "ERRO: a vitrine publicada nao passou na verificacao de leitura."
    Write-Log ($verificacao | Select-Object -Last 5 | Out-String).Trim()
    exit $codigoVerificacao
}
Write-Log "--- fim ---"
