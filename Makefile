PY := .venv/bin/python
PIP := .venv/bin/pip
SCRAPLING := .venv/bin/scrapling

.PHONY: setup test dry-run dry-run-ig pilot-tiktok scale-tiktok login-ig check-ig pilot-ig scale-ig parse merge validate all clean

setup:
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt
	$(SCRAPLING) install

test:
	$(PY) -m unittest discover -s tests -v

dry-run:
	$(PY) -m src.scrape_tiktok --dry-run

dry-run-ig:
	$(PY) -m src.scrape_instagram --dry-run

pilot-tiktok:
	$(PY) -m src.scrape_tiktok --pilot --headful

scale-tiktok:
	$(PY) -m src.scrape_tiktok

expand-seeds:
	$(PY) -m src.expand_seeds --top 30

login-ig:
	$(PY) -m src.scrape_instagram --login

check-ig:
	$(PY) -m src.scrape_instagram --check

pilot-ig:
	$(PY) -m src.scrape_instagram --pilot --headful

enrich-ig:
	$(PY) -m src.enrich_instagram

scale-ig:
	$(PY) -m src.scrape_instagram

parse:
	$(PY) -m src.parse --platform all

merge:
	$(PY) -m src.merge

validate:
	$(PY) -m src.validate

all: parse merge validate
