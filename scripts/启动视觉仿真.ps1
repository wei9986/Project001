# 在 Windows 开发机上启动视觉仿真（无硬件）
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path (Split-Path -Parent $Project) ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}
Set-Location -LiteralPath $Project
& $Python -m tools.启动检查 --profile simulation --json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m app.视觉应用 --mode simulation --task "config\仿真任务.json" --log "logs\application_simulation.jsonl"
exit $LASTEXITCODE
