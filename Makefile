PYTHON ?= .venv/bin/python
CDK ?= npx --yes aws-cdk@2

.PHONY: install lock layer lint test audit synth deploy diff clean

install:            ## Create the virtualenv and install everything
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt
	$(PYTHON) -m pip install --require-hashes --no-deps --force-reinstall -r layer/requirements.txt  # exactly what ships
	$(PYTHON) -m pip check
	npm ci --ignore-scripts --no-audit --no-fund  # ESLint for the dashboard (dev only)

lock:               ## Regenerate layer/requirements.txt (exact versions + hashes) from layer/requirements.in
	# Run from layer/, so the header's file paths match Dependabot's /layer directory.
	cd layer && ../$(PYTHON) -m piptools compile --quiet --generate-hashes --allow-unsafe --strip-extras \
		--no-emit-index-url --output-file=requirements.txt requirements.in
	# pip-tools 7.6 with pip 26 writes a spurious --no-index into the header; Dependabot reuses the header.
	sed -i.bak 's/ --no-index//' layer/requirements.txt && rm layer/requirements.txt.bak

layer:              ## Build the Lambda dependency layer (arm64, Python 3.14)
	PYTHON=$(PYTHON) scripts/build_layer.sh

lint:               ## Python (ruff) and the dashboard's JavaScript (ESLint)
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	npx --no-install eslint web

test:
	$(PYTHON) -m pytest

audit:              ## Check dependencies for known vulnerabilities
	$(PYTHON) -m pip_audit -r requirements-dev.txt  # CDK app and dev tools
	$(PYTHON) -m pip_audit --require-hashes --disable-pip -r layer/requirements.txt  # the lockfile: no resolving needed

synth: layer        ## Synthesize CloudFormation and run cdk-nag security checks
	. .venv/bin/activate && $(CDK) synth --quiet

diff: layer
	. .venv/bin/activate && $(CDK) diff

deploy: layer       ## Deploy (pass ALERT_EMAIL=you@example.com; optional OWNER_NAME=Alex PARTNER_NAME=Sam)
	. .venv/bin/activate && $(CDK) deploy --require-approval broadening -c alertEmail=$(ALERT_EMAIL) \
		$(if $(OWNER_NAME),-c ownerName="$(OWNER_NAME)") $(if $(PARTNER_NAME),-c partnerName="$(PARTNER_NAME)")

clean:
	rm -rf build cdk.out .pytest_cache .ruff_cache .coverage
