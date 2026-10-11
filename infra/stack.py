"""Portfolio Tracker infrastructure.

Security posture (see docs/SECURITY.md for the full list):
- One customer-managed KMS key (rotation on) encrypts the table, SNS topic, SQS queue and log groups.
- Every Lambda has its own role with only the grants it needs; Bedrock access is limited to one model.
- The API requires a Cognito JWT on every route, is throttled, and logs every request.
- Data is retained on stack deletion, with point-in-time recovery and deletion protection.
- API keys for the data providers live in SSM Parameter Store as SecureStrings, never in code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_destinations as destinations
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent
PARAM_PREFIX = "/portfolio-tracker"


class PortfolioTrackerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        alert_email: str | None = self.node.try_get_context("alertEmail")
        allowed_origins: list[str] = self.node.try_get_context("allowedOrigins") or ["http://localhost:5173"]
        model_id: str = self.node.try_get_context("bedrockModelId") or "anthropic.claude-haiku-5-5"
        monthly_budget = float(self.node.try_get_context("monthlyBudgetUsd") or 5)
        layer_path = Path(self.node.try_get_context("layerPath") or ROOT / "build" / "layer")
        if not layer_path.is_dir():
            raise FileNotFoundError(f"Lambda layer not found at {layer_path}. Run `make layer` first.")

        Tags.of(self).add("project", "portfolio-tracker")

        # ------------------------------------------------------------------ encryption key
        key = kms.Key(
            self,
            "DataKey",
            alias="alias/portfolio-tracker",
            description="Encrypts Portfolio Tracker data, messages and logs",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        # CloudWatch Logs and CloudWatch alarms (publishing to the encrypted topic) need the key too.
        key.add_to_resource_policy(
            iam.PolicyStatement(
                principals=[iam.ServicePrincipal(f"logs.{Aws.REGION}.amazonaws.com")],
                actions=["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"],
                resources=["*"],
                conditions={
                    "ArnLike": {"kms:EncryptionContext:aws:logs:arn": f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:*"}
                },
            )
        )
        key.add_to_resource_policy(
            iam.PolicyStatement(
                principals=[iam.ServicePrincipal("cloudwatch.amazonaws.com")],
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
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
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
        topic = sns.Topic(self, "AlertsTopic", display_name="Portfolio Tracker", master_key=key, enforce_ssl=True)
        if alert_email:
            topic.add_subscription(subscriptions.EmailSubscription(alert_email))

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
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        user_pool_client = user_pool.add_client(
            "WebClient",
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            prevent_user_existence_errors=True,
            generate_secret=False,  # public browser client
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(7),
            enable_token_revocation=True,
        )

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
            "BENCHMARK_TICKER": self.node.try_get_context("benchmarkTicker") or "SPY",
            "PRICE_API_KEY_PARAM": f"{PARAM_PREFIX}/tiingo-api-key",
            "NEWS_API_KEY_PARAM": f"{PARAM_PREFIX}/finnhub-api-key",
            "BEDROCK_MODEL_ID": model_id,
            "BEDROCK_REGION": Aws.REGION,
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
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        price_sync = make_function("PriceSync", "price_sync", timeout=300)
        price_sync.configure_async_invoke(on_failure=destinations.SqsDestination(price_sync_dlq), retry_attempts=1)
        table.grant_read_write_data(price_sync)
        price_sync.add_to_role_policy(
            iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[ssm_param_arn("tiingo-api-key")])
        )

        news_ingest = make_function("NewsIngest", "news_ingest", timeout=600, memory=512)
        table.grant_read_write_data(news_ingest)
        news_ingest.add_to_role_policy(
            iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[ssm_param_arn("finnhub-api-key")])
        )
        news_ingest.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-mantle:CreateInference"],
                resources=[f"arn:{Aws.PARTITION}:bedrock-mantle:{Aws.REGION}:{Aws.ACCOUNT_ID}:project/*"],
                conditions={"StringEquals": {"bedrock-mantle:Model": model_id}},
            )
        )

        evaluate = make_function("EvaluateSignals", "evaluate_signals", timeout=300)
        table.grant_read_write_data(evaluate)
        topic.grant_publish(evaluate)

        weekly = make_function("WeeklyReport", "weekly_report", timeout=300)
        table.grant_read_data(weekly)
        topic.grant_publish(weekly)

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
            "/holdings/{ticker}": [apigw.HttpMethod.PUT, apigw.HttpMethod.DELETE],
            "/rule-types": [apigw.HttpMethod.GET],
            "/rules": [apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            "/rules/{ruleId}": [apigw.HttpMethod.DELETE],
            "/alerts": [apigw.HttpMethod.GET],
            "/portfolio": [apigw.HttpMethod.GET],
            "/signals": [apigw.HttpMethod.GET],
            "/news": [apigw.HttpMethod.GET],
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
        events.Rule(
            self,
            "WeeklyReportSchedule",
            schedule=events.Schedule.cron(minute="0", hour="14", week_day="SUN"),
            targets=[targets.LambdaFunction(weekly, retry_attempts=2)],
        )

        # ------------------------------------------------------------------ monitoring
        alarm_action = cw_actions.SnsAction(topic)
        cloudwatch.Alarm(
            self,
            "PipelineFailedAlarm",
            metric=pipeline.metric_failed(period=Duration.hours(1)),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="The weekday price/news/signals pipeline failed",
        ).add_alarm_action(alarm_action)
        for fn in (api_fn, price_sync, news_ingest, evaluate, weekly):
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

        if alert_email:
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

        # ------------------------------------------------------------------ outputs
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
        }
