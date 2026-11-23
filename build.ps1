# Builds PLAY for Windows:
#   build\dist\PLAY\PLAY.exe            the app (PyInstaller one-folder build)
#   dist\PLAYSetup-<ver>.exe            the installer (Inno Setup 6)
#   dist\PLAY-<ver>-portable-win64.zip  the portable version
#   dist\PLAY-roku.zip                  the Roku app package
# Needs: .venv with requirements + pyinstaller, and Inno Setup 6 (winget install JRSoftware.InnoSetup).
# PowerShell errors stop the build ('Stop'). Native tools run through Invoke-Step, which relaxes that only for the
# tool itself (Windows PowerShell 5.1 would otherwise turn PyInstaller's ordinary stderr log into a fatal error
# when the output is redirected, .\build.ps1 *> build.log) and fails on its exit code.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Invoke-Step([string]$what, [scriptblock]$cmd) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $cmd 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $old }   # to the output stream: *> log catches it
    if ($LASTEXITCODE) { throw "$what failed ($LASTEXITCODE)" }
}

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw '.venv not found: python -m venv .venv, then pip install -r requirements.txt pyinstaller' }
Remove-Item dist, build\dist -Recurse -Force -ErrorAction SilentlyContinue   # never ship a stale build

$m = Select-String -Path play\server.py -Pattern "^VERSION = '([^']+)'"
if (-not $m) { throw 'VERSION not found in play\server.py' }
$ver = $m.Matches[0].Groups[1].Value

Invoke-Step 'icon' { & $py tools\make_icon.py }
Invoke-Step 'PyInstaller' {
    & $py -m PyInstaller --noconfirm --windowed --name PLAY --icon "$PSScriptRoot\assets\play.ico" `
        --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
        --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia `
        --add-data "$PSScriptRoot\roku;roku" `
        --distpath build\dist --workpath build\work --specpath build play_main.py
}
$app = 'build\dist\PLAY'
if (-not (Test-Path "$app\PLAY.exe")) { throw 'PyInstaller produced no PLAY.exe' }
if (-not (Test-Path "$app\_internal\roku\manifest")) { throw 'the TV app is missing from the build (Set up TVs needs it)' }
Copy-Item README.md, LICENSE, NAMING.md $app

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
New-Item -ItemType Directory -Force dist | Out-Null
Invoke-Step 'Inno Setup' { & $iscc /Q "/DAppVersion=$ver" installer\play.iss }

$zip = "dist\PLAY-$ver-portable-win64.zip"
Compress-Archive -Path $app -DestinationPath $zip -Force
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::OpenRead((Resolve-Path $zip))
try { $ok = [bool]($z.Entries | Where-Object { $_.FullName -match '(^|/)PLAY\.exe$' }) } finally { $z.Dispose() }
if (-not $ok) { throw "$zip has no PLAY.exe (was a file locked?)" }

Invoke-Step 'Roku package' { & $py tools\roku_build.py }
Write-Host "Built $app\PLAY.exe, dist\PLAYSetup-$ver.exe, $zip, dist\PLAY-roku.zip"
