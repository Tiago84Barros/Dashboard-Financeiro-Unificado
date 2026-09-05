<#
.SYNOPSIS
    Registra (ou atualiza) as tarefas agendadas de atualizacao das vitrines.

.DESCRIPTION
    Rode uma vez, num PowerShell qualquer:

        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\registrar_tarefas.ps1

    Nao pede privilegio de administrador: as tarefas ficam no contexto do
    usuario, que e o que precisa ser -- o Docker Desktop e o `.env` do projeto
    sao do usuario, e uma tarefa em SYSTEM nao alcanca nenhum dos dois.

    DOIS GATILHOS, e os dois importam:

      * diario as 19:30, depois do fechamento da B3;
      * ao entrar na sessao (logon), com 3 minutos de folga para o Docker
        Desktop subir.

    O gatilho de logon e o que cobre a maquina desligada. Nao e redundancia com
    o diario: e o unico caminho pelo qual um dia perdido e recuperado. Ele so
    funciona porque a cadencia de `core.publicacao_agenda` e medida contra a
    ultima publicacao BEM-SUCEDIDA e nao contra um horario -- ligar o computador
    depois de uma semana fora publica o que venceu, na ordem, e ligar duas vezes
    no mesmo dia nao publica nada de novo. Um agendador puramente horario
    perderia o dia em silencio.

    `StartWhenAvailable` cobre o caso intermediario (maquina ligada mas ocupada
    ou suspensa no horario). `RestartCount 2` cobre queda de rede momentanea.
    `MultipleInstances IgnoreNew` impede que o gatilho de logon atropele uma
    execucao diaria em andamento.

    ATENCAO: nao ha `WakeToRun`. Acordar a maquina sozinha para publicar seria
    decisao do usuario sobre a propria maquina, nao do script.
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

$python = "C:\Users\Tiago Barros\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $python)) {
    $encontrado = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $encontrado) { throw "Nenhum interpretador Python encontrado." }
    $python = $encontrado.Source
}

function Registrar-Tarefa {
    param(
        [string]$Nome,
        [string]$Descricao,
        [object[]]$Gatilhos,
        [string]$Argumentos
    )
    $acao = New-ScheduledTaskAction -Execute $python -Argument $Argumentos `
        -WorkingDirectory $repo
    $config = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
        -LogonType Interactive -RunLevel Limited

    if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
        Write-Host "Substituindo tarefa existente: $Nome"
    }
    Register-ScheduledTask -TaskName $Nome -Description $Descricao `
        -Action $acao -Trigger $Gatilhos -Settings $config -Principal $principal | Out-Null
    Write-Host "Registrada: $Nome"
}

# --- Atualizacao das vitrines -------------------------------------------------
$diario = New-ScheduledTaskTrigger -Daily -At "19:30"
$logon  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Folga para o Docker Desktop subir antes de o orquestrador procurar o container.
$logon.Delay = "PT3M"

Registrar-Tarefa -Nome "DFU - Atualizar vitrines" `
    -Descricao ("Publica as vitrines vencidas (FII, B3, EUA) do armazem local " +
                "para o Supabase. Decide por cadencia, verifica pelo leitor da " +
                "tela e avisa no Telegram quando falha.") `
    -Gatilhos @($diario, $logon) `
    -Argumentos "`"$repo\scripts\atualizar_vitrines.py`""

# --- Coleta de noticias -------------------------------------------------------
# Este e o agendador REAL da coleta, e ele mora aqui e nao no GitHub Actions.
# O motivo e estrutural, o mesmo das vitrines: desde que o acervo passou a
# morar no armazem local, um runner na nuvem nao alcanca `noticias_itens`.
# Ligar o cron de la faria a coleta gastar cota de provedor e descartar tudo --
# o job avisa ("coleta nao persistida"), mas a requisicao ja foi paga.
#
# A CADA 30 MINUTOS, e nao a cada hora: 30 min e a granularidade do modo mais
# fino (Crise). O freio de cadencia mora no banco e descarta a execucao que o
# modo corrente nao pede, entao disparar de mais custa um processo ocioso, e
# disparar de menos custa noticia atrasada justamente no dia em que ela importa.
#
# Os :17 e :47 herdam a convencao do workflow: a virada da hora ja esta ocupada
# pelas outras rotinas, e escrita concorrente no compute Nano do Supabase foi
# problema antes.
$noticias = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(17)
$noticias.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
# Duracao finita e longa em vez de [TimeSpan]::MaxValue: o valor maximo e
# aceito por algumas versoes do agendador e recusado por outras, e a recusa
# aconteceria na maquina do usuario, meses depois deste script.

$logonNoticias = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logonNoticias.Delay = "PT3M"

Registrar-Tarefa -Nome "DFU - Coleta de noticias" `
    -Descricao ("Coleta noticias dos provedores e grava o acervo no armazem " +
                "local. Verifica o destino ANTES de gastar cota; o freio de " +
                "cadencia no banco descarta o que o modo corrente nao pede.") `
    -Gatilhos @($noticias, $logonNoticias) `
    -Argumentos "-m data_pipeline.cli_noticias"

# --- Backfill de documentos FII ----------------------------------------------
# Semanal, e nao diario: e enriquecimento incremental de PDFs, pesado e sem
# prazo de validade. Diario disputaria CPU e rede com a publicacao sem que a
# vitrine ficasse melhor por isso.
$semanal = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "09:00"
$acaoBackfill = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -File " +
               "`"$repo\scripts\run_fii_document_backfill.ps1`"") `
    -WorkingDirectory $repo
$configBackfill = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
$principalBackfill = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited

foreach ($obsoleta in @("DashboardFinanceiro-FII-Backfill",
                        "DFU - Republicar vitrine de FIIs")) {
    if (Get-ScheduledTask -TaskName $obsoleta -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $obsoleta -Confirm:$false
        Write-Host "Removida tarefa obsoleta: $obsoleta"
    }
}

if (Get-ScheduledTask -TaskName "DFU - Backfill documentos FII" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "DFU - Backfill documentos FII" -Confirm:$false
}
Register-ScheduledTask -TaskName "DFU - Backfill documentos FII" `
    -Description "Enriquecimento incremental dos documentos CVM de FII no armazem local." `
    -Action $acaoBackfill -Trigger $semanal -Settings $configBackfill `
    -Principal $principalBackfill | Out-Null
Write-Host "Registrada: DFU - Backfill documentos FII"

Write-Host ""
Get-ScheduledTask -TaskName "DFU - *" |
    Select-Object TaskName, State,
        @{n = "Proxima"; e = { (Get-ScheduledTaskInfo $_.TaskName).NextRunTime } } |
    Format-Table -AutoSize
