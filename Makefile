# Marcel v3 — one gate per lane. `make check` runs every lane that builds off a Mac.
PY_LANES := hub runner plugins/marcel/tools
CHANNEL  := plugins/marcel/channel

.PHONY: check test ios-check $(PY_LANES) channel

check: $(PY_LANES) channel

$(PY_LANES):
	$(MAKE) -C $@ check

channel:
	cd $(CHANNEL) && npm ci --silent && npm run check

test:
	@for lane in $(PY_LANES); do $(MAKE) -C $$lane test || exit 1; done
	cd $(CHANNEL) && npm ci --silent && npm test

# iOS builds need Xcode: run this on the Mac only.
ios-check:
	@command -v xcodebuild >/dev/null || { echo "ios-check needs Xcode: run it on the Mac."; exit 1; }
	cd ios && xcodebuild test -scheme Marcel -destination 'platform=iOS Simulator,name=iPhone 16'
