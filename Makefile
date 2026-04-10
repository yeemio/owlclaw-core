.PHONY: help dev-up dev-down dev-reset test-up test-down test test-unit test-int lint typecheck build-console workflow-status workflow-orchestrate workflow-mailbox workflow-agent workflow-supervisor workflow-supervisor-console workflow-launch

## Show available commands
help:
	@echo "OwlClaw development commands:"
	@echo "  dev-up       Start full local stack (docker-compose.dev.yml)"
	@echo "  dev-down     Stop full local stack"
	@echo "  dev-reset    Stop full stack and remove volumes"
	@echo "  test-up      Start test database stack (docker-compose.test.yml)"
	@echo "  test-down    Stop test database stack"
	@echo "  test         Run unit + integration tests"
	@echo "  test-unit    Run unit tests only"
	@echo "  test-int     Run integration tests only"
	@echo "  lint         Run Ruff checks"
	@echo "  typecheck    Run MyPy checks"
	@echo "  build-console Build frontend assets into owlclaw/web/static"
	@echo "  workflow-status Inspect multi-worktree workflow state"
	@echo "  workflow-orchestrate Run continuous workflow orchestrator loop"
	@echo "  workflow-mailbox Pull mailbox or write ack status"
	@echo "  workflow-agent Run semi-automatic mailbox consumer"
	@echo "  workflow-supervisor Start/stop/status for workflow automation processes"
	@echo "  workflow-supervisor-console Open a visible watch terminal for the supervisor"
	@echo "  workflow-launch One-click launch 6 workflow windows plus control window"
	@echo ""
	@echo "Windows: use PowerShell scripts under scripts/ when make is unavailable."

## Start full local stack
dev-up:
	docker compose -f docker-compose.dev.yml up -d

## Stop full local stack
dev-down:
	docker compose -f docker-compose.dev.yml down

## Reset full local stack and volumes
dev-reset:
	docker compose -f docker-compose.dev.yml down -v

## Start test stack
test-up:
	docker compose -f docker-compose.test.yml up -d

## Stop test stack
test-down:
	docker compose -f docker-compose.test.yml down

## Run unit and integration tests
test:
	poetry run pytest tests/unit/ tests/integration/ -q

## Run unit tests
test-unit:
	poetry run pytest tests/unit/ -q

## Run integration tests
test-int:
	poetry run pytest tests/integration/ -q

## Run lint
lint:
	poetry run ruff check .

## Run static type check
typecheck:
	poetry run mypy owlclaw/

## Build console frontend assets
build-console:
	cd owlclaw/web/frontend && npm install && npm run build

## Inspect multi-worktree workflow state
workflow-status:
	poetry run python scripts/workflow_status.py

## Run continuous workflow orchestrator loop
workflow-orchestrate:
	poetry run python scripts/workflow_orchestrator.py

## Pull mailbox or write ack state for an agent
workflow-mailbox:
	poetry run python scripts/workflow_mailbox.py --help

## Run the semi-automatic mailbox consumer
workflow-agent:
	poetry run python scripts/workflow_agent.py --help

## Start/stop/status the workflow automation supervisor
workflow-supervisor:
	poetry run python scripts/workflow_supervisor.py --help

## Open a visible supervisor watch console (Windows PowerShell)
workflow-supervisor-console:
	pwsh ./scripts/workflow-supervisor-console.ps1

workflow-launch:
	pwsh ./scripts/workflow-launch.ps1
