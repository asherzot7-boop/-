# Nam Tour sotuv agenti — Windows'ga o'rnatish.
# Ishga tushirish: install.bat ni ikki marta bosing (yoki: powershell -ExecutionPolicy Bypass -File install.ps1)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root

function Say($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ask($question) { $a = Read-Host "$question [h/y]"; return ($a -match '^(h|ha|y|yes|д|да|ҳ|ҳа)$') }

# 1. Python 3.12+
Say "Python tekshirilmoqda"
$PyExe = $null; $PyPre = @()
$candidates = @(@{Exe = "py"; Pre = @("-3.13")}, @{Exe = "py"; Pre = @("-3.12")}, @{Exe = "python"; Pre = @()},
                @{Exe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"; Pre = @()})
function Find-Python {
    foreach ($c in $candidates) {
        try {
            $pre = $c.Pre
            $ver = & $c.Exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver -and [version]$ver -ge [version]"3.12") { return $c }
        } catch { }
    }
    return $null
}
$found = Find-Python
if (-not $found) {
    Say "Python 3.12 o'rnatilmoqda (winget)"
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $found = Find-Python
}
if (-not $found) { throw "Python topilmadi. python.org dan Python 3.12 ni o'rnating va install.bat ni qayta ishga tushiring." }
$PyExe = $found.Exe; $PyPre = $found.Pre
Write-Host "Python: $PyExe $PyPre"

# 2. Virtual muhit va kutubxonalar
Say "Kutubxonalar o'rnatilmoqda (bir necha daqiqa)"
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
    & $PyExe @PyPre -m venv "$Root\.venv"
    if ($LASTEXITCODE -ne 0) { throw "Virtual muhit yaratilmadi" }
}
$Py = "$Root\.venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r "$Root\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { throw "Kutubxonalar o'rnatilmadi (internetni tekshiring va qayta urinib ko'ring)" }

# 3. Sozlamalar fayli
Say "Sozlamalar (.env)"
if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    # Noutbukda API faqat shu kompyuterga ochiq; internetdan faqat Cloudflare Tunnel orqali.
    Add-Content "$Root\.env" "`nAPI_HOST=127.0.0.1"
    # Panel paroli: tasodifiy API_TOKEN
    $tok = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    $lines = (Get-Content "$Root\.env" -Encoding UTF8) -replace '^API_TOKEN=$', "API_TOKEN=$tok"
    [IO.File]::WriteAllLines("$Root\.env", $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Panel paroli (API_TOKEN): $tok" -ForegroundColor Green
    # Mijoz fayllarini shifrlash kaliti
    $fk = & $Py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    $lines = (Get-Content "$Root\.env" -Encoding UTF8) -replace '^SALES_FILES_KEY=$', "SALES_FILES_KEY=$fk"
    [IO.File]::WriteAllLines("$Root\.env", $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Shifrlash kaliti yaratildi (.env -> SALES_FILES_KEY). Uni alohida joyda saqlang: yo'qolsa, fayllarni ochib bo'lmaydi." -ForegroundColor Yellow
    Write-Host ".env yaratildi. Hozir Bloknot ochiladi: SALES_BOT_TOKEN, SALES_MANAGER_IDS va boshqalarni to'ldiring, saqlang va yoping."
    Start-Process notepad.exe "$Root\.env" -Wait
} else {
    Write-Host ".env allaqachon bor, o'zgartirilmadi."
}

# 4. Ma'lumotlar bazasi
Say "Ma'lumotlar bazasi tayyorlanmoqda"
& $Py -m app.main migrate
if ($LASTEXITCODE -ne 0) { throw "Bazani tayyorlab bo'lmadi: .env dagi DATABASE_URL ni tekshiring" }

# 5. Kompyuter yoqilganda agent o'zi ishga tushsin
Say "Avtomatik ishga tushish (Task Scheduler)"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\deploy\windows\start.ps1`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$opts = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "NamTourAgent" -Action $action -Trigger $trigger -Settings $opts -Force | Out-Null
Write-Host "NamTourAgent vazifasi yaratildi: Windows'ga kirganingizda agent o'zi ishga tushadi."

# 6. Kunlik zaxira nusxa
Say "Kunlik zaxira nusxa"
Write-Host "Google Drive for desktop o'rnatilgan bo'lsa, uning papkasini kiriting (masalan: G:\My Drive\NamTour)."
$dest = Read-Host "Zaxira papkasi (bo'sh qoldirsangiz: $Root\data\backups)"
if (-not $dest) { $dest = "$Root\data\backups" }
$bAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\deploy\windows\backup.ps1`" -To `"$dest`"" -WorkingDirectory $Root
$bTrigger = New-ScheduledTaskTrigger -Daily -At 3am
$bOpts = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "NamTourBackup" -Action $bAction -Trigger $bTrigger -Settings $bOpts -Force | Out-Null
Write-Host "Har kuni soat 03:00 da (kompyuter o'chiq bo'lsa — yoqilgandan keyin) zaxira: $dest"

# 7. Uyqu rejimi
Say "Uyqu rejimi"
if (Ask "Zaryadkaga ulanganda noutbuk uxlamasin va qopqoq yopilganda ham ishlasinmi?") {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
    powercfg /setactive SCHEME_CURRENT
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Tayyor: zaryadkada noutbuk uxlamaydi. Batareyada odatdagidek uxlaydi."
    } else {
        Write-Host "Quvvat sozlamasi o'zgarmadi: install.bat ni o'ng tugma -> 'Run as administrator' bilan qayta ishga tushiring." -ForegroundColor Yellow
    }
}

# 8. Cloudflare Tunnel (Instagram, WhatsApp, telefon uchun)
Say "Cloudflare Tunnel"
if (Ask "Instagram/WhatsApp/telefon ulanadimi? (cloudflared o'rnatiladi)") {
    winget install -e --id Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements
    Write-Host "Keyingi qadamlar: deploy\windows\README.md -> 'Cloudflare Tunnel' bo'limi."
} else {
    Write-Host "O'tkazib yuborildi. Telegram tunnelsiz ham ishlaydi."
}

# 9. Ishga tushirish
Say "Agent ishga tushirilmoqda"
Start-ScheduledTask -TaskName "NamTourAgent"
Start-Sleep -Seconds 8
try {
    $h = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 5
    Write-Host "Agent ishlayapti: baza=$($h.database), AI=$($h.llm)" -ForegroundColor Green
} catch {
    Write-Host "Agent hali javob bermadi. Jurnalni ko'ring: $Root\data\logs" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Tayyor. Holat: status.bat | To'xtatish: stop.bat | Jurnal: data\logs"
Write-Host "Panel: http://127.0.0.1:8000/panel (kirish: .env dagi API_TOKEN)"
