$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$task = Get-ScheduledTask -TaskName "NamTourAgent" -ErrorAction SilentlyContinue
if ($task) { Write-Host "Vazifa: $($task.State)" } else { Write-Host "NamTourAgent vazifasi yo'q: avval install.bat" }
try {
    $h = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 5
    Write-Host "Agent: ishlayapti (baza=$($h.database), AI=$($h.llm), demo=$($h.demo_mode))" -ForegroundColor Green
} catch { Write-Host "Agent: javob bermayapti" -ForegroundColor Red }
$last = Get-ChildItem "$Root\data\logs\agent_*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
if ($last) { Write-Host "`nJurnal oxiri ($($last.Name)):"; Get-Content $last.FullName -Tail 15 -Encoding UTF8 }
