.PHONY: check fmt test test-live sync

sync:
	uv sync --quiet

# ../scripts: the plugin's standalone scripts (/marcel-adopt), tested here (tests/test_adopt_script.py).
check: sync
	uv run ruff format --check . ../scripts
	uv run ruff check . ../scripts
	uv run pyright
	uv run pytest --cov --cov-report=term-missing

fmt: sync
	uv run ruff format . ../scripts
	uv run ruff check --fix . ../scripts

test: sync
	uv run pytest

test-live: sync
	uv run pytest -m live
