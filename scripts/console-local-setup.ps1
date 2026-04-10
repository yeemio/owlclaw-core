# OwlClaw Console local setup (real-browser validation helper)
# Usage examples:
#   pwsh ./scripts/console-local-setup.ps1 -SkipDbInit -Port 8000
#   pwsh ./scripts/console-local-setup.ps1 -SkipDbInit -Port 8000 -RunE2E
#   pwsh ./scripts/console-local-setup.ps1 -Port 8000 -RunE2E -KeepServer

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$SkipDbInit,
    [switch]$SkipMigrate,
    [switch]$RunE2E,
    [switch]$FrontendCiInstall,
    [switch]$KeepServer,
    [int]$HealthTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Step {
    param(
        [string]$Title,
        [scriptblock]$Action
    )
    Write-Host "==> $Title"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Title (exit=$LASTEXITCODE)"
    }
}

function Wait-Health {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 90
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($resp.StatusCode -eq 200) {
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Resolve-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Run-E2E {
    param([string]$RepoRoot)
    $frontendDir = Join-Path $RepoRoot "owlclaw/web/frontend"
    Push-Location $frontendDir
    try {
        $brokenPnpmPath = "node_modules/.pnpm/@babel+core@7.29.0/node_modules/@babel/code-frame"
        $needsReinstall = $FrontendCiInstall -or (-not (Test-Path "node_modules")) -or (-not (Test-Path $brokenPnpmPath))
        if ($needsReinstall) {
            if (-not (Test-Path "package-lock.json")) {
                throw "package-lock.json not found, cannot run npm ci"
            }
            Write-Host "Installing frontend dependencies via npm ci..."
            npm ci
            if ($LASTEXITCODE -ne 0) {
                throw "npm ci failed"
            }
        }
        Write-Host "Running browser E2E (manual-server mode)..."
        npm run test:e2e:run
        if ($LASTEXITCODE -ne 0) {
            throw "npm run test:e2e:run failed"
        }
    } finally {
        Pop-Location
    }
}

$repoRoot = Resolve-RepoRoot
Set-Location $repoRoot

if (-not $SkipDbInit) {
    if (-not $env:PG_PASSWORD) {
        throw "PG_PASSWORD is required when DB init is enabled. Example: `$env:PG_PASSWORD='your_password'."
    }
    $adminUrl = "postgresql://postgres:$env:PG_PASSWORD@127.0.0.1:5432/postgres"
    Invoke-Step "Create owlclaw database and role" { poetry run owlclaw db init --admin-url $adminUrl --skip-hatchet }
}

if (-not $SkipMigrate) {
    Invoke-Step "Run migrations" { poetry run owlclaw db migrate }
}

Invoke-Step "Check uvicorn dependency" { poetry run python -c "import uvicorn" }

$serverOutDir = Join-Path $repoRoot ".kiro/reviews/artifacts/console-local-setup"
New-Item -ItemType Directory -Force -Path $serverOutDir | Out-Null
$serverStdout = Join-Path $serverOutDir "server.stdout.log"
$serverStderr = Join-Path $serverOutDir "server.stderr.log"

Write-Host "==> Start OwlClaw Console on port $Port"
$server = Start-Process -FilePath "poetry" -ArgumentList @(
    "run",
    "python",
    "-m",
    "uvicorn",
    "owlclaw.cli.start:create_start_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    "$Port",
    "--log-level",
    "info"
) -PassThru -RedirectStandardOutput $serverStdout -RedirectStandardError $serverStderr

try {
    $healthy = Wait-Health -Url "http://127.0.0.1:$Port/healthz" -TimeoutSeconds $HealthTimeoutSeconds
    if (-not $healthy) {
        throw "Console health check failed: http://127.0.0.1:$Port/healthz (logs: $serverStdout / $serverStderr)"
    }
    Write-Host "Health check passed: http://127.0.0.1:$Port/healthz"
    Write-Host "Console URL: http://127.0.0.1:$Port/console/"

    if ($RunE2E) {
        Run-E2E -RepoRoot $repoRoot
    }

    if ($KeepServer) {
        Write-Host "KeepServer enabled. Process id: $($server.Id)"
        Write-Host "Logs: $serverStdout / $serverStderr"
    }
} finally {
    if (-not $KeepServer) {
        if ($server -and -not $server.HasExited) {
            Stop-Process -Id $server.Id -Force
        }
    }
}
