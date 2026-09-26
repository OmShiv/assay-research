.PHONY: test check experiments paper arxiv index clean

# prefer the project venv when it exists, so make works whether or not it is activated
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
TECTONIC ?= tectonic
ASSAY_CORPORA ?= $(CURDIR)/../corpora

test:
	$(PY) -m pytest -q

index:
	$(PY) -m assay index --quiet

check:
	$(PY) scripts/check_dashes.py
	$(PY) -m assay index --quiet && git diff --exit-code --stat -- assay.json
	$(PY) -m pytest -q

experiments:
	ASSAY_CORPORA=$(ASSAY_CORPORA) $(PY) experiments/run_all.py

paper:
	$(PY) paper/make_numbers.py
	cd paper && $(TECTONIC) assay.tex
	$(PY) scripts/check_dashes.py

# self-contained source bundle for arXiv (TeX source, generated numbers and tables, .bbl)
arxiv: paper
	cd paper && $(TECTONIC) --keep-intermediates assay.tex >/dev/null
	rm -rf dist/arxiv && mkdir -p dist/arxiv/tables
	cp paper/assay.tex paper/numbers.tex paper/refs.bib paper/assay.bbl dist/arxiv/
	cp paper/tables/*.tex paper/tables/*.dat paper/tables/brief_fastapi.txt dist/arxiv/tables/
	cd dist/arxiv && $(TECTONIC) assay.tex >/dev/null && rm -f assay.pdf && zip -qr ../assay-arxiv.zip . && echo "wrote dist/assay-arxiv.zip"

clean:
	rm -rf .pytest_cache src/*.egg-info paper/*.aux paper/*.log paper/*.bbl paper/*.blg paper/*.xdv
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
