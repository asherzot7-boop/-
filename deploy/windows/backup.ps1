# Baza va mijoz hujjatlarining zaxira nusxasi. Task Scheduler har kuni chaqiradi.
param([string]$To = "")
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force -Path "$Root\data\logs" | Out-Null
$log = "$Root\data\logs\backup.log"
if ($To) { $args2 = @("-m", "app.main", "backup", "--to", $To) } else { $args2 = @("-m", "app.main", "backup") }
& "$Root\.venv\Scripts\python.exe" @args2 2>&1 | Out-File -FilePath $log -Append -Encoding UTF8
