"""Portfolio Tracker infrastructure.

Security posture (see docs/SECURITY.md for the full list):
- Everything is encrypted at rest. By default with AWS-owned/managed keys (free); set the
  `useCustomerManagedKey` context flag to use one rotating customer-managed KMS key instead.
- Every Lambda has its own role with only the grants it needs; Bedrock access is limited to one model.
- The API requires a Cognito JWT on every route, is throttled, and logs every request.
- Data is retained on stack deletion, with point-in-time recovery and deletion protection.
- API keys for the data providers live in SSM Parameter Store as SecureStrings, never in code.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from aws_cdk import (
    Aws,
    CfnOutput,
    DefaultStackSynthesizer,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import aws_accessanalyzer as accessanalyzer
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_guardduty as guardduty
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_destinations as destinations
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from cdk_nag import NagPackSuppression, NagSuppressions
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent
PARAM_PREFIX = "/portfolio-tracker"


def bedrock_statements(api: str, model_id: str) -> list[iam.PolicyStatement]:
    """Least-privilege permission to call exactly one Claude model.

    runtime + cross-region profile (e.g. us.anthropic.claude-haiku-4-5-...): invoke the profile in
    this region, and the underlying model in whichever US region the profile routes to, but only
    when the call comes through that profile.
    """
    if api == "mantle":
        return [
            iam.PolicyStatement(
                actions=["bedrock-mantle:CreateInference"],
                resources=[f"arn:{Aws.PARTITION}:bedrock-mantle:{Aws.REGION}:{Aws.ACCOUNT_ID}:project/*"],
                conditions={"StringEquals": {"bedrock-mantle:Model": model_id}},
            )
        ]
    _, _, rest = model_id.partition(".")
    if rest.startswith("anthropic."):  # inference profile id such as us.anthropic...
        profile_arn = f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:inference-profile/{model_id}"
        return [
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[profile_arn]),
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[f"arn:{Aws.PARTITION}:bedrock:*::foundation-model/{rest}"],
                conditions={"StringEquals": {"bedrock:InferenceProfileArn": profile_arn}},
            ),
        ]
    return [
        iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}::foundation-model/{model_id}"],
        )
    ]


def _flag(value: Any, default: bool = False) -> bool:
    """Context values from `-c key=value` arrive as strings; from cdk.json as booleans."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


class PortfolioTrackerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        alert_email: str | None = self.node.try_get_context("alertEmail")
        allowed_origins: list[str] = self.node.try_get_context("allowedOrigins") or ["http://localhost:5173"]
        model_id: str = self.node.try_get_context("bedrockModelId") or "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        bedrock_api: str = self.node.try_get_context("bedrockApi") or "runtime"
        if bedrock_api not in ("runtime", "mantle"):
            raise ValueError("bedrockApi must be 'runtime' or 'mantle'")
        budget_setting = self.node.try_get_context("monthlyBudgetUsd")
        monthly_budget = float(5 if budget_setting is None else budget_setting)  # 0 = no budget
        # Display names for the two people whose holdings are tracked (e.g. you and a spouse).
        owner_name: str = self.node.try_get_context("ownerName") or ""
        partner_name: str = self.node.try_get_context("partnerName") or ""
        layer_path = Path(self.node.try_get_context("layerPath") or ROOT / "build" / "layer")
        if not layer_path.is_dir():
            raise FileNotFoundError(f"Lambda layer not found at {layer_path}. Run `make layer` first.")

        Tags.of(self).add("project", "portfolio-tracker")

        # ------------------------------------------------------------------ encryption key
        # Off (default): AWS-owned/AWS-managed keys everywhere, no monthly key charge.
        # On: one customer-managed key (about $1/month) for control, revocation and CloudTrail auditing.
        use_cmk = _flag(self.node.try_get_context("useCustomerManagedKey"))
        key: kms.Key | None = None
        if use_cmk:
            key = kms.Key(
                self,
                "DataKey",
                alias="alias/portfolio-tracker",
                description="Encrypts Portfolio Tracker data, messages and logs",
                enable_key_rotation=True,
                removal_policy=RemovalPolicy.RETAIN,
            )
            # CloudWatch Logs, CloudWatch alarms and EventBridge (publishing to the encrypted topic) need it too.
            key.add_to_resource_policy(
                iam.PolicyStatement(
                    principals=[iam.ServicePrincipal(f"logs.{Aws.REGION}.amazonaws.com")],
                    actions=["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"],
                    resources=["*"],
                    conditions={
                        "ArnLike": {
                            "kms:EncryptionContext:aws:logs:arn": f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:*"
                        }
                    },
                )
            )
            key.add_to_resource_policy(
                iam.PolicyStatement(
                    principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com"), iam.ServicePrincipal("events.amazonaws.com")],
                    actions=["kms:Decrypt", "kms:GenerateDataKey*"],
                    resources=["*"],
                    conditions={"StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID}},
                )
            )

        # ------------------------------------------------------------------ data
        table = dynamodb.Table(
            self,
            "Table",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED if key else dynamodb.TableEncryption.DEFAULT,
            encryption_key=key,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            deletion_protection=True,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.RETAIN,
        )
        table.add_global_secondary_index(
            index_name="gsi1",
            partition_key=dynamodb.Attribute(name="gsi1pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="gsi1sk", type=dynamodb.AttributeType.STRING),
        )

        # ------------------------------------------------------------------ notifications
        # Alerts and reports carry portfolio details, so the topic is always encrypted: with the
        # customer key when enabled, otherwise with the free AWS-managed SNS key.
        topic_key = key or kms.Alias.from_alias_name(self, "SnsManagedKey", "alias/aws/sns")
        topic = sns.Topic(self, "AlertsTopic", display_name="Portfolio Tracker", master_key=topic_key, enforce_ssl=True)
        if key:
            ops_topic = topic
        else:
            # CloudWatch alarms cannot publish to a topic encrypted with the AWS-managed SNS key,
            # so alarms get their own topic. Alarm messages contain no portfolio data.
            ops_topic = sns.Topic(self, "OpsTopic", display_name="Portfolio Tracker ops", enforce_ssl=True)
            NagSuppressions.add_resource_suppressions(
                ops_topic,
                [
                    NagPackSuppression(
                        id="AwsSolutions-SNS2",
                        reason="Only operational and security notifications (CloudWatch alarms, Access Analyzer and GuardDuty "
                        "findings; no portfolio data) use this topic; alarms cannot publish to a topic encrypted with the "
                        "AWS-managed key. Enable useCustomerManagedKey to encrypt it.",
                    )
                ],
            )
        if alert_email:
            topic.add_subscription(subscriptions.EmailSubscription(alert_email))
            if ops_topic is not topic:
                ops_topic.add_subscription(subscriptions.EmailSubscription(alert_email))

        # ------------------------------------------------------------------ web dashboard hosting
        # Private bucket, served only through CloudFront (Origin Access Control). Strict security
        # headers; the CSP allows scripts only from this site and API calls only to this region's
        # API Gateway and this app's Cognito login domain.
        auth_prefix = f"portfolio-tracker-{Aws.ACCOUNT_ID}"
        auth_url = f"https://{auth_prefix}.auth.{Aws.REGION}.amazoncognito.com"
        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            removal_policy=RemovalPolicy.RETAIN,
        )
        csp = "; ".join(
            [
                "default-src 'none'",
                "script-src 'self'",
                "style-src 'self' https://fonts.googleapis.com",
                "font-src https://fonts.gstatic.com",
                "img-src 'self' data:",
                "manifest-src 'self'",
                f"connect-src 'self' https://*.execute-api.{Aws.REGION}.amazonaws.com {auth_url}",
                "base-uri 'none'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                "object-src 'none'",
                "upgrade-insecure-requests",
            ]
        )
        headers = cloudfront.ResponseHeadersPolicy(
            self,
            "SiteHeaders",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=csp, override=True
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(730), include_subdomains=True, preload=True, override=True
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override=True
                ),
            ),
            custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[
                    cloudfront.ResponseCustomHeader(
                        header="Permissions-Policy", value="camera=(), microphone=(), geolocation=(), payment=()", override=True
                    )
                ]
            ),
        )
        distribution = cloudfront.Distribution(
            self,
            "Site",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=headers,
                compress=True,
            ),
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # North America and Europe edges: cheapest
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            comment="Portfolio Tracker dashboard",
        )
        site_url = f"https://{distribution.distribution_domain_name}"
        allowed_origins = [*allowed_origins, site_url]

        # ------------------------------------------------------------------ auth
        user_pool = cognito.UserPool(
            self,
            "UserPool",
            self_sign_up_enabled=False,  # accounts are created by the owner only
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=Duration.days(3),
            ),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            # Passkeys (Touch ID / Face ID) as a first factor; the password stays as a backup.
            sign_in_policy=cognito.SignInPolicy(
                allowed_first_auth_factors=cognito.AllowedFirstAuthFactors(password=True, passkey=True)
            ),
            passkey_user_verification=cognito.PasskeyUserVerification.REQUIRED,
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        # A passkey with Touch ID / Face ID counts as MFA, so it still works for anyone who turns on TOTP.
        cfn_pool: cognito.CfnUserPool = user_pool.node.default_child  # type: ignore[assignment]
        cfn_pool.web_authn_factor_configuration = "MULTI_FACTOR_WITH_USER_VERIFICATION"
        user_pool_client = user_pool.add_client(
            "WebClient",
            auth_flows=cognito.AuthFlow(user=True, user_srp=True, user_password=True),
            prevent_user_existence_errors=True,
            generate_secret=False,  # public browser client
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(7),
            enable_token_revocation=True,
            # Dashboard sign-in: Cognito's hosted page with authorization code + PKCE (no client secret).
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[f"{site_url}/", "http://localhost:5173/"],
                logout_urls=[f"{site_url}/", "http://localhost:5173/"],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
        )
        # Managed login (v2) is needed for passkeys; the passkey relying party is this Cognito domain.
        login_domain = user_pool.add_domain(
            "LoginDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=auth_prefix),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
        )
        # Sign-in page style: dark, in the dashboard's colours, over the background in infra/login/.
        login_dir = Path(__file__).parent / "login"
        branding = cognito.CfnManagedLoginBranding(
            self,
            "LoginBranding",
            user_pool_id=user_pool.user_pool_id,
            client_id=user_pool_client.user_pool_client_id,
            use_cognito_provided_values=False,
            settings=json.loads((login_dir / "branding.json").read_text()),
            assets=[
                cognito.CfnManagedLoginBranding.AssetTypeProperty(
                    category="PAGE_BACKGROUND",
                    color_mode="DARK",
                    extension="JPEG",
                    bytes=base64.b64encode((login_dir / "background.jpg").read_bytes()).decode(),
                )
            ],
        )
        login_domain.node.add_dependency(branding)  # branding exists before the page switches to v2

        # ------------------------------------------------------------------ Lambda shared pieces
        layer = lambda_.LayerVersion(
            self,
            "DepsLayer",
            code=lambda_.Code.from_asset(str(layer_path)),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_13],
            compatible_architectures=[lambda_.Architecture.ARM_64],
            description="anthropic SDK + pydantic",
        )
        code = lambda_.Code.from_asset(str(ROOT / "src"), exclude=["**/__pycache__", "**/*.pyc"])
        common_env = {
            "TABLE_NAME": table.table_name,
            "ALERTS_TOPIC_ARN": topic.topic_arn,
            "PRICE_API_KEY_PARAM": f"{PARAM_PREFIX}/tiingo-api-key",
            "NEWS_API_KEY_PARAM": f"{PARAM_PREFIX}/finnhub-api-key",
            "BEDROCK_MODEL_ID": model_id,
            "BEDROCK_API": bedrock_api,
            "NEWS_EXCLUDED_TICKERS": ",".join(self.node.try_get_context("newsExcludedTickers") or []),
            "PARTNER_NEWS": str(_flag(self.node.try_get_context("partnerNews"), default=True)).lower(),
            "BEDROCK_REGION": Aws.REGION,
            "OWNER_NAME": owner_name,
            "PARTNER_NAME": partner_name,
        }

        def make_function(name: str, handler: str, timeout: int, memory: int = 256) -> lambda_.Function:
            log_group = logs.LogGroup(
                self,
                f"{name}Logs",
                retention=logs.RetentionDays.ONE_MONTH,
                encryption_key=key,
                removal_policy=RemovalPolicy.DESTROY,
            )
            return lambda_.Function(
                self,
                name,
                runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64,
                handler=f"portfolio.handlers.{handler}.handler",
                code=code,
                layers=[layer],
                timeout=Duration.seconds(timeout),
                memory_size=memory,
                environment=common_env,
                environment_encryption=key,
                tracing=lambda_.Tracing.ACTIVE,
                log_group=log_group,
                logging_format=lambda_.LoggingFormat.JSON,
                recursive_loop=lambda_.RecursiveLoop.TERMINATE,
            )

        def ssm_param_arn(name: str) -> str:
            return f"arn:{Aws.PARTITION}:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:parameter{PARAM_PREFIX}/{name}"

        # ------------------------------------------------------------------ functions
        price_sync_dlq = sqs.Queue(
            self,
            "PriceSyncDlq",
            encryption=sqs.QueueEncryption.KMS if key else sqs.QueueEncryption.SQS_MANAGED,
            encryption_master_key=key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        price_sync = make_function("PriceSync", "price_sync", timeout=300)
        price_sync.configure_async_invoke(on_failure=destinations.SqsDestination(price_sync_dlq), retry_attempts=1)
        intraday = make_function("IntradayQuotes", "intraday_quotes", timeout=60)
        for fn in (price_sync, intraday):
            table.grant_read_write_data(fn)
            fn.add_to_role_policy(iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[ssm_param_arn("tiingo-api-key")]))

        news_ingest = make_function("NewsIngest", "news_ingest", timeout=600, memory=512)
        table.grant_read_write_data(news_ingest)
        news_ingest.add_to_role_policy(
            iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[ssm_param_arn("finnhub-api-key")])
        )
        for statement in bedrock_statements(bedrock_api, model_id):
            news_ingest.add_to_role_policy(statement)

        evaluate = make_function("EvaluateSignals", "evaluate_signals", timeout=300)
        table.grant_read_write_data(evaluate)
        topic.grant_publish(evaluate)

        weekly = make_function("WeeklyReport", "weekly_report", timeout=300)
        table.grant_read_data(weekly)
        topic.grant_publish(weekly)

        # Monthly checkup email, written by a stronger model than the nightly news summaries.
        checkup_model: str = self.node.try_get_context("checkupModelId") or model_id
        monthly = make_function("MonthlyCheckup", "monthly_checkup", timeout=300, memory=1024)  # PDF rendering
        monthly.add_environment("CHECKUP_MODEL_ID", checkup_model)
        monthly.add_environment("SITE_URL", site_url)
        if alert_email:
            # The PDF goes out through SES (SNS can't attach files), from and to the alert address, which must be
            # a verified SES identity. Without it the job falls back to the plain-text SNS email.
            monthly.add_environment("REPORT_EMAIL", alert_email)
            monthly.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ses:SendEmail", "ses:SendRawEmail"],
                    resources=[f"arn:{Aws.PARTITION}:ses:{Aws.REGION}:{Aws.ACCOUNT_ID}:identity/{alert_email}"],
                )
            )
        table.grant_read_write_data(monthly)
        topic.grant_publish(monthly)
        for statement in bedrock_statements(bedrock_api, checkup_model):
            monthly.add_to_role_policy(statement)

        api_fn = make_function("Api", "api", timeout=15, memory=512)
        api_fn.add_environment("PRICE_SYNC_FUNCTION_NAME", price_sync.function_name)
        table.grant_read_write_data(api_fn)
        price_sync.grant_invoke(api_fn)

        # ------------------------------------------------------------------ API
        api_logs = logs.LogGroup(
            self,
            "ApiAccessLogs",
            retention=logs.RetentionDays.THREE_MONTHS,
            encryption_key=key,
            removal_policy=RemovalPolicy.DESTROY,
        )
        authorizer = authorizers.HttpUserPoolAuthorizer("CognitoAuthorizer", user_pool, user_pool_clients=[user_pool_client])
        http_api = apigw.HttpApi(
            self,
            "HttpApi",
            create_default_stage=False,
            default_authorizer=authorizer,
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=allowed_origins,
                allow_methods=[
                    apigw.CorsHttpMethod.GET,
                    apigw.CorsHttpMethod.POST,
                    apigw.CorsHttpMethod.PUT,
                    apigw.CorsHttpMethod.DELETE,
                ],
                allow_headers=["Authorization", "Content-Type"],
                max_age=Duration.hours(1),
            ),
        )
        stage = apigw.HttpStage(
            self,
            "DefaultStage",
            http_api=http_api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=10, burst_limit=20),
        )
        cfn_stage: apigw.CfnStage = stage.node.default_child  # type: ignore[assignment]
        cfn_stage.access_log_settings = apigw.CfnStage.AccessLogSettingsProperty(
            destination_arn=api_logs.log_group_arn,
            format=(
                '{"requestId":"$context.requestId","ip":"$context.identity.sourceIp",'
                '"requestTime":"$context.requestTime","routeKey":"$context.routeKey",'
                '"status":"$context.status","latency":"$context.responseLatency",'
                '"user":"$context.authorizer.claims.sub","error":"$context.error.message"}'
            ),
        )
        integration = integrations.HttpLambdaIntegration("ApiIntegration", api_fn)
        routes = {
            "/holdings": [apigw.HttpMethod.GET],
            "/groups/{group}/holdings/{ticker}": [apigw.HttpMethod.PUT, apigw.HttpMethod.DELETE],
            "/groups/{group}/holdings/{ticker}/purchases": [apigw.HttpMethod.POST],
            "/rule-types": [apigw.HttpMethod.GET],
            "/rules": [apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            "/rules/{ruleId}": [apigw.HttpMethod.DELETE],
            "/alerts": [apigw.HttpMethod.GET],
            "/portfolio": [apigw.HttpMethod.GET],
            "/profile": [apigw.HttpMethod.GET],
            "/signals": [apigw.HttpMethod.GET],
            "/news": [apigw.HttpMethod.GET],
            "/watchlist": [apigw.HttpMethod.GET],
            "/watchlist/{ticker}": [apigw.HttpMethod.PUT, apigw.HttpMethod.DELETE],
            "/groups/{group}/holdings/{ticker}/transactions": [apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            "/groups/{group}/holdings/{ticker}/transactions/{txnId}": [apigw.HttpMethod.PUT, apigw.HttpMethod.DELETE],
            "/realized": [apigw.HttpMethod.GET],
            "/checkup": [apigw.HttpMethod.GET],
            "/targets": [apigw.HttpMethod.GET, apigw.HttpMethod.PUT],
        }
        for path, methods in routes.items():
            http_api.add_routes(path=path, methods=methods, integration=integration)

        # ------------------------------------------------------------------ weekday pipeline
        retry = {
            "errors": ["Lambda.ServiceException", "Lambda.TooManyRequestsException", "States.Timeout"],
            "interval": Duration.seconds(30),
            "max_attempts": 2,
            "backoff_rate": 2.0,
        }
        sync_step = tasks.LambdaInvoke(self, "Sync prices", lambda_function=price_sync, result_path=sfn.JsonPath.DISCARD)
        sync_step.add_retry(**retry)
        news_step = tasks.LambdaInvoke(
            self, "Ingest and summarize news", lambda_function=news_ingest, result_path=sfn.JsonPath.DISCARD
        )
        news_step.add_retry(**retry)
        signals_step = tasks.LambdaInvoke(
            self, "Evaluate signals and alerts", lambda_function=evaluate, result_path=sfn.JsonPath.DISCARD
        )
        signals_step.add_retry(**retry)
        # News is useful but optional: if it fails, signals still run on prices alone.
        news_step.add_catch(signals_step, result_path="$.newsError")
        definition = sync_step.next(news_step).next(signals_step)

        pipeline_logs = logs.LogGroup(
            self,
            "PipelineLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=key,
            removal_policy=RemovalPolicy.DESTROY,
        )
        pipeline = sfn.StateMachine(
            self,
            "DailyPipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(30),
            tracing_enabled=True,
            logs=sfn.LogOptions(destination=pipeline_logs, level=sfn.LogLevel.ALL, include_execution_data=False),
        )
        # 22:30 UTC = 5:30 PM EST / 6:30 PM EDT, after US market close either way.
        events.Rule(
            self,
            "WeekdaySchedule",
            schedule=events.Schedule.cron(minute="30", hour="22", week_day="MON-FRI"),
            targets=[targets.SfnStateMachine(pipeline)],
        )
        # Every 15 min across 9:30-16:00 New York time in both EDT and EST (13:00-21:45 UTC);
        # the function returns without calling Tiingo outside market hours. One request per run.
        if _flag(self.node.try_get_context("intradayQuotes"), default=True):
            events.Rule(
                self,
                "IntradaySchedule",
                schedule=events.Schedule.cron(minute="0/15", hour="13-21", week_day="MON-FRI"),
                targets=[targets.LambdaFunction(intraday, retry_attempts=0)],
            )
        events.Rule(
            self,
            "WeeklyReportSchedule",
            schedule=events.Schedule.cron(minute="0", hour="14", week_day="SUN"),
            targets=[targets.LambdaFunction(weekly, retry_attempts=2)],
        )
        events.Rule(
            self,
            "MonthlyCheckupSchedule",
            schedule=events.Schedule.expression("cron(0 14 ? * SAT#1 *)"),  # first Saturday, 14:00 UTC
            targets=[targets.LambdaFunction(monthly, retry_attempts=2)],
        )

        # ------------------------------------------------------------------ GitHub Actions deploy role
        # Keyless CI deploys: GitHub's OIDC token is exchanged for this role, which can only assume the CDK
        # bootstrap roles. Trust is pinned to the repo's `production` environment, which only allows `main`.
        # `githubRepo` must match the token's subject: repos using GitHub's immutable subjects send
        # `owner@id/repo@id`, not `owner/repo` (see the repo's actions/oidc/customization/sub).
        # Opt-in (`githubDeploy: true` plus `githubRepo`), since an account allows one provider per URL.
        github_repo: str = (self.node.try_get_context("githubRepo") or "").removeprefix("repo:")
        github_deploy: tuple[iam.Role, iam.OidcProviderNative] | None = None
        if _flag(self.node.try_get_context("githubDeploy")):
            if not re.fullmatch(r"[\w.-]+(@\d+)?/[\w.-]+(@\d+)?", github_repo):
                raise ValueError("githubDeploy needs githubRepo: owner/repo or owner@id/repo@id")
            github = "token.actions.githubusercontent.com"
            synthesizer = self.synthesizer
            qualifier = (
                synthesizer.bootstrap_qualifier if isinstance(synthesizer, DefaultStackSynthesizer) else None
            ) or DefaultStackSynthesizer.DEFAULT_QUALIFIER
            provider = iam.OidcProviderNative(
                self, "GitHubOidcProvider", url=f"https://{github}", client_ids=["sts.amazonaws.com"]
            )
            deploy_role = iam.Role(
                self,
                "GitHubDeployRole",
                description=f"GitHub Actions deploys from {github_repo} (production environment only)",
                max_session_duration=Duration.hours(1),
                assumed_by=iam.WebIdentityPrincipal(
                    provider.oidc_provider_arn,
                    conditions={
                        "StringEquals": {
                            f"{github}:aud": "sts.amazonaws.com",
                            f"{github}:sub": f"repo:{github_repo}:environment:production",
                        }
                    },
                ),
            )
            deploy_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[f"arn:{self.partition}:iam::{self.account}:role/cdk-{qualifier}-*-{self.account}-{self.region}"],
                )
            )
            CfnOutput(self, "GitHubDeployRoleArn", value=deploy_role.role_arn)
            github_deploy = (deploy_role, provider)

        def ops_rule(rule_id: str, pattern: events.EventPattern, text: str) -> None:
            """Emails matching AWS events to the ops topic."""
            events.Rule(
                self,
                rule_id,
                event_pattern=pattern,
                targets=[targets.SnsTopic(ops_topic, message=events.RuleTargetInput.from_text(text))],
            )

        # ------------------------------------------------------------------ external-access findings
        # Free account-level analyzer: flags any resource in this region (buckets, keys, roles, queues,
        # topics, functions) reachable from outside the account, and emails new findings to the ops topic.
        # Opt-in (`accessAnalyzer: true`), since an account allows only one and may already have it.
        # GitHub assuming the deploy role is intended, so that exact finding (that role, GitHub's provider, not
        # public) is archived; any other external access to it, or to anything else, still alerts.
        if _flag(self.node.try_get_context("accessAnalyzer")):
            archive_rules = None
            if github_deploy:
                deploy_role, provider = github_deploy
                archive_rules = [
                    accessanalyzer.CfnAnalyzer.ArchiveRuleProperty(
                        rule_name="ExpectedGitHubDeployRole",
                        filter=[
                            accessanalyzer.CfnAnalyzer.FilterProperty(property="resource", eq=[deploy_role.role_arn]),
                            accessanalyzer.CfnAnalyzer.FilterProperty(
                                property="principal.Federated", eq=[provider.oidc_provider_arn]
                            ),
                            accessanalyzer.CfnAnalyzer.FilterProperty(property="isPublic", eq=["false"]),
                        ],
                    )
                ]
            accessanalyzer.CfnAnalyzer(self, "ExternalAccessAnalyzer", type="ACCOUNT", archive_rules=archive_rules)
            ops_rule(
                "AccessAnalyzerFindings",
                events.EventPattern(
                    source=["aws.access-analyzer"], detail_type=["Access Analyzer Finding"], detail={"status": ["ACTIVE"]}
                ),
                f"IAM Access Analyzer: {events.EventField.from_path('$.detail.resourceType')} "
                f"{events.EventField.from_path('$.detail.resource')} is accessible from outside the account "
                f"(public: {events.EventField.from_path('$.detail.isPublic')}). "
                "Review it in the IAM console under Access Analyzer; archive it there if it is intended.",
            )

        # ------------------------------------------------------------------ threat detection
        # GuardDuty watches the account's API, DNS and network activity (e.g. stolen keys used from a strange
        # place) and emails medium and high findings to the ops topic. Opt-in (`guardDuty: true`): one per region.
        if _flag(self.node.try_get_context("guardDuty")):
            guardduty.CfnDetector(self, "GuardDuty", enable=True)  # updates to a finding re-publish every 6 hours
            ops_rule(
                "GuardDutyFindings",
                events.EventPattern(
                    source=["aws.guardduty"],
                    detail_type=["GuardDuty Finding"],
                    detail={"severity": events.Match.greater_than_or_equal(4)},
                ),
                f"GuardDuty severity {events.EventField.from_path('$.detail.severity')} in "
                f"{events.EventField.from_path('$.region')}: {events.EventField.from_path('$.detail.title')} "
                f"(finding {events.EventField.from_path('$.detail.id')}, seen "
                f"{events.EventField.from_path('$.detail.service.count')} times). Review it in the GuardDuty console.",
            )

        # ------------------------------------------------------------------ monitoring
        alarm_action = cw_actions.SnsAction(ops_topic)
        cloudwatch.Alarm(
            self,
            "PipelineFailedAlarm",
            metric=pipeline.metric_failed(period=Duration.hours(1)),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="The weekday price/news/signals pipeline failed",
        ).add_alarm_action(alarm_action)
        for fn in (api_fn, price_sync, news_ingest, evaluate, weekly, monthly):
            cloudwatch.Alarm(
                self,
                f"{fn.node.id}ErrorsAlarm",
                metric=fn.metric_errors(period=Duration.hours(1)),
                threshold=3,
                evaluation_periods=1,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                alarm_description=f"{fn.node.id} Lambda errors",
            ).add_alarm_action(alarm_action)
        cloudwatch.Alarm(
            self,
            "PriceSyncDlqAlarm",
            metric=price_sync_dlq.metric_approximate_number_of_messages_visible(period=Duration.hours(1)),
            threshold=1,
            evaluation_periods=1,
            alarm_description="A price backfill failed and landed in the dead-letter queue",
        ).add_alarm_action(alarm_action)

        if alert_email and monthly_budget > 0:
            budgets.CfnBudget(
                self,
                "MonthlyBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_type="COST",
                    time_unit="MONTHLY",
                    # Whole-account budget: tag-filtered budgets need cost allocation tags activated first.
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=monthly_budget, unit="USD"),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            comparison_operator="GREATER_THAN",
                            notification_type=threshold_type,
                            threshold=pct,
                            threshold_type="PERCENTAGE",
                        ),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(address=alert_email, subscription_type="EMAIL")],
                    )
                    for threshold_type, pct in (("ACTUAL", 80), ("FORECASTED", 100))
                ],
            )

        # ------------------------------------------------------------------ dashboard deployment
        # Uploads web/ plus a config.json with this stack's API URL and login settings, then
        # invalidates the CloudFront cache; the deploy doesn't wait for that to finish, so edges serve the
        # new version within a minute or two (the CI smoke test waits for it). Browsers revalidate
        # each file on load (max-age=0: a cheap 304 when unchanged) instead of guessing a cache lifetime,
        # so a deploy doesn't leave them running a new page with an old module; CloudFront keeps its
        # own long cache (s-maxage), which each deploy invalidates.
        s3deploy.BucketDeployment(
            self,
            "DeploySite",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.asset(str(ROOT / "web")),
                s3deploy.Source.json_data(
                    "config.json",
                    {
                        "apiUrl": http_api.api_endpoint,
                        "authDomain": auth_url,
                        "clientId": user_pool_client.user_pool_client_id,
                    },
                ),
            ],
            cache_control=[
                s3deploy.CacheControl.max_age(Duration.seconds(0)),
                s3deploy.CacheControl.must_revalidate(),
                s3deploy.CacheControl.s_max_age(Duration.days(365)),
            ],
            distribution=distribution,
            distribution_paths=["/*"],
            # Don't hold the deploy for the cache clear (1-3+ minutes); see the comment above.
            wait_for_distribution_invalidation=False,
            memory_limit=256,
            log_group=logs.LogGroup(
                self,
                "DeploySiteLogs",
                retention=logs.RetentionDays.ONE_WEEK,
                encryption_key=key,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )

        # ------------------------------------------------------------------ outputs
        CfnOutput(self, "DashboardUrl", value=site_url)
        CfnOutput(self, "ApiUrl", value=http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "PriceSyncFunctionName", value=price_sync.function_name)
        CfnOutput(self, "PipelineArn", value=pipeline.state_machine_arn)

        self.table = table
        self.functions = {
            "api": api_fn,
            "price_sync": price_sync,
            "news_ingest": news_ingest,
            "evaluate": evaluate,
            "weekly": weekly,
            "monthly": monthly,
        }
