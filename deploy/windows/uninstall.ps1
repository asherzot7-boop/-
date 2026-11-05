& "$PSScriptRoot\stop.ps1"
Unregister-ScheduledTask -TaskName "NamTourAgent" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "NamTourBackup" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Avtomatik ishga tushish va zaxira o'chirildi. Ma'lumotlar (data papkasi) o'chirilmadi."
