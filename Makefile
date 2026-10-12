PYTHON ?= python3.12
RELEASE_REMOTE ?= origin
export VERSION RELEASE_REMOTE

.PHONY: help release-participant release-participant-dry-run
help:
	@echo 'make release-participant VERSION=0.2.0  Cut a participant release and publish to PyPI'
	@echo 'make release-participant-dry-run VERSION=0.2.0  Preview without changing Git or PyPI'

release-participant:
	$(PYTHON) scripts/release.py

release-participant-dry-run:
	$(PYTHON) scripts/release.py --dry-run
