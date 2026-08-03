# courier

> **Shared NauroLabs email/notification service.** Any project or agent POSTs a message; courier sends it via **Azure Communication Services (ACS) Email** using a managed identity. One ACS resource and one sender domain for the whole lab.

## Why this exists

NauroLabs had no way to send email. `courier` is the shared capability so projects don't each stand up their own mailer. It follows the [golden path](../.github/PLATFORM.md): managed identity (no connection strings in code), Bicep infra, App Insights, cost discipline.

First consumer: the personal **Copilot Dispatch** weekly newsletter (config lives in mindVault, delivery goes through courier).

## What it is / isn't

- **Is:** a small HTTP service (`POST /api/send`) backed by ACS Email, plus a lightweight feedback ledger. Server-to-server only, function-key auth, recipient allowlist.
- **Isn't:** a marketing/bulk platform, a template engine, or anything user-facing. Callers send ready-to-go HTML.

## API

### `POST /api/send?code=<function-key>` — send an email

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

### `GET /api/feedback` — record a 👍/👎 vote (anonymous)

Embed these links in your HTML email. When a recipient clicks one, their vote is appended to a per-project append-blob in Azure Blob Storage and they see a simple confirmation page.

| Parameter | Description |
|---|---|
| `p` | Project name (`[a-z0-9][a-z0-9-]{0,31}`) |
| `v` | Verdict: `up` or `down` |
| `u` | The canonical URL of the email/page being rated (must start with `http://` or `https://`, max 2048 chars) |

Example links to embed in HTML:

```html
<a href="https://<functionHost>/api/feedback?p=dispatch&v=up&u=https%3A%2F%2Fyour-newsletter-url">👍</a>
<a href="https://<functionHost>/api/feedback?p=dispatch&v=down&u=https%3A%2F%2Fyour-newsletter-url">👎</a>
```

Returns an HTML confirmation page (200) or a 400/502 on error. No function key required — designed for recipient clicks.

### `GET /api/feedback/export?code=<function-key>&p=<project>` — export votes

Returns all recorded votes for a project as JSONL (`application/x-ndjson`), one JSON object per line:

```jsonc
{"project":"dispatch","verdict":"up","url":"https://…","ts":"2026-01-15T10:00:00+00:00"}
{"project":"dispatch","verdict":"down","url":"https://…","ts":"2026-01-15T11:23:00+00:00"}
```

Returns an empty body (200) when no votes have been recorded yet.

**Feedback storage** — votes are appended to `feedback/<project>.jsonl` in the Function's storage account using the managed identity (Storage Blob Data Owner). No separate database.

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

email recipient
   │  GET /api/feedback?p=…&v=up|down&u=…  (anonymous click from email link)
   ▼
Function App
   │  BlobServiceClient(DefaultAzureCredential)  ← same managed identity
   ▼
Azure Blob Storage — feedback/<project>.jsonl  (append-blob, private)

caller (project / GitHub Action)
   │  GET /api/feedback/export?code=…&p=…  (function key)
   ▼
Function App → returns raw JSONL from the append-blob
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

The feedback endpoints use `AzureWebJobsStorage__accountName` to locate the storage account. `local.settings.json.example` ships with `"AzureWebJobsStorage": "UseDevelopmentStorage=true"` which routes to the [Azurite](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azurite) local emulator — start it alongside `func start` for end-to-end local feedback testing.

## Testing

```powershell
cd app
pip install pytest azure-functions azure-communication-email azure-identity azure-storage-blob
pytest tests/ -v
```

All feedback helpers and HTTP endpoints are unit-tested with mocked storage; no Azure resources required.

## Status

MVP scaffold. Not yet deployed — see [DEPLOY.md](./DEPLOY.md) for the one-time provisioning steps.
