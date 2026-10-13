.PHONY: check fmt test test-live sync

sync:
	uv sync --quiet

# ../scripts and the skills' scripts: the plugin's standalone scripts, tested here
# (tests/test_adopt_script.py, tests/test_kickoff_missing.py, ...).
SCRIPTS = ../scripts ../skills/project-kickoff/scripts

check: sync
	uv run ruff format --check . $(SCRIPTS)
	uv run ruff check . $(SCRIPTS)
	uv run pyright
	uv run pytest --cov --cov-report=term-missing

fmt: sync
	uv run ruff format . $(SCRIPTS)
	uv run ruff check --fix . $(SCRIPTS)

test: sync
	uv run pytest

test-live: sync
	uv run pytest -m live
