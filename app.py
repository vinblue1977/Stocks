#!/usr/bin/env python3
"""CDK entry point. `cdk synth` also runs cdk-nag's AWS Solutions security checks."""

import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from infra.nag import apply_suppressions
from infra.stack import PortfolioTrackerStack

app = cdk.App()
stack = PortfolioTrackerStack(
    app,
    "PortfolioTracker",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-2"),
    ),
    termination_protection=True,
    description="Serverless stock portfolio tracker with news summaries and rule-based alerts",
)
apply_suppressions(stack)
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
app.synth()
