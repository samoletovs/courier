# courier

> **Shared NauroLabs email/notification service.** Any project or agent POSTs a message; courier sends it via **Azure Communication Services (ACS) Email** using a managed identity. One ACS resource and one sender domain for the whole lab.

## Why this exists

NauroLabs had no way to send email. `courier` is the shared capability so projects don't each stand up their own mailer. It follows the [golden path](../.github/PLATFORM.md): managed identity (no connection strings in code), Bicep infra, App Insights, cost discipline.

First consumer: the personal **Copilot Dispatch** weekly newsletter (config lives in mindVault, delivery goes through courier).

## What it is / isn't

- **Is:** a small HTTP service (`POST /api/send`) backed by ACS Email. Server-to-server only, function-key auth, recipient allowlist.
- **Isn't:** a marketing/bulk platform, a template engine, or anything user-facing. Callers send ready-to-go HTML.

## API

`POST /api/send?code=<function-key>`

```jsonc
{
  "to": "you@example.com",          // string or array
  "subject": "The Copilot Dispatch — Vol. I No. 2",
  "html": "<h1>…</h1>",             // HTML body
  "text": "plain text fallback",     // optional
  "cc": [],                          // optional
  "bcc": []                          // optional
}
```

Response: `{ "status": "Succeeded", "id": "<operation-id>" }`.

**Guardrails**
- Recipients must match `ALLOWED_RECIPIENTS` (comma-separated emails/domains). Anything else is rejected — courier is **not** an open relay.
- `code` (function key) required. Store it as a secret in each caller (e.g. a GitHub Actions secret).

## Architecture

```
caller (project / GitHub Action)
   │  POST /api/send  (function key)
   ▼
Function App (Flex Consumption, Python)
   │  EmailClient(endpoint, DefaultAzureCredential)   ← user-assigned managed identity
   ▼
Azure Communication Services — Email (Azure-managed domain)
   →  donotreply@<guid>.azurecomm.net
```

## Infrastructure

`infrastructure/main.bicep` (resource-group scope, `courier-rg`) provisions:
- User-assigned managed identity
- Log Analytics + Application Insights (30-day retention, 1 GB/day cap)
- Storage account (Function host)
- **Communication Services** + **Email Communication Services** + **Azure-managed domain** + `donotreply` sender
- Function App (Flex Consumption, Python 3.11)
- Role assignment: **Contributor** on the ACS resource → the managed identity (ACS Entra data-plane path)

Deploy: see [DEPLOY.md](./DEPLOY.md).

## Cost

ACS has **no monthly base fee**. Email is ~$0.00025/email + ~$0.00012/MB. A weekly newsletter to one inbox is **fractions of a cent/month**. Function runs on Flex Consumption free grant. Effective cost ≈ €0.

## Local dev

```powershell
cd app
cp local.settings.json.example local.settings.json   # fill ACS_ENDPOINT, SENDER_ADDRESS, ALLOWED_RECIPIENTS
func start
```
`az login` provides the credential locally (your account needs Contributor on the ACS resource).

## Status

MVP scaffold. Not yet deployed — see [DEPLOY.md](./DEPLOY.md) for the one-time provisioning steps.
