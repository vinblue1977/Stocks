# Security

## Controls

| Risk | Control |
|---|---|
| Unauthenticated access | Cognito JWT on every route; self sign-up disabled |
| Reading another user's data | User ID taken only from the verified token; tested |
| Password attacks | 12+ character policy, optional TOTP MFA, throttling, no user-existence leaks |
| Stolen tokens | 1-hour tokens, 7-day refresh, revocation enabled |
| Bad input | Ticker/number validation, 10 KB body limit, per-user caps |
| Prompt injection in headlines | Passed as tagged data; schema-validated output; no advice; output triggers no actions |
| Leaked secrets | SSM SecureString, scoped per function; never in code, env vars or logs |
| Over-privileged code | One role per function; Bedrock limited to one model; cdk-nag checks |
| Data loss | PITR, deletion protection, resources retained on stack delete |
| Data exposure | Encryption at rest (AWS default or optional customer key); TLS-only SNS/SQS; HTTPS-only outbound |
| Runaway cost | Throttling, recursive-loop guard, pipeline timeout, budget alert |
| Vulnerable deps / CI | `pip-audit`; OIDC deploys with no stored keys; protected `production` environment |

Accepted cdk-nag exceptions and their reasons are in [`infra/nag.py`](../infra/nag.py).

## Optional hardening

1. **Customer-managed key:** set `useCustomerManagedKey=true` (about $1/month) for revocation and CloudTrail auditing.
2. **Require MFA:** set `Mfa.REQUIRED` once the web app supports MFA setup.
3. **Cognito Plus plan:** adds threat protection (paid).
4. **WAF:** put CloudFront and a WAF in front of the API before exposing it publicly.
5. **CI:** pin Actions to commit SHAs and enable Dependabot.
6. **Deploy role:** limit the OIDC role to the CDK bootstrap roles for this repo's `production` environment.
