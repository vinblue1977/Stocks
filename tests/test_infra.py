"""Asserts the security properties of the synthesized CloudFormation template, in both encryption modes."""

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.stack import PortfolioTrackerStack, _flag


def synth(tmp_path_factory, **context):
    layer = tmp_path_factory.mktemp("layer")
    (layer / "python").mkdir()
    app = cdk.App(context={"layerPath": str(layer), "alertEmail": "owner@example.com", **context})
    stack = PortfolioTrackerStack(app, "Test", env=cdk.Environment(account="111111111111", region="us-east-1"))
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """Default mode: AWS-managed encryption, no customer-managed key."""
    return synth(tmp_path_factory)


@pytest.fixture(scope="module")
def cmk_template(tmp_path_factory):
    return synth(tmp_path_factory, useCustomerManagedKey=True)


# ---------------------------------------------------------------- encryption modes
def test_default_mode_creates_no_paid_key(template):
    template.resource_count_is("AWS::KMS::Key", 0)
    table = next(iter(template.find_resources("AWS::DynamoDB::Table").values()))
    assert "KMSMasterKeyId" not in json.dumps(table)  # AWS-owned key
    template.has_resource_properties("AWS::SQS::Queue", Match.object_like({"SqsManagedSseEnabled": True}))
    topics = template.find_resources("AWS::SNS::Topic")
    alerts = next(t for t in topics.values() if t["Properties"]["DisplayName"] == "Portfolio Tracker")
    assert "alias/aws/sns" in json.dumps(alerts["Properties"]["KmsMasterKeyId"])  # alerts always encrypted
    # Alarms go to a separate ops topic (alarms cannot publish to an aws/sns-encrypted topic)
    template.resource_count_is("AWS::SNS::Topic", 2)
    alarm_targets = {
        json.dumps(a["Properties"]["AlarmActions"]) for a in template.find_resources("AWS::CloudWatch::Alarm").values()
    }
    assert len(alarm_targets) == 1 and "OpsTopic" in alarm_targets.pop()


def test_cmk_mode_encrypts_everything_with_rotating_key(cmk_template):
    cmk_template.resource_count_is("AWS::KMS::Key", 1)
    cmk_template.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})
    cmk_template.has_resource_properties(
        "AWS::DynamoDB::Table",
        Match.object_like({"SSESpecification": {"SSEEnabled": True, "SSEType": "KMS", "KMSMasterKeyId": Match.any_value()}}),
    )
    cmk_template.has_resource_properties("AWS::SQS::Queue", Match.object_like({"KmsMasterKeyId": Match.any_value()}))
    cmk_template.resource_count_is("AWS::SNS::Topic", 1)
    for group in cmk_template.find_resources("AWS::Logs::LogGroup").values():
        assert "KmsKeyId" in group["Properties"]
    for fn in cmk_template.find_resources("AWS::Lambda::Function").values():
        assert "KmsKeyArn" in fn["Properties"]


@pytest.mark.parametrize(
    "value, expected", [(True, True), ("true", True), ("on", True), (False, False), ("false", False), (None, False)]
)
def test_flag_parsing(value, expected):
    assert _flag(value) is expected


# ---------------------------------------------------------------- controls that apply in both modes
@pytest.fixture(scope="module", params=["default", "cmk"])
def any_template(request, template, cmk_template):
    return template if request.param == "default" else cmk_template


def test_table_backed_up_and_retained(any_template):
    any_template.has_resource(
        "AWS::DynamoDB::Table",
        {
            "DeletionPolicy": "Retain",
            "Properties": Match.object_like(
                {"PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}, "DeletionProtectionEnabled": True}
            ),
        },
    )


def test_every_route_requires_jwt(any_template):
    routes = any_template.find_resources("AWS::ApiGatewayV2::Route")
    assert routes
    for route in routes.values():
        assert route["Properties"]["AuthorizationType"] == "JWT"


def test_api_is_throttled_and_logged(any_template):
    any_template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "DefaultRouteSettings": {"ThrottlingRateLimit": 10, "ThrottlingBurstLimit": 20},
            "AccessLogSettings": Match.object_like({"DestinationArn": Match.any_value()}),
        },
    )


def test_cognito_hardened(any_template):
    any_template.has_resource_properties(
        "AWS::Cognito::UserPool",
        Match.object_like(
            {
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
                "Policies": {"PasswordPolicy": Match.object_like({"MinimumLength": 12, "RequireSymbols": True})},
            }
        ),
    )
    any_template.has_resource_properties(
        "AWS::Cognito::UserPoolClient", Match.object_like({"PreventUserExistenceErrors": "ENABLED", "GenerateSecret": False})
    )


def test_topics_and_queue_require_tls(any_template):
    topic_policies = any_template.find_resources("AWS::SNS::TopicPolicy")
    assert len(topic_policies) == len(any_template.find_resources("AWS::SNS::Topic"))
    policies = json.dumps(topic_policies) + json.dumps(any_template.find_resources("AWS::SQS::QueuePolicy"))
    assert '"aws:SecureTransport": "false"' in policies


def test_functions_traced_on_arm(any_template):
    functions = any_template.find_resources("AWS::Lambda::Function")
    assert len(functions) == 5
    for fn in functions.values():
        assert fn["Properties"]["TracingConfig"] == {"Mode": "Active"}
        assert fn["Properties"]["Architectures"] == ["arm64"]


def test_bedrock_access_limited_to_one_model(any_template):
    policies = json.dumps(any_template.find_resources("AWS::IAM::Policy"))
    assert "bedrock-mantle:CreateInference" in policies
    assert '"bedrock-mantle:Model": "anthropic.claude-haiku-5-5"' in policies
    assert '"bedrock:*"' not in policies and '"Action": "*"' not in policies


def test_secrets_scoped_to_named_parameters(any_template):
    policies = json.dumps(any_template.find_resources("AWS::IAM::Policy"))
    assert "parameter/portfolio-tracker/tiingo-api-key" in policies
    assert "parameter/portfolio-tracker/*" not in policies


def test_budget_and_alarms(any_template):
    any_template.resource_count_is("AWS::Budgets::Budget", 1)
    assert len(any_template.find_resources("AWS::CloudWatch::Alarm")) == 7
