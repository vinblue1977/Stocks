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
                "Lambda version qualifiers of the stack's own functions, and Bedrock projects in this account "
                "restricted by a bedrock-mantle:Model condition to a single model.",
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
