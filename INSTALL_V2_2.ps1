$ErrorActionPreference='Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $root 'web\app.py'
if (!(Test-Path $app) -or !(Test-Path (Join-Path $root 'web\waha_bridge.py'))) { throw 'Extract the ZIP contents directly into the existing Yojana-Pilot project folder, then run this script there.' }
$backup = "$app.v22.bak"
if (!(Test-Path $backup)) { Copy-Item $app $backup }
$txt=[System.IO.File]::ReadAllText($app)
$imp='from .waha_bridge import router as waha_router'
if (!$txt.Contains($imp)) {
  $anchor='from .conversation_api import router as conversation_router'
  if (!$txt.Contains($anchor)) { throw 'Could not locate conversation router import: your app.py differs; no changes made.' }
  $txt=$txt.Replace($anchor, "$anchor`n$imp")
}
if (!$txt.Contains('app.include_router(waha_router)')) {
  $anchor='    app.include_router(conversation_router)'
  if (!$txt.Contains($anchor)) { throw 'Could not locate conversation router registration: no changes made.' }
  $txt=$txt.Replace($anchor, "$anchor`n    app.include_router(waha_router)")
}
[System.IO.File]::WriteAllText($app,$txt,(New-Object System.Text.UTF8Encoding($false)))
Write-Host 'WAHA router registration complete. Homepage routing is defined in web/app.py. Run: .\.venv\Scripts\python.exe -m pytest -q'
