Stop-ScheduledTask -TaskName "NamTourAgent" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*app.main*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "Agent to'xtatildi. Qayta yoqish: Start-ScheduledTask NamTourAgent yoki kompyuterga qayta kiring."
