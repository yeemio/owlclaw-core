param(
    [int]$Interval = 15,
    [switch]$EnsureRunning = $true,
    [switch]$NoNewWindow = $false
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$ensureFlag = if ($EnsureRunning) { "--ensure-running" } else { "--no-ensure-running" }
$command = "Set-Location '$repoRoot'; poetry run python scripts/workflow_supervisor.py --repo-root '$repoRoot' --interval $Interval watch $ensureFlag"

if ($NoNewWindow) {
    pwsh -NoExit -Command $command
    exit $LASTEXITCODE
}

Start-Process pwsh -ArgumentList "-NoExit", "-Command", $command -WorkingDirectory $repoRoot
