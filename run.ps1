# Executado pelo Agendador de Tarefas (oculto). Roda o publicador e grava log.
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$log = Join-Path $PSScriptRoot "publicar.log"
"==================================================" | Out-File -Append -Encoding utf8 $log
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - iniciando" | Out-File -Append -Encoding utf8 $log
& $py publicar.py 2>&1 | Out-File -Append -Encoding utf8 $log
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - fim (exit $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $log
