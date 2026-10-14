# Marcel on the NUC. Run as the owner from this folder, or with `make -C deploy <target>`.
# docs/operating.md explains each target and what to do when one fails.
SHELL := /bin/bash
MARCEL_HOME ?= $(HOME)/marcel
export MARCEL_HOME
TUNNEL ?= marcel

.PHONY: install deploy logs health status tunnel tunnel-rules check backup

# Everything, from nothing or over an earlier install: node, plugin, brain, units, hub.
install:
	./install.sh

# Rebuild and restart the hub container from this checkout. The runner and sessions keep running.
deploy:
	./compose.sh up -d --build
	./health.sh

# Follow the hub's and the runner's logs (Ctrl-C to stop). `make logs N=500` for more history.
N ?= 100
logs:
	@trap 'kill 0' INT; \
	./compose.sh logs -f --tail=$(N) hub & \
	journalctl --user -f -n $(N) -u marcel-runner -u marcel-claude-daemon & \
	wait

# Is everything up? Exit codes 0 ok, 1 down, 2 only a claude version drift (docs/operating.md).
health:
	@./health.sh

status: health
	@./compose.sh ps

# Write ~/.cloudflared/marcel.yml (only /api/* forwarded) and print the sudo steps for its unit.
tunnel:
	@set -e; mkdir -p $(MARCEL_HOME); id=$$(cloudflared tunnel list 2>/dev/null | awk '$$2 == "$(TUNNEL)" {print $$1}'); \
	test -n "$$id" || { echo "No Cloudflare tunnel named $(TUNNEL)."; exit 1; }; \
	sed -e "s|@TUNNEL_ID@|$$id|g" -e "s|@HOME@|$(HOME)|g" cloudflared/marcel.yml > $(HOME)/.cloudflared/marcel.yml; \
	cloudflared tunnel --config $(HOME)/.cloudflared/marcel.yml ingress validate; \
	$(MAKE) --no-print-directory tunnel-rules; \
	sed -e "s|@USER@|$(USER)|g" -e "s|@HOME@|$(HOME)|g" cloudflared/cloudflared-marcel.service > $(MARCEL_HOME)/cloudflared-marcel.service; \
	echo "Wrote $(HOME)/.cloudflared/marcel.yml. To use it, run once:"; \
	echo "  sudo install -m 644 $(MARCEL_HOME)/cloudflared-marcel.service /etc/systemd/system/cloudflared-marcel.service"; \
	echo "  sudo systemctl daemon-reload && sudo systemctl restart cloudflared-marcel"

# Back up the database and the memory repo now (marcel-backup.timer does it nightly).
backup:
	./backup.sh

# Only /api/* reaches the hub; the channel and everything else get a 404.
tunnel-rules:
	@set -e; cfg=$(HOME)/.cloudflared/marcel.yml; \
	rule() { cloudflared tunnel --config $$cfg ingress rule "https://marcel-bot.com$$1" 2>/dev/null | grep -q "$$2" \
	  || { echo "Tunnel rule wrong: $$1 should go to $$2."; exit 1; }; }; \
	rule /api/health 127.0.0.1:7420; rule /api/ws 127.0.0.1:7420; \
	rule /channel http_status:404; rule /brain/x http_status:404; rule /apix http_status:404; rule /api http_status:404; \
	echo "Tunnel rules: only /api/* reaches the hub."

# The deploy lane's own gate: the scripts parse, the compose file and the units are valid.
check:
	bash -n install.sh install-claude.sh compose.sh health.sh backup.sh tests/test_health.sh
	bash tests/test_health.sh
	python3 -c 'import json; json.load(open("managed-settings.json"))'
	MARCEL_UID=1000 MARCEL_GID=1000 MARCEL_HOME=/tmp/marcel docker compose -f docker-compose.yml config -q
	systemd-analyze --user verify systemd/*.service systemd/*.path systemd/*.timer 2>&1 | grep -v 'Command .* is not executable' | { ! grep . ; }
