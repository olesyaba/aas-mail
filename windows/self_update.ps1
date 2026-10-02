# AAS mail self-update for Windows, started detached (from a temp copy) by
# windows_entry.update_install():
#   self_update.ps1 <zip url> <sha256 | -> <app folder> <expected version> <app pid>
# Same steps as app/self_update.sh: download over HTTPS, check SHA-256 and the version
# inside, stop the running app, swap the folder, relaunch; the old folder comes back
# on failure. ASCII only: Windows PowerShell 5.1 reads a BOM-less script as ANSI.
param([string]$Url, [string]$Sha, [string]$AppDir, [string]$Want, [int]$AppPid)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # the progress bar makes Invoke-WebRequest crawl

function Say($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" }
$Lock = Join-Path $env:APPDATA 'eas-bridge\update.lock'
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ("aas-mail-update-" + [guid]::NewGuid())
$Old = "$AppDir.old-update"

function Relaunch {
  Start-Process -FilePath (Join-Path $AppDir 'python\pythonw.exe') -WorkingDirectory $AppDir `
    -ArgumentList @('-X', 'utf8', ('"' + (Join-Path $AppDir 'windows\main.py') + '"'))
}
function Fail($m) {
  Say "FAIL: $m"
  if ((Test-Path $Old) -and -not (Test-Path $AppDir)) { Move-Item $Old $AppDir }
  Relaunch
  Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
  Remove-Item $Lock -ErrorAction SilentlyContinue
  exit 1
}

# One update at a time (a second click or the auto-check must not swap under this one).
try { New-Item -ItemType Directory $Lock | Out-Null } catch { Say 'another update is running - skip'; exit 0 }
try {
  if ($Url -notlike 'https://github.com/*') { Fail "unexpected download url: $Url" }
  New-Item -ItemType Directory $Tmp | Out-Null
  $Zip = Join-Path $Tmp 'update-win.zip'
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Say "download $Url"
  Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing -TimeoutSec 900
  if ($Sha -and $Sha -ne '-') {
    $got = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $Sha.ToLower()) { Fail "checksum mismatch ($got, expected $Sha)" }
    Say 'checksum ok'
  }
  Expand-Archive $Zip -DestinationPath $Tmp
  $New = Join-Path $Tmp 'AAS mail'
  $m = Select-String -Path (Join-Path $New 'webapp.py') -Pattern '^\s*"version": "([^"]*)"' | Select-Object -First 1
  $ver = if ($m) { $m.Matches[0].Groups[1].Value } else { '' }
  if ($ver -ne $Want) { Fail "archive has version '$ver', expected '$Want'" }

  Say 'quit running app and its server'
  Stop-Process -Id $AppPid -Force -ErrorAction SilentlyContinue
  Get-Process pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($AppDir + '\') } | Stop-Process -Force
  Remove-Item -Recurse -Force $Old -ErrorAction SilentlyContinue
  # WebView2 lets go of the folder a moment after its host process is gone.
  for ($i = 0; ; $i++) {
    try { Move-Item $AppDir $Old; break } catch { if ($i -ge 40) { Fail "move old app: $_" }; Start-Sleep -Milliseconds 500 }
  }
  try { Move-Item $New $AppDir } catch { Fail "copy new app: $_" }
  Say "installed $ver, relaunch"
  Relaunch
  Remove-Item -Recurse -Force $Old, $Tmp -ErrorAction SilentlyContinue
} catch {
  Fail "$_"
} finally {
  Remove-Item $Lock -ErrorAction SilentlyContinue
}
