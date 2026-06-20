"""courier — shared NauroLabs email send service.

POST /api/send  (function-key auth)
Body: { "to": str|list, "subject": str, "html": str, "text"?: str, "cc"?: list, "bcc"?: list }

Sends via Azure Communication Services Email using a user-assigned managed
identity. Enforces a recipient allowlist so it can never be an open relay.
"""

import json
import logging
import os

import azure.functions as func
from azure.communication.email import EmailClient
from azure.identity import DefaultAzureCredential

app = func.FunctionApp()

MAX_BODY_BYTES = 256 * 1024  # cap payload size (HTML newsletters are small)


def _allowlist() -> list[str]:
    raw = os.environ.get("ALLOWED_RECIPIENTS", "")
    return [a.strip().lower() for a in raw.split(",") if a.strip()]


def _is_allowed(address: str, allow: list[str]) -> bool:
    address = address.strip().lower()
    if not address or "@" not in address:
        return False
    domain = address.split("@", 1)[1]
    for entry in allow:
        if entry.startswith("@"):
            if domain == entry[1:]:
                return True
        elif "@" in entry:
            if address == entry:
                return True
        elif domain == entry:  # bare domain
            return True
    return False


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


@app.route(route="send", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def send(req: func.HttpRequest) -> func.HttpResponse:
    body_bytes = req.get_body() or b""
    if len(body_bytes) > MAX_BODY_BYTES:
        return func.HttpResponse('{"error":"payload too large"}', status_code=413, mimetype="application/json")

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return func.HttpResponse('{"error":"invalid JSON"}', status_code=400, mimetype="application/json")

    to = _as_list(payload.get("to"))
    cc = _as_list(payload.get("cc"))
    bcc = _as_list(payload.get("bcc"))
    subject = (payload.get("subject") or "").strip()
    html = payload.get("html") or ""
    text = payload.get("text") or ""

    if not to or not subject or not (html or text):
        return func.HttpResponse(
            '{"error":"to, subject and html|text are required"}',
            status_code=400,
            mimetype="application/json",
        )

    # Open-relay guard: every recipient must match the allowlist.
    allow = _allowlist()
    if not allow:
        logging.error("ALLOWED_RECIPIENTS not configured — refusing to send.")
        return func.HttpResponse('{"error":"server not configured"}', status_code=500, mimetype="application/json")

    for address in to + cc + bcc:
        if not _is_allowed(address, allow):
            logging.warning("Rejected recipient outside allowlist (domain only logged).")
            return func.HttpResponse(
                '{"error":"recipient not allowed"}', status_code=403, mimetype="application/json"
            )

    endpoint = os.environ["ACS_ENDPOINT"]
    sender = os.environ["SENDER_ADDRESS"]
    client_id = os.environ.get("AZURE_CLIENT_ID")

    credential = DefaultAzureCredential(managed_identity_client_id=client_id) if client_id else DefaultAzureCredential()
    email_client = EmailClient(endpoint, credential)

    message = {
        "senderAddress": sender,
        "recipients": {
            "to": [{"address": a} for a in to],
            "cc": [{"address": a} for a in cc],
            "bcc": [{"address": a} for a in bcc],
        },
        "content": {
            "subject": subject,
            "plainText": text,
            "html": html,
        },
    }

    try:
        poller = email_client.begin_send(message)
        result = poller.result()
    except Exception:  # noqa: BLE001 — surface a generic error, log details server-side
        logging.exception("ACS email send failed")
        return func.HttpResponse('{"error":"send failed"}', status_code=502, mimetype="application/json")

    status = result.get("status") if isinstance(result, dict) else getattr(result, "status", "Unknown")
    op_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
    logging.info("Email send status=%s id=%s recipients=%d", status, op_id, len(to))

    return func.HttpResponse(
        json.dumps({"status": str(status), "id": op_id}),
        status_code=202,
        mimetype="application/json",
    )
