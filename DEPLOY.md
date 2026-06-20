# DEPLOY — courier

One-time provisioning of the shared email service. **Not yet run** — these are the steps to stand up ACS Email v1.

## Prerequisites
- `az` logged in to the Visual Studio Enterprise subscription.
- Your inbox address for the recipient allowlist.

## 1. Provision infrastructure

```powershell
# Recipient allowlist (open-relay guard) — your inbox or a domain
$env:COURIER_ALLOWED_RECIPIENTS = 'you@example.com'

az group create -n courier-rg -l northeurope

az deployment group create -g courier-rg `
  -f infrastructure/main.bicep `
  -p infrastructure/main.bicepparam
```

The deployment outputs `acsEndpoint`, `senderAddress`, `functionAppName`, and `managedIdentityClientId`. The Azure-managed sender domain (`donotreply@<guid>.azurecomm.net`) is created automatically — no DNS needed.

## 2. Deploy the function code

```powershell
cd app
func azure functionapp publish <functionAppName>   # from deployment output
```

## 3. Get the function key (store as a caller secret)

```powershell
az functionapp keys list -g courier-rg -n <functionAppName> --query "functionKeys.default" -o tsv
```

Store this as a secret in each caller (e.g. a GitHub Actions secret `COURIER_KEY`). Never commit it.

## 4. Smoke test

```powershell
$key = az functionapp keys list -g courier-rg -n <functionAppName> --query "functionKeys.default" -o tsv
$host = az functionapp show -g courier-rg -n <functionAppName> --query defaultHostName -o tsv
$body = @{ to='you@example.com'; subject='courier smoke test'; html='<p>It works.</p>' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://$host/api/send?code=$key" -ContentType 'application/json' -Body $body
```

A `202` with `{ status: "Succeeded", id: ... }` means email is live.

## Notes
- **Custom branded sender** (`dispatch.naurolabs.com`) is a later upgrade: add a custom domain to the Email Communication Service, verify the TXT/SPF/DKIM records in Google Cloud DNS, then swap `SENDER_ADDRESS`.
- The managed identity has **Contributor** on the ACS resource only (documented ACS Entra data-plane path).
