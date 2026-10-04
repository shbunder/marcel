.PHONY: check fmt test sync run

sync:
	uv sync --quiet

check: sync
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov --cov-report=term-missing

fmt: sync
	uv run ruff format .
	uv run ruff check --fix .

test: sync
	uv run pytest

# The mock hub on 127.0.0.1:7499. Pass MOCK_DELAY=0 for an instant scenario.
run: sync
	uv run python -m marcel_mock_hub
