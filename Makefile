# ==== REFACTORED BY SBU ===
SHELL := /bin/bash
.DEFAULT_GOAL := help

# INFO / WARNING prefix for echo's
INFO := \033[1;32m[   INFO]\033[0m
WARNING := \033[0;93m[WARNING]\033[0m

include .env
-include .env.local

# Export all variables to subprocesses
.EXPORT_ALL_VARIABLES:

# ENVIRONMENT SETUP
.PHONY: .uv
.uv: ## Check that uv is installed
	@uv --version || echo -e "$(WARNING) Please install uv: https://docs.astral.sh/uv/getting-started/installation/"

# Zoo park deps live OUTSIDE uv.lock (thin per-park dep-venvs provisioned by
# scripts/zoo-setup.sh), so a bare `uv sync` leaves them stale or missing.
# Both env targets therefore re-provision them — always use these targets,
# never bare `uv sync`. The ~/.marcel/zoo default is computed INSIDE the
# zoo-deps recipe: a top-level `MARCEL_ZOO_DIR ?=` would leak to every make
# child via .EXPORT_ALL_VARIABLES and un-hermetically point tests at the
# deployed zoo.

.PHONY: env-install
env-install: .uv ## Install the package, dependencies, and pre-commit for local development (zoo park deps included)
	echo -e "$(INFO) Installing packages and depencies..."
	uv sync --frozen --all-extras --all-packages --group dev --group lint --group docs
	@$(MAKE) --no-print-directory zoo-deps

.PHONY: env-sync
env-sync: .uv ## Update local packages and uv.lock (zoo park deps included)
	echo -e "$(INFO) Updating packages and uv.lock..."
	uv sync --all-extras --all-packages --group lint --group docs
	@$(MAKE) --no-print-directory zoo-deps

.PHONY: zoo-deps
zoo-deps: ## Provision zoo park dep-venvs at $$MARCEL_ZOO_DIR (no-op when no zoo checkout exists)
	@ZOO_DIR="$${MARCEL_ZOO_DIR:-$$HOME/.marcel/zoo}"; \
	if [ -d "$$ZOO_DIR" ] && [ -n "$$(ls -A "$$ZOO_DIR" 2>/dev/null)" ]; then \
		MARCEL_ZOO_DIR="$$ZOO_DIR" ./scripts/zoo-setup.sh --deps-only; \
	else \
		echo -e "$(WARNING) No zoo checkout at $$ZOO_DIR — skipped park deps (run 'make zoo-setup' when you need one)"; \
	fi

# DOCUMENTATION
# `--no-strict` so you can build the docs without insiders packages
.PHONY: docs-build
docs-build: ## Build the documentation
	echo -e "$(INFO) Building documentation..."
	uv run mkdocs build --no-strict

# `--no-strict` so you can build the docs without insiders packages
.PHONY: docs-serve
docs-serve: ## Build and serve the documentation
	echo -e "$(INFO) Serving documentation..."
	uv run mkdocs serve --no-strict

# TESTING
.PHONY: test
test: ## Run all tests (Python + Rust)
	echo -e "$(INFO) Running Python tests..."
	uv run pytest tests/ -x -v
	echo -e "$(INFO) Running Rust CLI tests..."
	cd src/marcel_cli && cargo test

.PHONY: test-core
test-core: ## Run core package tests
	echo -e "$(INFO) Running core-tests..."
	uv run pytest tests/core/ -x -v

.PHONY: test-cov
test-cov: ## Run tests with coverage report (fails below 95%; marcel_testing must be 100%)
	echo -e "$(INFO) Running all tests with coverage..."
	uv run pytest tests/ --cov=src/marcel_core --cov=src/marcel_sdk --cov=src/marcel_testing --cov-report=term-missing --cov-fail-under=95
	echo -e "$(INFO) Checking marcel_testing (the test harness itself) is fully covered..."
	uv run coverage report --include='src/marcel_testing/*' --fail-under=100 > /dev/null || \
		(echo -e "$(WARNING) marcel_testing must stay at 100% coverage — the harness cannot be the untested part" && \
		uv run coverage report --include='src/marcel_testing/*' && exit 1)

.PHONY: install-cli
install-cli: ## Install the Marcel CLI binary (Rust) to ~/.cargo/bin
	echo -e "$(INFO) Building and installing Marcel CLI..."
	cd src/marcel_cli && cargo install --path .

.PHONY: cli
cli: cli-build ## Start the Marcel CLI (Rust TUI)
	./src/marcel_cli/target/release/marcel

.PHONY: cli-build
cli-build: ## Build the Marcel CLI (Rust, release mode)
	echo -e "$(INFO) Building Marcel CLI..."
	cd src/marcel_cli && cargo build --release

.PHONY: cli-dev
cli-dev: ## Build and run the Marcel CLI (Rust, debug mode)
	cd src/marcel_cli && cargo run

# WEB FRONTEND
.PHONY: web-install
web-install: ## Install web frontend dependencies
	echo -e "$(INFO) Installing web dependencies..."
	cd src/web && npm ci

.PHONY: web-build
web-build: ## Build web frontend for production
	echo -e "$(INFO) Building web frontend..."
	cd src/web && npm run build

.PHONY: web-dev
web-dev: ## Start web frontend dev server (Vite on :5173)
	cd src/web && npm run dev

# Dev server port — defaults to 7421 to avoid conflicting with Docker prod (7420)
MARCEL_DEV_PORT ?= 7421

.PHONY: serve
serve: ## Start marcel-core dev container (Docker, uvicorn --reload on $(MARCEL_DEV_PORT))
	echo -e "$(INFO) Starting marcel-dev container on http://0.0.0.0:$(MARCEL_DEV_PORT) ..."
	docker compose -f docker-compose.dev.yml up -d --build
	echo -e "$(INFO) Follow logs: make serve-logs  |  stop: make serve-down"

.PHONY: serve-logs
serve-logs: ## Tail marcel-dev container logs
	docker compose -f docker-compose.dev.yml logs -f marcel-dev

.PHONY: serve-down
serve-down: ## Stop the marcel-dev container
	docker compose -f docker-compose.dev.yml down

.PHONY: test-v2
test-v2: ## Test v2 endpoint with a message (usage: make test-v2 MSG="your message")
	@if [ -z "$(MSG)" ]; then \
		echo -e "$(WARNING) Usage: make test-v2 MSG=\"your message here\""; \
		echo "Examples:"; \
		echo "  make test-v2 MSG=\"Hello Marcel!\""; \
		echo "  make test-v2 MSG=\"List files in current directory\""; \
		echo "  make test-v2 MSG=\"Read README.md\""; \
		exit 1; \
	fi
	@./test_v2.sh "$(MSG)"

# Deployment
.PHONY: setup
setup: ## Full setup: systemd units + Docker build + start (one command to rule them all)
	@./scripts/setup.sh

.PHONY: setup-check
setup-check: ## Dry-run: verify prerequisites for setup without starting anything
	@./scripts/setup.sh --check

.PHONY: teardown
teardown: ## Stop Marcel and remove systemd units
	@./scripts/teardown.sh

# Zoo (habitats repo — marcel-zoo)
.PHONY: zoo-setup
zoo-setup: ## Clone marcel-zoo into $MARCEL_ZOO_DIR (default ~/.marcel/zoo) and install its deps into the kernel venv
	@./scripts/zoo-setup.sh

.PHONY: zoo-sync
zoo-sync: ## Git-pull the zoo and refresh its deps in the kernel venv
	@./scripts/zoo-setup.sh --sync

.PHONY: zoo-docker-deps
zoo-docker-deps: ## Install zoo deps into the running prod container (docker exec zoo-setup --deps-only)
	@if ! docker compose ps --status running --quiet marcel 2>/dev/null | grep -q .; then \
		echo -e "$(WARNING) Prod container 'marcel' is not running — start it first with: make docker-up"; \
		exit 1; \
	fi
	@echo -e "$(INFO) Installing zoo deps into the prod container..."
	@docker exec marcel bash /app/scripts/zoo-setup.sh --deps-only

.PHONY: zoo-docker-sync
zoo-docker-sync: zoo-sync zoo-docker-deps ## Pull the zoo (host) and refresh deps in both the kernel venv and the prod container

# Onboarding
DATA_DIR ?= $(HOME)/.marcel

.PHONY: add-user
add-user: ## Onboard a user — profile, role, memory dir (usage: make add-user USER=alice [ROLE=admin])
	@if [ -z "$(USER)" ]; then \
		echo -e "$(WARNING) Usage: make add-user USER=<slug> [ROLE=admin|user]"; \
		exit 1; \
	fi
	@uv run python -m marcel_core.ops add-user --user "$(USER)" --role "$(or $(ROLE),user)"

.PHONY: remove-user
remove-user: ## Offboard a user by archiving (never deleting) (usage: make remove-user USER=alice)
	@if [ -z "$(USER)" ]; then \
		echo -e "$(WARNING) Usage: make remove-user USER=<slug>"; \
		exit 1; \
	fi
	@uv run python -m marcel_core.ops remove-user --user "$(USER)"

.PHONY: telegram-setup
telegram-setup: ## Register + verify the Telegram webhook via the Bot API (usage: make telegram-setup URL=https://your.public.url)
	@if [ -z "$(URL)" ]; then \
		echo -e "$(WARNING) Usage: make telegram-setup URL=<public https base URL>"; \
		exit 1; \
	fi
	@uv run python -m marcel_core.ops telegram-setup --url "$(URL)"

.PHONY: doctor
doctor: ## Report install/runtime health — server, webhook, zoo, users (exit 0 when healthy)
	@uv run python -m marcel_core.ops doctor

.PHONY: link-telegram
link-telegram: ## Link a Marcel user to a Telegram chat ID (usage: make link-telegram USER=alice CHAT=123456789)
	@if [ -z "$(USER)" ] || [ -z "$(CHAT)" ]; then \
		echo -e "$(WARNING) Usage: make link-telegram USER=<slug> CHAT=<telegram_chat_id>"; \
		exit 1; \
	fi
	@uv run python -c "from marcel_core.plugin.channels import discover; discover(); import _marcel_ext_channels.telegram.sessions as s; s.link_user('$(USER)', '$(CHAT)')"
	@echo -e "$(INFO) Linked user '$(USER)' to Telegram chat $(CHAT)"

# Docker targets
.PHONY: docker-build
docker-build: ## Build the Marcel Docker image
	echo -e "$(INFO) Building Marcel Docker image..."
	docker compose build

.PHONY: docker-up
docker-up: ## Start Marcel in Docker (production)
	echo -e "$(INFO) Starting Marcel (Docker) on port 7420..."
	docker compose up -d

.PHONY: docker-down
docker-down: ## Stop Marcel Docker container
	docker compose down

.PHONY: docker-logs
docker-logs: ## Tail Marcel Docker logs
	docker compose logs -f marcel

.PHONY: docker-restart
docker-restart: ## Rebuild and restart Marcel Docker container
	echo -e "$(INFO) Redeploying Marcel..."
	./scripts/redeploy.sh

.PHONY: check
check: format lint typecheck test-cov ## Run format, lint, typecheck, and tests

# CODE QUALITY
.PHONY: format
format: ## Format the code (Python + Rust)
	echo -e "$(INFO) Formatting Python code..."
	uv run ruff format
	uv run ruff check --fix --fix-only
	echo -e "$(INFO) Formatting Rust code..."
	cd src/marcel_cli && cargo fmt

.PHONY: lint
lint: ## Lint the code (Python + Rust)
	echo -e "$(INFO) Linting Python code..."
	uv run ruff format --check
	uv run ruff check
	echo -e "$(INFO) Linting Rust code..."
	cd src/marcel_cli && cargo clippy -- -D warnings

.PHONY: typecheck-pyright
typecheck-pyright:
	echo -e "$(INFO) Typechecking code with pyright..."
	@# To typecheck for a specific version of python, run 'make install-all-python' then set environment variable PYRIGHT_PYTHON=3.10 or similar
	@# PYRIGHT_PYTHON_IGNORE_WARNINGS avoids the overhead of making a request to github on every invocation
	PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright $(if $(PYRIGHT_PYTHON),--pythonversion $(PYRIGHT_PYTHON))

.PHONY: typecheck
typecheck: typecheck-pyright ## Run static type checking

.PHONY: help
help: ## Show this help
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		sed 's/^.*Makefile://g' | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "For detailed commands, check the Makefile or run: make -n <target>"

# --- Habitat marketplace (FEAT-260718-210a5f) --------------------------------
.PHONY: marketplace-sources marketplace-browse marketplace-install marketplace-update marketplace-remove

marketplace-sources:  ## List trusted habitat sources
	uv run python -m marcel_core.marketplace.cli sources

marketplace-browse:  ## Browse a source: make marketplace-browse SOURCE=<name>
	uv run python -m marcel_core.marketplace.cli browse --source $(SOURCE)

marketplace-install:  ## Install (reviewed): make marketplace-install SOURCE=<name> NAME=<habitat>
	uv run python -m marcel_core.marketplace.cli install --source $(SOURCE) --name $(NAME)

marketplace-update:  ## Update (reviewed): make marketplace-update KIND=skill|connector NAME=<habitat>
	uv run python -m marcel_core.marketplace.cli update --kind $(KIND) --name $(NAME)

marketplace-remove:  ## Remove: make marketplace-remove KIND=skill|connector NAME=<habitat>
	uv run python -m marcel_core.marketplace.cli remove --kind $(KIND) --name $(NAME)

.PHONY: enable-habitat disable-habitat

enable-habitat:  ## Enable for a user: make enable-habitat KIND=skill|connector NAME=<habitat> USER=<slug>
	uv run python -m marcel_core.marketplace.cli enable --kind $(KIND) --name $(NAME) --user $(USER)

disable-habitat:  ## Disable for a user: make disable-habitat KIND=skill|connector NAME=<habitat> USER=<slug>
	uv run python -m marcel_core.marketplace.cli disable --kind $(KIND) --name $(NAME) --user $(USER)

