# Builds build\NamTour-Setup.exe on Windows: embedded Python + libraries + the app, checked, then packed by Inno Setup.
# Needs Python 3.12 (64-bit) on PATH; GitHub Actions runs it on every push (.github/workflows/windows-installer.yml).
#   powershell -ExecutionPolicy Bypass -File deploy\windows\installer\build.ps1 -Version 1.0.0
param([string]$Version = "1.0.0", [string]$PythonVersion = "3.12.10")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$Build = "$Repo\build"
$Out = "$Build\NamTour"
if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Write-Host "==> Embedded Python $PythonVersion"
Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile "$Build\python.zip"
Expand-Archive "$Build\python.zip" "$Out\python"
# Import paths of the embedded interpreter: its stdlib, the libraries, and the install folder (..) for `app`.
Set-Content -Path "$Out\python\python312._pth" -Value "python312.zip`r`n.`r`nLib\site-packages`r`n..`r`nimport site" -Encoding ascii

Write-Host "==> Libraries"
python -c "import sys, struct; assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, sys.version"
python -m pip install --disable-pip-version-check --no-cache-dir --target "$Out\python\Lib\site-packages" -r "$Repo\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Application files"
Copy-Item -Recurse "$Repo\app" "$Out\app"
foreach ($f in @("alembic.ini", ".env.example", "sales_catalog.json", "sales_faq.json", "sales_eval_cases.json", "sales_contract_template.txt")) {
    Copy-Item "$Repo\$f" "$Out\$f"
}
if (Test-Path "$Repo\visual") { Copy-Item -Recurse "$Repo\visual" "$Out\visual" }
Get-ChildItem -Path $Out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
python "$PSScriptRoot\make_icon.py" "$Out\namtour.ico"

Write-Host "==> Smoke test: the embedded copy starts, migrates and answers /health"
$Smoke = "$Build\smoke"
Copy-Item -Recurse $Out $Smoke
$Py = "$Smoke\python\python.exe"
& $Py -c "import app.main, app.desktop, fastapi, aiogram, cryptography, anthropic, fpdf; print('imports ok')"
if ($LASTEXITCODE -ne 0) { throw "imports failed in the embedded Python" }
& $Py -c "from pathlib import Path; from app import desktop; desktop.ensure_env(Path(r'$Smoke'))"
$agent = Start-Process -FilePath $Py -ArgumentList "-m", "app.main" -WorkingDirectory $Smoke -PassThru -NoNewWindow `
    -RedirectStandardOutput "$Build\smoke-out.log" -RedirectStandardError "$Build\smoke-err.log"
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
    Start-Sleep -Seconds 2
    try { $ok = (Invoke-WebRequest "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { }
}
$setup = try { (Invoke-WebRequest "http://127.0.0.1:8000/setup" -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 0 }
Stop-Process -Id $agent.Id -Force -ErrorAction SilentlyContinue
if (-not $ok -or $setup -ne 200) {
    Get-Content "$Build\smoke-out.log", "$Build\smoke-err.log" -ErrorAction SilentlyContinue | Select-Object -Last 60
    throw "the agent did not start from the embedded Python (health ok: $ok, /setup: $setup)"
}
Write-Host "health ok, /setup ok"

Write-Host "==> Installer"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    choco install innosetup -y --no-progress | Out-Null
}
if (-not (Test-Path $iscc)) { throw "Inno Setup (ISCC.exe) not found" }
& $iscc "/DAppVersion=$Version" "/DSourceDir=$Out" "/O$Build" "$PSScriptRoot\namtour.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Get-Item "$Build\NamTour-Setup.exe" | Format-List Name, Length
