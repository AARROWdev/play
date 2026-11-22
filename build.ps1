# Builds PLAY for Windows:
#   build\dist\PLAY\PLAY.exe            the app (PyInstaller one-folder build)
#   dist\PLAYSetup-<ver>.exe            the installer (Inno Setup 6)
#   dist\PLAY-<ver>-portable-win64.zip  the portable version
#   dist\PLAY-roku.zip                  the Roku app package
# Needs: .venv with requirements + pyinstaller, and Inno Setup 6 (winget install JRSoftware.InnoSetup).
# 'Continue', with every native step checked through $LASTEXITCODE: under 'Stop', Windows PowerShell 5.1 turns
# PyInstaller's ordinary stderr log into a fatal error when the output is redirected (.\build.ps1 *> build.log).
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
Remove-Item dist -Recurse -Force -ErrorAction SilentlyContinue

$m = Select-String -Path play\server.py -Pattern "^VERSION = '([^']+)'"
if (-not $m) { throw 'VERSION not found in play\server.py' }
$ver = $m.Matches[0].Groups[1].Value

.\.venv\Scripts\python tools\make_icon.py
if ($LASTEXITCODE) { throw "icon failed ($LASTEXITCODE)" }
.\.venv\Scripts\python -m PyInstaller --noconfirm --windowed --name PLAY --icon "$PSScriptRoot\assets\play.ico" `
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia `
    --distpath build\dist --workpath build\work --specpath build play_main.py
if ($LASTEXITCODE) { throw "PyInstaller failed ($LASTEXITCODE)" }

$app = 'build\dist\PLAY'
Copy-Item README.md, LICENSE, NAMING.md $app -ErrorAction Stop

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
New-Item -ItemType Directory -Force dist | Out-Null
& $iscc /Q "/DAppVersion=$ver" installer\play.iss
if ($LASTEXITCODE) { throw "Inno Setup failed ($LASTEXITCODE)" }

Compress-Archive -Path $app -DestinationPath "dist\PLAY-$ver-portable-win64.zip" -Force
.\.venv\Scripts\python tools\roku_build.py
if ($LASTEXITCODE) { throw "Roku package failed ($LASTEXITCODE)" }

Write-Host "Built $app\PLAY.exe, dist\PLAYSetup-$ver.exe, dist\PLAY-$ver-portable-win64.zip, dist\PLAY-roku.zip"
