param()

$ErrorActionPreference = "Stop"

$argsList = @(
    "tests/unit/triggers/test_api.py",
    "tests/unit/triggers/test_queue_trigger_properties.py",
    "tests/unit/triggers/test_queue_kafka_adapter.py",
    "tests/unit/triggers/test_queue_log_security.py",
    "tests/unit/triggers/test_db_change.py",
    "-q"
)

Write-Host "Running failure-path regression suite..." -ForegroundColor Yellow
Write-Host ("poetry run pytest " + ($argsList -join " ")) -ForegroundColor DarkYellow

poetry run pytest @argsList
