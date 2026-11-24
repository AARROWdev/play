# Runs inside a fresh Windows Sandbox: install PLAY, check what the installer set up, run it against a small
# generated library over HTTP and UDP, then uninstall and check everything is gone. Results -> C:\out\results.txt.
# This installs, kills PLAY and shuts the machine down: only ever inside Windows Sandbox. `exit`, not `throw`:
# a caller's ErrorActionPreference of SilentlyContinue would skip a throw.
if ($env:USERNAME -ne 'WDAGUtilityAccount') { Write-Host 'inside.ps1 runs only inside Windows Sandbox (use run-sandbox.ps1)'; exit 1 }
$ErrorActionPreference = 'Continue'
$log = 'C:\out\results.txt'
$fails = 0
# T 'name' { ...; Verdict $ok 'detail' }. The verdict is the block's LAST output (a [bool] or an R object), so anything a
# command writes earlier can't make a failing check pass; an exception is a FAIL, never a skipped line.
function Verdict($ok, $detail = '') { [pscustomobject]@{ ok = [bool]$ok; detail = "$detail" } }
function T($name, [scriptblock]$test) {
    try {
        $r = @(& $test)
        $last = if ($r.Count) { $r[-1] } else { $null }
        if ($last -is [bool]) { $ok, $detail = $last, '' }
        elseif ($null -ne $last -and $last.PSObject.Properties['ok']) { $ok, $detail = $last.ok, $last.detail }
        else { $ok, $detail = $false, "no verdict (last output: $last)" }
    } catch {
        $ok = $false
        $detail = "error: $($_.Exception.Message)"
    }
    Add-Content $log ("{0}  {1}  {2}" -f ($(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail))
    if (-not $ok) { $script:fails++ }
}
function RunValue { (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name PLAY -ErrorAction SilentlyContinue).PLAY }
function FirewallRules { @(Get-NetFirewallRule -DisplayName PLAY -ErrorAction SilentlyContinue) }   # structured, any language
function WaitFor([scriptblock]$cond, [int]$seconds) {
    for ($i = 0; $i -lt $seconds * 4; $i++) { if (& $cond) { return $true }; Start-Sleep -Milliseconds 250 }
    return [bool](& $cond)
}
Set-Content $log "PLAY sandbox run $(Get-Date -Format s)"

$setup = (Get-ChildItem C:\play\dist\PLAYSetup-*.exe | Select-Object -First 1).FullName
$exe = "$env:ProgramFiles\PLAY\PLAY.exe"
T 'installer exits 0' { $p = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru; Verdict ($p.ExitCode -eq 0) "exit $($p.ExitCode)" }
T 'PLAY.exe installed in Program Files' { Test-Path $exe }
T 'README, NAMING and LICENSE installed' { (Test-Path "$env:ProgramFiles\PLAY\README.md") -and (Test-Path "$env:ProgramFiles\PLAY\NAMING.md") -and (Test-Path "$env:ProgramFiles\PLAY\LICENSE") }
T 'Start menu entry' { Test-Path "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\PLAY.lnk" }
T 'one firewall rule: enabled, inbound, allow, Private only, for PLAY.exe' {
    $rules = @(FirewallRules)
    if ($rules.Count -ne 1) { return Verdict $false "rules=$($rules.Count)" }
    $r = $rules[0]
    $app = ($r | Get-NetFirewallApplicationFilter).Program
    $ok = ($r.Enabled -eq 'True') -and ($r.Direction -eq 'Inbound') -and ($r.Action -eq 'Allow') -and ("$($r.Profile)" -eq 'Private') -and ($app -eq $exe)
    Verdict $ok "enabled=$($r.Enabled) dir=$($r.Direction) action=$($r.Action) profile=$($r.Profile) program=$app"
}
T 'installer sets no start entry (PLAY does, at its first start)' { Verdict (-not (RunValue)) "run='$(RunValue)'" }

# a tiny library: two movies, one episode, one Kids film
$media = 'C:\Media'
New-Item -ItemType Directory -Force "$media\Movies", "$media\Kids" | Out-Null
$bytes = [byte[]](0..255) * 4000
[IO.File]::WriteAllBytes("$media\Movies\Jungle Park (1993).mp4", $bytes)
[IO.File]::WriteAllBytes("$media\Movies\Jungle World (2015).mp4", [byte[]](1..200) * 3000)
[IO.File]::WriteAllBytes("$media\Movies\Ann Droid S01E01.mkv", [byte[]](5..250) * 2000)
[IO.File]::WriteAllBytes("$media\Kids\Toy Shelf.mp4", [byte[]](9..99) * 2000)
# backdate them: brand-new files wait 10 s to settle (still being copied?), and this run shouldn't have to
Get-ChildItem $media -Recurse -File | ForEach-Object { $_.LastWriteTime = (Get-Date).AddHours(-1); $_.CreationTime = (Get-Date).AddHours(-1) }
function MediaState { (Get-ChildItem $media -Recurse -File | Sort-Object FullName | ForEach-Object { "$($_.FullName)|$((Get-FileHash $_.FullName -Algorithm SHA256).Hash)" }) -join "`n" }
$before = MediaState
# a first start, as after a real install: PLAY turns on Start with Windows, then (no folders yet) waits in Settings
Start-Process $exe -ArgumentList '--tray'
T 'first start turned on Start with Windows' {
    [void](WaitFor { RunValue } 30)
    Verdict ((RunValue) -eq "`"$exe`" --tray") "$(RunValue)"
}
Get-Process PLAY -ErrorAction SilentlyContinue | Stop-Process -Force
[void](WaitFor { -not (Get-Process PLAY -ErrorAction SilentlyContinue) } 10)
New-Item -ItemType Directory -Force "$env:APPDATA\PLAY" | Out-Null
@{ folders = @(@{ path = "$media\Movies"; kids = $false }, @{ path = "$media\Kids"; kids = $true }) } |
    ConvertTo-Json -Depth 4 | Set-Content "$env:APPDATA\PLAY\library.json" -Encoding UTF8   # with a BOM, on purpose

Start-Process $exe -ArgumentList '--tray'
$info = $null
[void](WaitFor { try { $script:info = Invoke-RestMethod http://127.0.0.1:8600/api/info -TimeoutSec 2 } catch {}; $script:info } 60)
T 'server answers on 8600' { Verdict ($info.name -eq 'PLAY') "$($info | ConvertTo-Json -Compress)" }
$homeTv = $null
T 'HOME lists the movies and the show' {
    [void](WaitFor { try { $script:homeTv = Invoke-RestMethod 'http://127.0.0.1:8600/api/tv?side=home' -TimeoutSec 10 } catch {}; $script:homeTv } 30)
    Verdict ((@($homeTv.movies).Count -eq 3) -and ($homeTv.shows[0].name -eq 'Ann Droid')) ((@($homeTv.movies) | ForEach-Object title) -join ', ')
}
T 'KIDS lists only Kids' {
    $k = Invoke-RestMethod 'http://127.0.0.1:8600/api/tv?side=kids' -TimeoutSec 10
    $t = @(@($k.movies) | ForEach-Object title)
    Verdict (($t.Count -eq 1) -and ($t[0] -eq 'Toy Shelf')) ($t -join ', ')
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
    Verdict (($status -eq 206) -and ($body.Length -eq 256) -and ($body[0] -eq 0) -and ($body[255] -eq 255)) "status $status, $($body.Length) bytes"
}
T 'paths outside the library are refused' {
    try { $code = (Invoke-WebRequest 'http://127.0.0.1:8600/media/..%2F..%2FWindows%2Fwin.ini' -UseBasicParsing -TimeoutSec 5).StatusCode }
    catch { $code = $_.Exception.Response.StatusCode.value__ }
    Verdict ($code -eq 404) "status $code"
}
T 'discovery answers on UDP 8601' {
    $udp = New-Object System.Net.Sockets.UdpClient
    $udp.Client.ReceiveTimeout = 3000
    $msg = [Text.Encoding]::ASCII.GetBytes('PLAY-DISCOVER')
    [void]$udp.Send($msg, $msg.Length, '127.0.0.1', 8601)
    $ep = New-Object System.Net.IPEndPoint ([Net.IPAddress]::Any, 0)
    $reply = [Text.Encoding]::UTF8.GetString($udp.Receive([ref]$ep)) | ConvertFrom-Json
    $udp.Close()
    Verdict (($reply.name -eq 'PLAY') -and ($reply.port -eq 8600)) "$($reply | ConvertTo-Json -Compress)"
}
T 'second start leaves Start with Windows as it is' { Verdict ((RunValue) -eq "`"$exe`" --tray") "$(RunValue)" }
T 'the Kids tag was saved in library.json' {
    $j = Get-Content "$env:APPDATA\PLAY\library.json" -Raw | ConvertFrom-Json
    $tagged = @($j.items.PSObject.Properties | Where-Object { $_.Value.kids -eq $true -and $_.Value.kids_folder_seen -eq $true })
    Verdict ($tagged.Count -eq 1) "kids-tagged items: $($tagged.Count)"
}

# a second launch hands over: the first copy (started in the tray, window hidden) shows its window and keeps serving
# (Start-Process -PassThru's PID isn't always the PLAY that ends up running, so find the first copy by itself)
$firstPid = @(Get-Process PLAY -ErrorAction SilentlyContinue)[0].Id
T 'first copy has no window yet' { Verdict ((Get-Process -Id $firstPid).MainWindowHandle -eq 0) "window $((Get-Process -Id $firstPid).MainWindowHandle)" }
$second = Start-Process $exe -PassThru
T 'second launch exits' { Verdict ($second.WaitForExit(20000)) "exited" }
T 'first copy kept running and showed its window' {
    $shown = WaitFor { (Get-Process -Id $firstPid -ErrorAction Stop).MainWindowHandle -ne 0 } 10
    Verdict $shown "pid $firstPid window $((Get-Process -Id $firstPid).MainWindowHandle)"
}
T 'first copy still serving' { (Invoke-RestMethod http://127.0.0.1:8600/api/info -TimeoutSec 5).name -eq 'PLAY' }
T 'only one PLAY running' { $n = @(Get-Process PLAY -ErrorAction SilentlyContinue).Count; Verdict ($n -eq 1) "$n running" }
T 'media files unchanged (names, case and contents)' { (MediaState) -ceq $before }

# Add media, driven like a person would: click the button, type a path into the file dialog, answer Yes. The file must
# be COPIED into the default (first) folder; the original has to stay where it is, byte for byte.
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y); [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);' -Name Mouse -Namespace Win
$srcDir = 'C:\Src'
$src = "$srcDir\Added Film (2005).mp4"
New-Item -ItemType Directory -Force $srcDir | Out-Null
[IO.File]::WriteAllBytes($src, [byte[]](3..240) * 5000)
(Get-Item $src).LastWriteTime = (Get-Date).AddHours(-1)
$srcHash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
$copy = "$media\Movies\Added Film (2005).mp4"
$ae = [System.Windows.Automation.AutomationElement]
$win = $ae::FromHandle((Get-Process -Id $firstPid).MainWindowHandle)
$byName = New-Object System.Windows.Automation.PropertyCondition($ae::NameProperty, ('Add media' + [char]0x2026))
$btn = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $byName)
T 'the Add media button is in the window' { $null -ne $btn }
if ($btn) {
    $r = $btn.Current.BoundingRectangle
    [void][Win.Mouse]::SetCursorPos([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2))
    Start-Sleep -Milliseconds 300
    [Win.Mouse]::mouse_event(2, 0, 0, 0, 0); [Win.Mouse]::mouse_event(4, 0, 0, 0, 0)    # left down, up
    Start-Sleep 4                                                                       # the file dialog opens with its name box focused
    [System.Windows.Forms.SendKeys]::SendWait(($src -replace '[\+\^%~(){}\[\]]', '{$0}') + '{ENTER}')
    Start-Sleep 3                                                                       # "Copy 1 file ... into ...?" (Yes is the default)
    [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
}
T 'Add media copied the file into the default folder' {
    $ok = WaitFor { Test-Path -LiteralPath $copy } 30
    Verdict ($ok -and ((Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash -eq $srcHash)) "copy exists: $ok"
}
T 'Add media left the original in place, unchanged' { Verdict ((Test-Path -LiteralPath $src) -and ((Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash -eq $srcHash)) "original exists: $(Test-Path -LiteralPath $src)" }
T 'the added film shows up in the library' {
    $found = WaitFor { try { @((Invoke-RestMethod 'http://127.0.0.1:8600/api/tv?side=home' -TimeoutSec 5).movies | Where-Object { $_.title -eq 'Added Film' }).Count -eq 1 } catch { $false } } 60
    Verdict $found "listed: $found"
}
T 'Add media changed nothing else in the media folder (no rename, rewrite or leftover .playpart)' {
    $expected = (@($before -split "`n") + "$copy|$srcHash") | Sort-Object { $_.Split('|')[0] }
    Verdict ((MediaState) -ceq ($expected -join "`n")) 'media folder is the old files plus exactly the copy'
}
$before = MediaState                                      # (the media folder now holds the added copy)

# the uninstaller refuses while PLAY runs (silent = no prompt)
T 'uninstaller refuses while PLAY runs' {
    $un = Start-Process "$env:ProgramFiles\PLAY\unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
    Verdict ((Test-Path $exe) -and $un.ExitCode -ne 0) "exit $($un.ExitCode), still installed: $(Test-Path $exe)"
}
Get-Process PLAY -ErrorAction SilentlyContinue | Stop-Process -Force
[void](WaitFor { -not (Get-Process PLAY -ErrorAction SilentlyContinue) } 10)
T 'uninstaller exits 0' { $un = Start-Process "$env:ProgramFiles\PLAY\unins000.exe" -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru; Verdict ($un.ExitCode -eq 0) "exit $($un.ExitCode)" }
T 'PLAY.exe removed' { WaitFor { -not (Test-Path $exe) } 10 }
T 'firewall rule removed' { $n = @(FirewallRules).Count; Verdict ($n -eq 0) "rules=$n" }
T 'start with Windows entry removed' { Verdict (-not (RunValue)) "run='$(RunValue)'" }
T 'media unchanged after uninstall' { (MediaState) -ceq $before }

Add-Content $log "DONE fails=$fails"
Stop-Computer -Force
