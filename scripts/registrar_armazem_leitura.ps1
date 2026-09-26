<#
.SYNOPSIS
    Registra a tarefa que sobe o servico so-leitura do armazem no logon.

.DESCRIPTION
    Rode uma vez, num PowerShell comum (nao precisa de administrador):

        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\registrar_armazem_leitura.ps1

    A tarefa roda `scripts\iniciar_armazem_leitura.py` a partir da PASTA DO
    SERVICO, um worktree destacado em origin/main (padrao:
    ..\dfu-armazem-servico). Nao roda da arvore de trabalho: la a branch muda
    conforme o trabalho do dia, e uma branch antiga sem a rota nova derrubaria o
    app para a vitrine em silencio. O supervisor leva a pasta para origin/main a
    cada logon.

    O `.env` continua um so, o da arvore principal; o caminho vai por --env.

    GATILHO DE LOGON, e nao de boot: o Docker Desktop e o `.env` sao do usuario,
    e o Docker so sobe quando ele entra. O servidor nao espera o Docker -- ate o
    armazem aparecer, responde 503 e o app avisa que caiu na vitrine.

    pythonw.exe, e nao python.exe: sem janela de console no logon. A saida vai
    para %LOCALAPPDATA%\DFU\armazem_leitura.log.

    O `cloudflared` NAO e registrado aqui. Ele vira servico do Windows pelo
    comando `cloudflared service install <token>` que o painel da Cloudflare
    mostra, rodado como administrador pelo dono da conta.
#>
param(
    [string]$Pasta = "",
    [string]$ArquivoEnv = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $Pasta) { $Pasta = Join-Path (Split-Path -Parent $repo) "dfu-armazem-servico" }
# Padrao: o .env da arvore PRINCIPAL (dona do .git comum), qualquer que seja o
# worktree de onde este script foi chamado.
if (-not $ArquivoEnv) {
    $gitComum = git -C $repo rev-parse --path-format=absolute --git-common-dir
    $ArquivoEnv = Join-Path (Split-Path -Parent $gitComum) ".env"
}

if (-not (Test-Path (Join-Path $Pasta "scripts\iniciar_armazem_leitura.py"))) {
    throw ("Pasta do servico nao encontrada: $Pasta`n" +
           "Crie com: git worktree add --detach `"$Pasta`" origin/main")
}
if (-not (Test-Path $ArquivoEnv)) { throw "Arquivo .env nao encontrado: $ArquivoEnv" }

$pythonw = "C:\Users\Tiago Barros\AppData\Local\Programs\Python\Python312\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $encontrado = Get-Command pythonw -ErrorAction SilentlyContinue
    if ($null -eq $encontrado) { throw "pythonw.exe nao encontrado." }
    $pythonw = $encontrado.Source
}

$nome = "DFU - Armazem leitura"
$acao = New-ScheduledTaskAction -Execute $pythonw `
    -Argument ("`"$Pasta\scripts\iniciar_armazem_leitura.py`" --atualizar " +
               "--env `"$ArquivoEnv`"") `
    -WorkingDirectory $Pasta
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Folga curta para a rede subir: o git fetch do --atualizar precisa dela, e
# falhar so faz o servico subir com o codigo que ja esta no disco.
$logon.Delay = "PT1M"
# ExecutionTimeLimit zero = sem limite. O padrao de 72 h mataria o servico no
# terceiro dia de PC ligado.
$config = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $nome -Confirm:$false
    Write-Host "Substituindo tarefa existente: $nome"
}
Register-ScheduledTask -TaskName $nome `
    -Description ("Sobe o servico so-leitura do armazem local (127.0.0.1:8787) " +
                  "que o tunel da Cloudflare expoe para a producao. Reinicia " +
                  "sozinho se cair; log em %LOCALAPPDATA%\DFU\armazem_leitura.log.") `
    -Action $acao -Trigger $logon -Settings $config -Principal $principal | Out-Null
Write-Host "Registrada: $nome"
Write-Host "Para subir agora sem esperar o proximo logon:"
Write-Host "    Start-ScheduledTask -TaskName `"$nome`""
