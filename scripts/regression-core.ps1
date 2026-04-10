param(
    [switch]$IncludeIntegration = $true
)

$ErrorActionPreference = "Stop"

$unitArgs = @(
    "tests/unit/triggers/test_api.py",
    "tests/unit/triggers/test_db_change.py",
    "tests/unit/triggers/test_queue_trigger.py",
    "tests/unit/triggers/test_queue_trigger_properties.py",
    "tests/unit/test_mcp_server.py",
    "tests/unit/web/test_middleware.py",
    "tests/unit/web/test_overview.py",
    "tests/unit/web/test_governance.py"
)

$integrationArgs = @(
    "tests/integration/test_api_trigger_integration.py",
    "tests/integration/test_queue_trigger_e2e.py"
)

$allArgs = @($unitArgs)
if ($IncludeIntegration) {
    $allArgs += $integrationArgs
}
$allArgs += "-q"

Write-Host "Running core regression suite..." -ForegroundColor Cyan
Write-Host ("poetry run pytest " + ($allArgs -join " ")) -ForegroundColor DarkCyan

poetry run pytest @allArgs
