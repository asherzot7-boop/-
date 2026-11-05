# Agentni ishga tushiradi va to'xtab qolsa 10 soniyadan keyin qayta yoqadi. Task Scheduler chaqiradi.
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
New-Item -ItemType Directory -Force -Path "$Root\data\logs" | Out-Null
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
while ($true) {
    $log = "$Root\data\logs\agent_$(Get-Date -Format yyyy-MM-dd).log"
    # cmd orqali: jurnal UTF-8 bo'lib yoziladi (PowerShell 5 o'zi UTF-16 yozadi)
    # The outer pair of quotes is what cmd /c strips; the inner ones protect paths with spaces.
    cmd /c "`"`"$Root\.venv\Scripts\python.exe`" -m app.main >> `"$log`" 2>&1`""
    Add-Content -Path $log -Value "$(Get-Date -Format s) agent to'xtadi (kod $LASTEXITCODE), 10 soniyadan keyin qayta yoqiladi" -Encoding UTF8
    Start-Sleep -Seconds 10
}
