.PHONY: check fmt test test-live sync

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

test-live: sync
	uv run pytest -m live
