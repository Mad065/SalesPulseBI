.PHONY: install dev test lint format demo serve clean

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev,xls]"

dev:
	$(BIN)/salespulsebi serve --reload

demo:
	$(BIN)/salespulsebi demo

serve:
	$(BIN)/salespulsebi serve

test:
	$(BIN)/pytest -v --tb=short

lint:
	$(BIN)/ruff check src tests

format:
	$(BIN)/ruff format src tests

clean:
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
