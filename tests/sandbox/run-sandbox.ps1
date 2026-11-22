# Installs and checks PLAY in a fresh Windows Sandbox (tests\sandbox\inside.ps1): installer, firewall rule, start with
# Windows, the server over HTTP and UDP, single instance, then uninstall. Nothing runs on this PC.
# Needs build.ps1 run first and Windows Sandbox turned on. Results are printed at the end.
param([int]$Minutes = 15)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
if (-not (Test-Path "$root\dist\PLAYSetup-*.exe")) { throw 'Run build.ps1 first' }
$out = Join-Path $env:TEMP "play-sandbox-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory $out | Out-Null
@"
<Configuration>
  <Networking>Disable</Networking>
  <MemoryInMB>4096</MemoryInMB>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MappedFolders>
    <MappedFolder><HostFolder>$root</HostFolder><SandboxFolder>C:\play</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$out</HostFolder><SandboxFolder>C:\out</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell -NoProfile -ExecutionPolicy Bypass -File C:\play\tests\sandbox\inside.ps1</Command></LogonCommand>
</Configuration>
"@ | Set-Content "$out\play.wsb" -Encoding UTF8
if (Get-Process WindowsSandbox* -ErrorAction SilentlyContinue) { throw 'A Windows Sandbox is already running; close it first' }
Start-Process "$out\play.wsb" -WindowStyle Minimized
$deadline = (Get-Date).AddMinutes($Minutes)
while ((Get-Date) -lt $deadline -and -not ((Test-Path "$out\results.txt") -and (Select-String -Path "$out\results.txt" -Pattern '^DONE' -Quiet -ErrorAction SilentlyContinue))) {
    Start-Sleep 5
}
if (Test-Path "$out\results.txt") { Get-Content "$out\results.txt" } else { Write-Host "No results after $Minutes minutes ($out)" }
Get-Process WindowsSandbox* -ErrorAction SilentlyContinue | Stop-Process -Force
