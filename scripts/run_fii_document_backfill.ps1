$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

$deadline = (Get-Date).AddMinutes(10)
do {
    $health = docker inspect -f "{{.State.Health.Status}}" dfu_warehouse 2>$null
    if ($health -eq "healthy") { break }
    Start-Sleep -Seconds 10
} while ((Get-Date) -lt $deadline)

if ($health -ne "healthy") {
    throw "Warehouse local nao ficou saudavel dentro de 10 minutos."
}

python scripts\backfill_fii_documents_local.py `
    --workers 10 `
    --min-free-gb 5 `
    --max-batch-mb 150 `
    --max-document-mb 30 `
    --download-timeout 20 `
    --download-attempts 2 `
    --max-processing-attempts 3 `
    --checkpoint-every 500 `
    --publish-every-checkpoint `
    --sleep-seconds 0.2

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python scripts\publish_fii_selection_from_local.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
