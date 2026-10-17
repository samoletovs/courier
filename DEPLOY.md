# DEPLOY — courier

The shared email service is **provisioned and live**. These instructions document how to update or recreate the deployment.

## Live deployment (verified 2026-10-12)

- Resource group: `courier-rg`
- Function App: `func-courier-c0ur1er` (`Running`)
- Flex Consumption plan: `plan-courier-c0ur1er`
- Azure Communication Services: `acs-courier-c0ur1er`
- Email Communication Services: `acs-email-courier-c0ur1er`
- Sender domains: `mail.naurolabs.com` and `AzureManagedDomain`
- Storage: `stcourierc0ur1er`
- Identity and monitoring: `id-courier`, `appi-courier`, and `log-courier`

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

The recipient allowlist parameter is marked secure and provisioned as the `allowed-recipients` Key Vault secret. The Function App receives only a Key Vault reference, resolved with its user-assigned managed identity and the **Key Vault Secrets User** role. Keep the allowlist value private when setting `COURIER_ALLOWED_RECIPIENTS`; local development continues to use `ALLOWED_RECIPIENTS` in `local.settings.json`.

## 2. Deploy the function code

```powershell
cd app
func azure functionapp publish <functionAppName>   # from deployment output
```

## 3. Get function-specific keys

```powershell
$sendKey = az functionapp function keys list -g courier-rg -n <functionAppName> `
  --function-name send --query "default" -o tsv
$recipientsKey = az functionapp function keys list -g courier-rg -n <functionAppName> `
  --function-name recipients --query "default" -o tsv
```

Store `$sendKey` as each caller's secret (for example, `COURIER_KEY`). Keep `$recipientsKey` operator-only. Never distribute the app-level host key: it authorizes both sending and allowlist mutation. Rotate any host key that was previously shared with callers.

## 4. Smoke test

```powershell
$host = az functionapp show -g courier-rg -n <functionAppName> --query defaultHostName -o tsv
$body = @{ to='you@example.com'; subject='courier smoke test'; html='<p>It works.</p>' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://$host/api/send?code=$sendKey" -ContentType 'application/json' -Body $body
```

A `202` with `{ status: "Succeeded", id: ... }` means email is live.

## Notes
- **Custom branded sender** (`dispatch.naurolabs.com`) is a later upgrade: add a custom domain to the Email Communication Service, verify the TXT/SPF/DKIM records in Google Cloud DNS, then swap `SENDER_ADDRESS`.
- The managed identity has **Contributor** on the ACS resource only (documented ACS Entra data-plane path).
