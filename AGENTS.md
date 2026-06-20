# AGENTS.md — courier

> Instructions for AI agents working in the `courier` project. Read with the lab-wide [PLATFORM.md](../.github/PLATFORM.md).

## What this project is

The shared NauroLabs **email/notification service**. A small Python Function App that exposes `POST /api/send` and relays the message through **Azure Communication Services (ACS) Email** using a **user-assigned managed identity**. One ACS resource serves the whole lab.

## Golden-path position

| Decision | Choice | Note |
|---|---|---|
| Hosting | **Functions standalone** (Flex Consumption) | No UI — it's a server-to-server API, like agentMode/mindMe. |
| Auth (callers) | **Function key** (`?code=`) | Internal server-to-server; key stored as a secret in each caller. |
| Auth (to ACS) | **Managed identity / Entra** | `DefaultAzureCredential`; no connection strings in code. |
| Secrets | Managed identity; function key is the only shared secret | No Key Vault needed for MVP. |
| Infra | **Bicep**, resource-group scope (`courier-rg`) | App Insights, 30d/1GB cap. |
| Region | `northeurope` (Function) · ACS `dataLocation: Europe` | |

## Hard rules

1. **Never an open relay.** The send endpoint MUST enforce the `ALLOWED_RECIPIENTS` allowlist. Do not remove or weaken this guard.
2. **No connection strings or ACS keys in code or committed files.** Use the managed identity. `disableLocalAuth` may be considered later.
3. **No PII in logs.** Log operation IDs and status, not email bodies or full recipient lists.
4. **Validate input at the boundary.** Reject malformed/oversized payloads; cap body size.
5. **Cost discipline.** Keep it on Flex Consumption + a single ACS resource. No premium tiers.

## RBAC note

The managed identity is granted **Contributor** scoped to the ACS resource only — this is the documented ACS Entra data-plane path (there is no narrower built-in "email sender" data role today). Tighten if/when Microsoft ships a finer role.

## First consumer

The personal **Copilot Dispatch** newsletter — its config and content live in **mindVault** (personal vault), and it calls courier only for delivery. Never copy personal/work newsletter content into this repo.
