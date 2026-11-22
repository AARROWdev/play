# Runs inside a fresh Windows Sandbox: install PLAY, check what the installer set up, run it against a small
# generated library over HTTP and UDP, then uninstall and check everything is gone. Results -> C:\out\results.txt.
$ErrorActionPreference = 'Continue'
$log = 'C:\out\results.txt'
$fails = 0
function Check($name, $ok, $detail = '') {
    $line = "{0}  {1}  {2}" -f ($(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail)
    Add-Content $log $line
    if (-not $ok) { $script:fails++ }
}
Set-Content $log "PLAY sandbox run $(Get-Date -Format s)"

$setup = Get-ChildItem C:\play\dist\PLAYSetup-*.exe | Select-Object -First 1
$p = Start-Process $setup.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=autostart' -Wait -PassThru
Check 'installer exits 0' ($p.ExitCode -eq 0) "exit $($p.ExitCode)"
$exe = "$env:ProgramFiles\PLAY\PLAY.exe"
Check 'PLAY.exe installed in Program Files' (Test-Path $exe)
Check 'README, NAMING and LICENSE installed' ((Test-Path "$env:ProgramFiles\PLAY\README.md") -and (Test-Path "$env:ProgramFiles\PLAY\NAMING.md") -and (Test-Path "$env:ProgramFiles\PLAY\LICENSE"))
Check 'Start menu entry' (Test-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\PLAY.lnk")
$rule = netsh advfirewall firewall show rule name=PLAY verbose | Out-String
Check 'firewall rule: private only, for PLAY.exe' (($rule -match 'Profiles:\s+Private') -and ($rule -match [regex]::Escape($exe)) -and ($rule -match 'Action:\s+Allow')) ($rule -replace '\s+', ' ').Substring(0, [Math]::Min(200, $rule.Length))
$run = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name PLAY -ErrorAction SilentlyContinue).PLAY
Check 'start with Windows entry' ($run -eq "`"$exe`" --tray") "$run"

# a tiny library: two movies, one episode, one Kids film
$media = 'C:\Media'
New-Item -ItemType Directory -Force "$media\Movies", "$media\Kids" | Out-Null
$bytes = [byte[]](0..255) * 4000
[IO.File]::WriteAllBytes("$media\Movies\Jungle Park (1993).mp4", $bytes)
[IO.File]::WriteAllBytes("$media\Movies\Jungle World (2015).mp4", [byte[]](1..200) * 3000)
[IO.File]::WriteAllBytes("$media\Movies\Ann Droid S01E01.mkv", [byte[]](5..250) * 2000)
[IO.File]::WriteAllBytes("$media\Kids\Toy Shelf.mp4", [byte[]](9..99) * 2000)
New-Item -ItemType Directory -Force "$env:APPDATA\PLAY" | Out-Null
@{ folders = @(@{ path = "$media\Movies"; kids = $false }, @{ path = "$media\Kids"; kids = $true }) } |
    ConvertTo-Json -Depth 4 | Set-Content "$env:APPDATA\PLAY\library.json" -Encoding UTF8

Start-Process $exe -ArgumentList '--tray'
$info = $null
for ($i = 0; $i -lt 60 -and -not $info; $i++) {
    Start-Sleep 1
    try { $info = Invoke-RestMethod http://127.0.0.1:8600/api/info -TimeoutSec 2 } catch {}
}
Check 'server answers on 8600' ($info.name -eq 'PLAY') "$($info | ConvertTo-Json -Compress)"
$home_ = Invoke-RestMethod 'http://127.0.0.1:8600/api/tv?side=home' -TimeoutSec 10
$titles = ($home_.movies | ForEach-Object title) -join ', '
Check 'HOME lists the movies' (($home_.movies.Count -eq 3) -and ($home_.shows[0].name -eq 'Ann Droid')) $titles
$kids = Invoke-RestMethod 'http://127.0.0.1:8600/api/tv?side=kids' -TimeoutSec 10
Check 'KIDS lists only Kids' ((@($kids.movies).Count -eq 1) -and ($kids.movies[0].title -eq 'Toy Shelf')) (($kids.movies | ForEach-Object title) -join ', ')
$jp = $home_.movies | Where-Object { $_.title -eq 'Jungle Park' }
$req = [Net.HttpWebRequest]::Create("http://127.0.0.1:8600$($jp.url)")
$req.AddRange(256, 511)              # Windows PowerShell won't set a Range header through Invoke-WebRequest
$req.Timeout = 10000
$resp = $req.GetResponse()
$ms = New-Object IO.MemoryStream
$resp.GetResponseStream().CopyTo($ms)
$status = [int]$resp.StatusCode
$resp.Close()
$body = $ms.ToArray()
Check 'range request streams the right bytes' (($status -eq 206) -and ($body.Length -eq 256) -and ($body[0] -eq 0) -and ($body[255] -eq 255)) "status $status, $($body.Length) bytes"
try { $bad = Invoke-WebRequest 'http://127.0.0.1:8600/media/..%2F..%2FWindows%2Fwin.ini' -UseBasicParsing -TimeoutSec 5; $code = $bad.StatusCode } catch { $code = $_.Exception.Response.StatusCode.value__ }
Check 'paths outside the library are refused' ($code -eq 404) "status $code"

$udp = New-Object System.Net.Sockets.UdpClient
$udp.Client.ReceiveTimeout = 3000
$udp.EnableBroadcast = $true
$msg = [Text.Encoding]::ASCII.GetBytes('PLAY-DISCOVER')
[void]$udp.Send($msg, $msg.Length, '127.0.0.1', 8601)
$ep = New-Object System.Net.IPEndPoint ([Net.IPAddress]::Any, 0)
try { $reply = [Text.Encoding]::UTF8.GetString($udp.Receive([ref]$ep)) | ConvertFrom-Json } catch { $reply = $null }
$udp.Close()
Check 'discovery answers on UDP 8601' (($reply.name -eq 'PLAY') -and ($reply.port -eq 8600)) "$($reply | ConvertTo-Json -Compress)"

Start-Process $exe   # a second launch must not start a second server; it opens the first one's window
$n = 0
for ($i = 0; $i -lt 20; $i++) {     # the second copy needs a few seconds to start, hand over and exit
    Start-Sleep 1
    $n = @(Get-Process PLAY -ErrorAction SilentlyContinue).Count
    if ($i -ge 3 -and $n -eq 1) { break }
}
Check 'second launch hands over and exits' ($n -eq 1) "$n running after $($i + 1) s"
Check 'first copy still serving' ((Invoke-RestMethod http://127.0.0.1:8600/api/info -TimeoutSec 5).name -eq 'PLAY')
Check 'media files were not changed' (([IO.File]::ReadAllBytes("$media\Movies\Jungle Park (1993).mp4").Length -eq $bytes.Length) -and (@(Get-ChildItem $media -Recurse -File).Count -eq 4))
Check 'tags saved under APPDATA' (Test-Path "$env:APPDATA\PLAY\fingerprints.json")

# the uninstaller refuses while PLAY runs (silent = no prompt): quit it first, as the user would from the tray
Get-Process PLAY -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
$un = Start-Process "$env:ProgramFiles\PLAY\unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
Start-Sleep 3
Check 'uninstaller exits 0' ($un.ExitCode -eq 0) "exit $($un.ExitCode)"
Check 'PLAY.exe removed' (-not (Test-Path $exe))
$rule = netsh advfirewall firewall show rule name=PLAY | Out-String
Check 'firewall rule removed' ($rule -notmatch 'Rule Name:\s+PLAY')
$run = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name PLAY -ErrorAction SilentlyContinue).PLAY
Check 'start with Windows entry removed' (-not $run)
Check 'media still there after uninstall' (@(Get-ChildItem $media -Recurse -File).Count -eq 4)

Add-Content $log "DONE fails=$fails"
Stop-Computer -Force
