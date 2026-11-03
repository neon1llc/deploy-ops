# Makefile - Keep local and CI checks aligned.

.PHONY: install check test demo

install:
	python -m pip install -r requirements-dev.txt -e .

check:
	python scripts/check.py
	python -m pytest

test:
	python -m pytest

demo:
	python -m deploy_ops demo "$$(mktemp -d)/workspace"
