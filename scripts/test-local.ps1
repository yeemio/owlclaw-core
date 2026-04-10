Param(
    [switch]$unit_only,
    [switch]$keep_up
)

$ErrorActionPreference = "Stop"

function Stop-TestStack {
    if (-not $keep_up) {
        docker compose -f docker-compose.test.yml down | Out-Host
    }
}

try {
    docker compose -f docker-compose.test.yml up -d | Out-Host

    if ($unit_only) {
        poetry run pytest tests/unit/ -q | Out-Host
    }
    else {
        poetry run pytest tests/unit/ tests/integration/ -q | Out-Host
    }
}
finally {
    Stop-TestStack
}
