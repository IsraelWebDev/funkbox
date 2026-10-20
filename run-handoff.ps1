# Alternative to launching from chat: run the v2 build locally with Claude Code on this laptop.
#   powershell -ExecutionPolicy Bypass -File .\run-handoff.ps1            # Sonnet (default)
#   powershell -ExecutionPolicy Bypass -File .\run-handoff.ps1 -Model haiku
# Requires Node (installed) and a Claude login (the first run of `claude` will ask you to sign in).
param([ValidateSet("sonnet","haiku","opus")][string]$Model = "sonnet")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    npm install -g @anthropic-ai/claude-code
}
# Locally there is a real GPU context, so do NOT force EGL; the plan's tests fall back automatically.
$env:FUNK_GL_BACKEND = "default"
& .\.venv\Scripts\Activate.ps1
uv pip install moderngl pytest -q
claude --model $Model --permission-mode acceptEdits -p (Get-Content -Raw .\HANDOFF.md)
