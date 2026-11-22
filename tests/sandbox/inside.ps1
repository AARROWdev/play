# Runs inside a fresh Windows Sandbox: install PLAY, check what the installer set up, run it against a small
# generated library over HTTP and UDP, then uninstall and check everything is gone. Results -> C:\out\results.txt.
# This installs, kills PLAY and shuts the machine down: only ever inside Windows Sandbox. `exit`, not `throw`:
# a caller's ErrorActionPreference of SilentlyContinue would skip a throw.
if ($env:USERNAME -ne 'WDAGUtilityAccount') { Write-Host 'inside.ps1 runs only inside Windows Sandbox (use run-sandbox.ps1)'; exit 1 }
$ErrorActionPreference = 'Continue'
$log = 'C:\out\results.txt'
$fails = 0
# T 'name' { ...; return $ok, 'detail' }  -- an exception inside the block is a FAIL, never a skipped line
function T($name, [scriptblock]$test) {
    try {
        $r = @(& $test)
        $ok = [bool]$r[0]
        $detail = if ($r.Count -gt 1) { "$($r[1])" } else { '' }
    } catch {
        $ok = $false
        $detail = "error: $($_.Exception.Message)"
    }
    Add-Content $log ("{0}  {1}  {2}" -f ($(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail))
    if (-not $ok) { $script:fails++ }
}
function Rules { @(netsh advfirewall firewall show rule name=PLAY verbose | Out-String) }
Set-Content $log "PLAY sandbox run $(Get-Date -Format s)"

$setup = (Get-ChildItem C:\play\dist\PLAYSetup-*.exe | Select-Object -First 1).FullName
$exe = "$env:ProgramFiles\PLAY\PLAY.exe"
T 'installer exits 0' { $p = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=autostart' -Wait -PassThru; ($p.ExitCode -eq 0), "exit $($p.ExitCode)" }
T 'PLAY.exe installed in Program Files' { Test-Path $exe }
T 'README, NAMING and LICENSE installed' { (Test-Path "$env:ProgramFiles\PLAY\README.md") -and (Test-Path "$env:ProgramFiles\PLAY\NAMING.md") -and (Test-Path "$env:ProgramFiles\PLAY\LICENSE") }
T 'Start menu entry' { Test-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\PLAY.lnk" }
T 'one firewall rule: enabled, inbound, allow, Private only, for PLAY.exe' {
    $r = Rules
    $names = ([regex]::Matches($r, '(?m)^Rule Name:\s+PLAY\s*$')).Count
    $prof = ([regex]::Match($r, '(?m)^Profiles:\s+(.+?)\s*$')).Groups[1].Value
    $ok = ($names -eq 1) -and ($prof -eq 'Private') -and ($r -match '(?m)^Enabled:\s+Yes') -and ($r -match '(?m)^Direction:\s+In') -and ($r -match '(?m)^Action:\s+Allow') -and ($r -match [regex]::Escape($exe))
    $ok, "rules=$names profiles='$prof'"
}
T 'start with Windows entry' { $run = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name PLAY -ErrorAction SilentlyContinue).PLAY; ($run -eq "`"$exe`" --tray"), "$run" }

# a tiny library: two movies, one episode, one Kids film
$media = 'C:\Media'
New-Item -ItemType Directory -Force "$media\Movies", "$media\Kids" | Out-Null
$bytes = [byte[]](0..255) * 4000
[IO.File]::WriteAllBytes("$media\Movies\Jungle Park (1993).mp4", $bytes)
[IO.File]::WriteAllBytes("$media\Movies\Jungle World (2015).mp4", [byte[]](1..200) * 3000)
[IO.File]::WriteAllBytes("$media\Movies\Ann Droid S01E01.mkv", [byte[]](5..250) * 2000)
[IO.File]::WriteAllBytes("$media\Kids\Toy Shelf.mp4", [byte[]](9..99) * 2000)
function MediaState { (Get-ChildItem $media -Recurse -File | Sort-Object FullName | ForEach-Object { "$($_.FullName)|$((Get-FileHash $_.FullName -Algorithm SHA256).Hash)" }) -join "`n" }
$before = MediaState
New-Item -ItemType Directory -Force "$env:APPDATA\PLAY" | Out-Null
@{ folders = @(@{ path = "$media\Movies"; kids = $false }, @{ path = "$media\Kids"; kids = $true }) } |
    ConvertTo-Json -Depth 4 | Set-Content "$env:APPDATA\PLAY\library.json" -Encoding UTF8   # with a BOM, on purpose

Start-Process $exe -ArgumentList '--tray'
$info = $null
for ($i = 0; $i -lt 60 -and -not $info; $i++) {
    Start-Sleep 1
    try { $info = Invoke-RestMethod http://127.0.0.1:8600/api/info -TimeoutSec 2 } catch {}
}
T 'server answers on 8600' { ($info.name -eq 'PLAY'), "$($info | ConvertTo-Json -Compress)" }
$homeTv = $null
T 'HOME lists the movies and the show' {
    $script:homeTv = Invoke-RestMethod 'http://127.0.0.1:8600/api/tv?side=home' -TimeoutSec 10
    ((@($homeTv.movies).Count -eq 3) -and ($homeTv.shows[0].name -eq 'Ann Droid')), ((@($homeTv.movies) | ForEach-Object title) -join ', ')
}
T 'KIDS lists only Kids' {
    $k = Invoke-RestMethod 'http://127.0.0.1:8600/api/tv?side=kids' -TimeoutSec 10
    $t = @(@($k.movies) | ForEach-Object title)
    (($t.Count -eq 1) -and ($t[0] -eq 'Toy Shelf')), ($t -join ', ')
}
T 'range request streams the right bytes' {
    $jp = @($homeTv.movies) | Where-Object { $_.title -eq 'Jungle Park' }
    $req = [Net.HttpWebRequest]::Create("http://127.0.0.1:8600$($jp.url)")
    $req.AddRange(256, 511)              # Windows PowerShell won't set a Range header through Invoke-WebRequest
    $req.Timeout = 10000
    $resp = $req.GetResponse()
    $ms = New-Object IO.MemoryStream
    $resp.GetResponseStream().CopyTo($ms)
    $status = [int]$resp.StatusCode
    $resp.Close()
    $body = $ms.ToArray()
    (($status -eq 206) -and ($body.Length -eq 256) -and ($body[0] -eq 0) -and ($body[255] -eq 255)), "status $status, $($body.Length) bytes"
}
T 'paths outside the library are refused' {
    try { $code = (Invoke-WebRequest 'http://127.0.0.1:8600/media/..%2F..%2FWindows%2Fwin.ini' -UseBasicParsing -TimeoutSec 5).StatusCode }
    catch { $code = $_.Exception.Response.StatusCode.value__ }
    ($code -eq 404), "status $code"
}
T 'discovery answers on UDP 8601' {
    $udp = New-Object System.Net.Sockets.UdpClient
    $udp.Client.ReceiveTimeout = 3000
    $msg = [Text.Encoding]::ASCII.GetBytes('PLAY-DISCOVER')
    [void]$udp.Send($msg, $msg.Length, '127.0.0.1', 8601)
    $ep = New-Object System.Net.IPEndPoint ([Net.IPAddress]::Any, 0)
    $reply = [Text.Encoding]::UTF8.GetString($udp.Receive([ref]$ep)) | ConvertFrom-Json
    $udp.Close()
    (($reply.name -eq 'PLAY') -and ($reply.port -eq 8600)), "$($reply | ConvertTo-Json -Compress)"
}

# a second launch hands over: the first copy (started in the tray, window hidden) shows its window and keeps serving
# Start-Process -PassThru's PID isn't always the PLAY that ends up running, so find the first copy by itself
$firstPid = @(Get-Process PLAY -ErrorAction SilentlyContinue)[0].Id
$second = Start-Process $exe -PassThru
T 'second launch exits' { $done = $second.WaitForExit(20000); $done, "exited=$done" }
T 'first copy kept running and showed its window' {
    $h = 0
    for ($i = 0; $i -lt 10 -and $h -eq 0; $i++) { Start-Sleep 1; $h = (Get-Process -Id $firstPid -ErrorAction Stop).MainWindowHandle }
    ($h -ne 0), "pid $firstPid window $h"
}
T 'first copy still serving' { (Invoke-RestMethod http://127.0.0.1:8600/api/info -TimeoutSec 5).name -eq 'PLAY' }
T 'only one PLAY running' { $n = @(Get-Process PLAY -ErrorAction SilentlyContinue).Count; ($n -eq 1), "$n running" }
T 'media files unchanged (names and contents)' { (MediaState) -eq $before }
T 'fingerprint cache written under APPDATA' { Test-Path "$env:APPDATA\PLAY\fingerprints.json" }

# reinstall with "Start with Windows" unticked: the entry goes
T 'reinstall without autostart removes the entry' {
    Get-Process PLAY -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    $p = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=' -Wait -PassThru
    $run = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name PLAY -ErrorAction SilentlyContinue).PLAY
    (($p.ExitCode -eq 0) -and -not $run), "exit $($p.ExitCode), run='$run'"
}

# the uninstaller refuses while PLAY runs (silent = no prompt): quit it first, as the user would from the tray
Get-Process PLAY -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
T 'uninstaller exits 0' { $un = Start-Process "$env:ProgramFiles\PLAY\unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru; Start-Sleep 3; ($un.ExitCode -eq 0), "exit $($un.ExitCode)" }
T 'PLAY.exe removed' { -not (Test-Path $exe) }
T 'firewall rule removed' { $r = Rules; ($r -notmatch '(?m)^Rule Name:\s+PLAY\s*$'), (($r -replace '\s+', ' ').Trim()) }
T 'start with Windows entry removed' { -not (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name PLAY -ErrorAction SilentlyContinue).PLAY }
T 'media unchanged after uninstall' { (MediaState) -eq $before }

Add-Content $log "DONE fails=$fails"
Stop-Computer -Force
