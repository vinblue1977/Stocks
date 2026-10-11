"""Asserts the security properties of the synthesized CloudFormation template."""

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.stack import PortfolioTrackerStack


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    layer = tmp_path_factory.mktemp("layer")
    (layer / "python").mkdir()
    app = cdk.App(context={"layerPath": str(layer), "alertEmail": "owner@example.com"})
    stack = PortfolioTrackerStack(app, "Test", env=cdk.Environment(account="111111111111", region="us-east-1"))
    return Template.from_stack(stack)


def test_table_encrypted_backed_up_and_retained(template):
    template.has_resource(
        "AWS::DynamoDB::Table",
        {
            "DeletionPolicy": "Retain",
            "Properties": Match.object_like(
                {
                    "SSESpecification": {"SSEEnabled": True, "SSEType": "KMS", "KMSMasterKeyId": Match.any_value()},
                    "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
                    "DeletionProtectionEnabled": True,
                }
            ),
        },
    )


def test_kms_key_rotates(template):
    template.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})


def test_every_route_requires_jwt(template):
    routes = template.find_resources("AWS::ApiGatewayV2::Route")
    assert routes
    for route in routes.values():
        assert route["Properties"]["AuthorizationType"] == "JWT"


def test_api_is_throttled_and_logged(template):
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "DefaultRouteSettings": {"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20},
            "AccessLogSettings": Match.object_like({"DestinationArn": Match.any_value()}),
        },
    )


def test_cognito_hardened(template):
    template.has_resource_properties(
        "AWS::Cognito::UserPool",
        Match.object_like(
            {
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
                "Policies": {"PasswordPolicy": Match.object_like({"MinimumLength": 12, "RequireSymbols": True})},
            }
        ),
    )
    template.has_resource_properties(
        "AWS::Cognito::UserPoolClient", Match.object_like({"PreventUserExistenceErrors": "ENABLED", "GenerateSecret": False})
    )


def test_topic_and_queue_encrypted_and_tls_only(template):
    template.has_resource_properties("AWS::SNS::Topic", {"KmsMasterKeyId": Match.any_value(), "DisplayName": "Portfolio Tracker"})
    template.has_resource_properties("AWS::SQS::Queue", Match.object_like({"KmsMasterKeyId": Match.any_value()}))
    policies = json.dumps(template.find_resources("AWS::SNS::TopicPolicy")) + json.dumps(
        template.find_resources("AWS::SQS::QueuePolicy")
    )
    assert '"aws:SecureTransport": "false"' in policies


def test_functions_traced_and_logs_encrypted(template):
    functions = template.find_resources("AWS::Lambda::Function")
    assert len(functions) == 5
    for fn in functions.values():
        assert fn["Properties"]["TracingConfig"] == {"Mode": "Active"}
        assert fn["Properties"]["Architectures"] == ["arm64"]
    for group in template.find_resources("AWS::Logs::LogGroup").values():
        assert "KmsKeyId" in group["Properties"]


def test_bedrock_access_limited_to_one_model(template):
    policies = json.dumps(template.find_resources("AWS::IAM::Policy"))
    assert "bedrock-mantle:CreateInference" in policies
    assert '"bedrock-mantle:Model": "anthropic.claude-haiku-5-5"' in policies
    assert '"bedrock:*"' not in policies and '"Action": "*"' not in policies


def test_secrets_scoped_to_named_parameters(template):
    policies = json.dumps(template.find_resources("AWS::IAM::Policy"))
    assert "parameter/portfolio-tracker/tiingo-api-key" in policies
    assert "parameter/portfolio-tracker/*" not in policies


def test_budget_and_alarms(template):
    template.resource_count_is("AWS::Budgets::Budget", 1)
    assert len(template.find_resources("AWS::CloudWatch::Alarm")) == 7
