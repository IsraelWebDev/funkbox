# funkbox one-shot setup for Windows (no GPU, no accounts, no admin rights).
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1             # venv + deps + smoke render
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Register   # ...and add "funk" to Claude Desktop
param([switch]$Register, [string]$OutDir = "$PSScriptRoot\out")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { py -3 -m venv .venv }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip -q
& $py -m pip install -r requirements.txt -q

Write-Host "`nSmoke test: 2s loop..." -ForegroundColor Cyan
& $py funk.py render --preset liquid-disco --seconds 2 --size 360 --out $OutDir --name smoke-test

if ($Register) {
    $cfgPath = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
    $cfg = if (Test-Path $cfgPath) {
        Copy-Item $cfgPath "$cfgPath.bak-$(Get-Date -Format yyyyMMddHHmmss)"
        Get-Content $cfgPath -Raw | ConvertFrom-Json
    } else { [pscustomobject]@{} }
    if (-not $cfg.PSObject.Properties["mcpServers"]) {
        $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{})
    }
    $entry = [pscustomobject]@{
        command = $py
        args    = @((Join-Path $PSScriptRoot "funk_mcp.py"))
        env     = [pscustomobject]@{ FUNK_OUT = $OutDir }
    }
    $cfg.mcpServers | Add-Member -NotePropertyName funk -NotePropertyValue $entry -Force
    New-Item -ItemType Directory -Force (Split-Path $cfgPath) | Out-Null
    $cfg | ConvertTo-Json -Depth 20 | Set-Content $cfgPath -Encoding utf8
    Write-Host "Registered 'funk' in $cfgPath (backup saved). Restart Claude Desktop." -ForegroundColor Green
}

Write-Host "`nDone. Outputs in $OutDir" -ForegroundColor Green
