PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
REQS := requirements-dev.txt app/requirements.txt
STAMP := $(VENV)/.requirements-dev.stamp

.PHONY: install-dev compile pytest test

$(PY):
	$(PYTHON) -m venv $(VENV)

$(STAMP): $(PY) $(REQS)
	$(PIP) install -r requirements-dev.txt
	touch $(STAMP)

install-dev: $(STAMP)

compile: install-dev
	$(PY) -m compileall app tests

pytest: install-dev
	$(PYTEST) -q

test: compile pytest
