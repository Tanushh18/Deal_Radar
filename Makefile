.PHONY: help setup dev android start test e2e ping health stats status sync clean clean-data freeze

VENV   := .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
UVI    := $(VENV)/bin/uvicorn
PORT   ?= 8000

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	 awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:           ## Create the virtualenv, install deps, create .env
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@test -f .env || (cp .env.example .env && echo "\n→ Created .env — add TELEGRAM_API_ID, TELEGRAM_API_HASH and SECRET_KEY\n")
	@echo "Setup complete. Next: make dev"

dev:             ## Run with auto-reload (development)
	$(UVI) app.main:app --reload --port $(PORT)

android:         ## Run for the Android app — binds all interfaces, prints the address to enter
	@ip=$$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "your-lan-ip"); \
	 echo ""; \
	 echo "  Emulator        ->  http://10.0.2.2:$(PORT)"; \
	 echo "  Physical device ->  http://$$ip:$(PORT)   (phone on the same Wi-Fi)"; \
	 echo ""
	$(UVI) app.main:app --host 0.0.0.0 --port $(PORT) --reload

start:           ## Run without reload (production-style)
	$(UVI) app.main:app --host 0.0.0.0 --port $(PORT) --workers 1

test:            ## Run the backend test suites (no Telegram needed)
	$(PY) -m tests.test_pipeline
	$(PY) -m tests.test_search
	$(PY) -m tests.test_notifications
	$(PY) -m tests.test_reparse
	$(PY) -m tests.test_devices

e2e:             ## Click through the website in Chrome on screen (E2E_HEADLESS=1 to hide it)
	$(PIP) install -q -r requirements-dev.txt
	$(PY) -m pytest tests/e2e -v

ping:            ## Hit the ping endpoint on a running server
	@curl -s http://localhost:$(PORT)/api/ping && echo

health:          ## Deep health check on a running server
	@curl -s http://localhost:$(PORT)/api/health && echo

stats:           ## Deal + ingest stats from a running server
	@curl -s http://localhost:$(PORT)/api/stats && echo

status:          ## Is it actually fetching deals? Full pipeline diagnostic
	@$(PY) tools/status.py

sync:            ## Force an ingest cycle now (needs ADMIN_TOKEN in .env)
	@curl -s -X POST http://localhost:$(PORT)/api/admin/ingest \
	  -H "X-Admin-Token: $$(grep -E '^ADMIN_TOKEN=' .env | cut -d= -f2-)" && echo

secret:          ## Generate a SECRET_KEY
	@$(PY) -c "import secrets; print(secrets.token_urlsafe(48))"

clean:           ## Remove __pycache__ only — never touches data/ (see clean-data)
	rm -rf __pycache__ */__pycache__ */*/__pycache__ .pytest_cache
	@echo "Cleaned pycache. data/ was left alone — see 'make clean-data' if you really want that gone."

clean-data:      ## Danger: delete the local deals cache. Confirms first; Sheets (if configured) is unaffected.
	@if [ -f data/deals.db ]; then \
	  read -p "This deletes data/deals.db (local cache only — Sheets is untouched if configured). Continue? [y/N] " ok; \
	  if [ "$$ok" = "y" ] || [ "$$ok" = "Y" ]; then rm -rf data && echo "Removed data/."; else echo "Cancelled."; fi; \
	else \
	  echo "No data/deals.db present — nothing to do."; \
	fi
