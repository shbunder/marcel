# Marcel v3 — one gate per lane. `make check` runs every lane that builds off a Mac.
PY_LANES := contracts hub mock-hub runner plugins/marcel/tools
# `make mock-hub` runs the mock hub; its gate is mock-hub-check, so it is built apart from the rest.
GATED    := $(filter-out mock-hub,$(PY_LANES))
CHANNEL  := plugins/marcel/channel

.PHONY: check test ios-check board lanes start scripts $(PY_LANES) channel mock-hub-check

check: scripts $(GATED) mock-hub-check channel

# The board CLI and the guard hook are a lane like any other: format, lint, types, covered tests.
SCRIPTS_ENV := uv run --quiet --python 3.12 --with pytest --with pytest-cov --with ruff --with pyright
scripts:
	$(SCRIPTS_ENV) ruff format --check scripts .claude/hooks
	$(SCRIPTS_ENV) ruff check scripts .claude/hooks
	$(SCRIPTS_ENV) pyright
	$(SCRIPTS_ENV) pytest scripts/tests -q --cov --cov-config=.coveragerc

$(GATED):
	$(MAKE) -C $@ check

mock-hub-check:
	$(MAKE) -C mock-hub check

# The mock hub for building the app, on 127.0.0.1:7499. Walk it with mock-hub/scripts/walk.sh.
mock-hub:
	$(MAKE) -C mock-hub run

channel:
	cd $(CHANNEL) && npm ci --silent && npm run check

test:
	@for lane in $(PY_LANES); do $(MAKE) -C $$lane test || exit 1; done
	cd $(CHANNEL) && npm ci --silent && npm test

# iOS builds need Xcode: run this on the Mac only.
ios-check:
	@command -v xcodebuild >/dev/null || { echo "ios-check needs Xcode: run it on the Mac."; exit 1; }
	cd ios && xcodebuild test -scheme Marcel -destination 'platform=iOS Simulator,name=iPhone 16'

# The board (project/). See project/README.md.
board:
	@python3 scripts/board.py list

lanes:
	@python3 scripts/board.py lanes

start:
	@test -n "$(S)" || { echo "Usage: make start S=S-03.2"; exit 1; }
	@python3 scripts/board.py start $(S)
