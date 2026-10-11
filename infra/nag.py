"""Documented exceptions to cdk-nag AWS Solutions rules. Each one says why it is acceptable."""

from __future__ import annotations

from aws_cdk import Stack
from cdk_nag import NagPackSuppression, NagSuppressions


def apply_suppressions(stack: Stack) -> None:
    NagSuppressions.add_stack_suppressions(
        stack,
        [
            NagPackSuppression(
                id="AwsSolutions-IAM4",
                reason="AWSLambdaBasicExecutionRole only allows writing the function's own logs; "
                "this is the AWS-recommended baseline for Lambda.",
                applies_to=["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"],
            ),
            NagPackSuppression(
                id="AwsSolutions-IAM5",
                reason="Wildcards are limited to: the table's own indexes (index/*), X-Ray trace writes "
                "(no resource-level support), Step Functions log delivery actions (AWS requires Resource '*'), "
                "KMS GenerateDataKey*/ReEncrypt* actions on the one stack key, "
                "Lambda version qualifiers of the stack's own functions, and Bedrock access to a single model: "
                "the model in any region its cross-region inference profile routes to (only via that profile, "
                "enforced by a bedrock:InferenceProfileArn condition), or Mantle projects limited by a "
                "bedrock-mantle:Model condition.",
            ),
            NagPackSuppression(
                id="AwsSolutions-COG2",
                reason="MFA is optional (TOTP) so the owner can sign in from the CLI during setup; "
                "enable it per user or switch the pool to Mfa.REQUIRED once the web app is in use.",
            ),
            NagPackSuppression(
                id="AwsSolutions-COG3",
                reason="Cognito advanced security (threat protection) needs the Plus feature plan, "
                "which costs more than this personal project's budget; self sign-up is disabled instead.",
            ),
            NagPackSuppression(
                id="AwsSolutions-COG8",
                reason="Same as COG3: the Plus feature plan is out of budget for a single-owner app.",
            ),
            NagPackSuppression(
                id="AwsSolutions-CFR1",
                reason="No geo restriction: the owner may sign in from anywhere; access is protected by Cognito.",
            ),
            NagPackSuppression(
                id="AwsSolutions-CFR2",
                reason="AWS WAF costs about $6+/month, more than this app's budget. The site serves only static files; "
                "data access goes through the authenticated, throttled API. See docs/SECURITY.md hardening options.",
            ),
            NagPackSuppression(
                id="AwsSolutions-CFR3",
                reason="CloudFront access logs are off to avoid log storage cost; the static site has no user data, "
                "and every data request is recorded in the API access logs.",
            ),
            NagPackSuppression(
                id="AwsSolutions-CFR4",
                reason="The default *.cloudfront.net certificate does not allow choosing a minimum TLS version; "
                "a custom domain with an ACM certificate (TLSv1.2_2021) is a documented upgrade.",
            ),
            NagPackSuppression(
                id="AwsSolutions-S1",
                reason="The site bucket holds only the public dashboard code and is readable only by CloudFront; "
                "server access logs would add cost without security value.",
            ),
            NagPackSuppression(
                id="AwsSolutions-SQS3",
                reason="This queue is itself the dead-letter queue for failed price backfills.",
            ),
            NagPackSuppression(
                id="AwsSolutions-L1",
                reason="Python 3.13 is pinned deliberately for the layer's compiled wheels; "
                "upgrade the runtime and rebuild the layer together.",
            ),
        ],
    )
