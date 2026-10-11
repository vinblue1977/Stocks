# Security

## What we protect

| Asset | Why it matters |
|---|---|
| Holdings, cost basis, alert rules | Personal financial information |
| Data-provider API keys | Abuse could exhaust quotas or incur charges |
| AWS account spend | Bedrock and Lambda are pay-per-use |
| Alert email channel | Spoofed or excessive alerts would erode trust |

## Threats and controls

| Threat | Control |
|---|---|
| Someone calls the API without an account | Cognito JWT authorizer on every route; self sign-up disabled |
| A signed-in user reads another user's data | User ID comes only from the verified token `sub`; every key is prefixed by it; tests cover cross-user access |
| Password guessing or credential stuffing | 12+ character policy with symbols; optional TOTP MFA; API throttling; user-existence errors suppressed |
| Stolen token | 1-hour access/ID tokens, 7-day refresh tokens, token revocation enabled |
| Malformed or oversized input | Ticker regex, numeric ranges with NaN rejection, 10 KB body limit, per-user caps (100 holdings, 50 rules) |
| Prompt injection via news headlines | Headlines passed as JSON inside `<articles>` tags, instructed as data only; schema-validated output; output never triggers actions |
| Leaked secrets | API keys in SSM SecureString, scoped per function; never in env vars, code or logs; `.gitignore` blocks `.env` files |
| Over-privileged code | One role per function; Bedrock limited to one model; SSM limited to named parameters; cdk-nag IAM checks |
| Data loss or accidental deletion | PITR, deletion protection, `RETAIN` on table, user pool and key |
| Data exposure at rest or in transit | Encryption at rest everywhere (AWS-managed keys by default; optional rotating customer-managed key via `useCustomerManagedKey`); TLS enforced on SNS/SQS; HTTPS-only outbound calls |
| Runaway cost (bug, abuse, retry storm) | API throttling, Lambda recursive-loop termination, pipeline timeout, monthly budget alert |
| Vulnerable dependencies | `pip-audit` in CI; minimal runtime deps (anthropic SDK + pydantic) |
| CI credential theft | OIDC short-lived credentials, `id-token: write` only on the deploy job, protected `production` environment |

## Hardening options

These are off by default to keep the project cheap and easy to set up. Turn them on as the app matures:

1. **Customer-managed key:** set `useCustomerManagedKey` to `true` (about $1/month) for your own key policy, instant revocation and CloudTrail records of every decrypt. It also lets alarms share the encrypted alerts topic instead of a separate unencrypted ops topic.
2. **Require MFA:** change `mfa=cognito.Mfa.OPTIONAL` to `REQUIRED` in `infra/stack.py` once the web app handles MFA setup.
3. **Cognito threat protection:** switch to the Plus feature plan for compromised-credential and risk-based checks (extra monthly cost).
4. **WAF:** HTTP APIs can't attach AWS WAF directly. Put CloudFront with a WAF web ACL in front of the API, for IP reputation and rate-based rules, when exposing it publicly.
5. **Pin GitHub Actions to commit SHAs** instead of version tags, and enable Dependabot for actions and pip.
6. **Scope the deploy role:** give the GitHub OIDC role only `sts:AssumeRole` on the CDK bootstrap roles (`cdk-*-deploy-role`, `cdk-*-file-publishing-role`), restricted by `token.actions.githubusercontent.com:sub` to this repository's `production` environment.
7. **Alarm on auth failures:** add a metric filter on API access logs for 401/403 spikes.

## Reporting

This is a personal project. If you find a vulnerability, open a private security advisory on the repository rather than a public issue.
