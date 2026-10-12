PYTHON ?= .venv/bin/python
CDK ?= npx --yes aws-cdk@2

.PHONY: install layer lint test audit synth deploy diff clean

install:            ## Create the virtualenv and install everything
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt

layer:              ## Build the Lambda dependency layer (arm64, Python 3.13)
	PYTHON=$(PYTHON) scripts/build_layer.sh

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

test:
	$(PYTHON) -m pytest

audit:              ## Check dependencies for known vulnerabilities
	$(PYTHON) -m pip_audit -r requirements.txt -r layer/requirements.txt

synth: layer        ## Synthesize CloudFormation and run cdk-nag security checks
	. .venv/bin/activate && $(CDK) synth --quiet

diff: layer
	. .venv/bin/activate && $(CDK) diff

deploy: layer       ## Deploy (pass ALERT_EMAIL=you@example.com; optional OWNER_NAME=Alex PARTNER_NAME=Sam)
	. .venv/bin/activate && $(CDK) deploy --require-approval broadening -c alertEmail=$(ALERT_EMAIL) \
		$(if $(OWNER_NAME),-c ownerName="$(OWNER_NAME)") $(if $(PARTNER_NAME),-c partnerName="$(PARTNER_NAME)")

clean:
	rm -rf build cdk.out .pytest_cache .ruff_cache .coverage
