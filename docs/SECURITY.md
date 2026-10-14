# Security

## Controls

| Risk | Control |
|---|---|
| Unauthenticated access | Cognito JWT on every route; self sign-up disabled |
| Dashboard attacks (XSS, clickjacking, tampering) | Strict CSP (scripts only from the site, no inline code), DOM built with `textContent` only, `X-Frame-Options: DENY`, HSTS, private S3 bucket reachable only through CloudFront |
| Token theft in the browser | OAuth code + PKCE via Cognito's hosted page (the app never sees the password); tokens in sessionStorage, cleared on tab close; 1-hour expiry |
| Reading another user's data | User ID taken only from the verified token; tested |
| Password attacks | Passkey sign-in (phishing-resistant, user verification required), 12+ character policy, optional TOTP MFA, throttling, no user-existence leaks |
| Stolen tokens | 1-hour tokens, 7-day refresh, revocation enabled |
| Bad input | Ticker/number validation, 10 KB body limit, per-user caps |
| Prompt injection in headlines | Passed as tagged data; schema-validated output; no advice; output triggers no actions |
| Leaked secrets | SSM SecureString, scoped per function; never in code, env vars or logs |
| Over-privileged code | One role per function; Bedrock limited to one model; cdk-nag checks |
| Data loss | PITR, deletion protection, resources retained on stack delete |
| Data exposure | Encryption at rest (AWS default or optional customer key); TLS-only SNS/SQS; HTTPS-only outbound |
| Runaway cost | Throttling, recursive-loop guard, pipeline timeout, budget alert |
| Vulnerable deps / CI | actionlint and zizmor on workflows; Dependabot waits 7 days after a release; `pip-audit` on every run and weekly; dependency review on PRs (exact versions only, so mainly Actions until Python deps are locked); OIDC deploys with no stored keys, from `main` only via the `production` environment |

Accepted cdk-nag exceptions and their reasons are in [`infra/nag.py`](../infra/nag.py).

## Optional hardening

1. **Customer-managed key:** set `useCustomerManagedKey=true` (about $1/month) for revocation and CloudTrail auditing.
2. **Passwordless only:** once everyone has a passkey, drop the password factor or require TOTP for it.
3. **Cognito Plus plan:** adds threat protection (paid).
4. **WAF and custom domain:** add AWS WAF (about $6+/month) and a custom domain with an ACM certificate to enforce TLS 1.2+ on the dashboard.
5. **CI:** done: Actions are pinned to commit SHAs, the runner is pinned, and Dependabot proposes weekly updates.
6. **Deploy role:** done: the OIDC role (`githubDeploy`) trusts only this repo's `production` environment, which allows only `main`, and can only assume the CDK bootstrap roles.

Also: the dashboard is served only to `allowedCountries` (US by default); the API Lambda is capped at 10 concurrent runs; GitHub requires every CI check (admins included) and SHA-pinned actions, and private vulnerability reporting is on.
