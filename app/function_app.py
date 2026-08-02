"""courier — shared NauroLabs email + lightweight feedback service.

POST /api/send            (function-key auth) — send email via ACS.
GET  /api/feedback        (anonymous)         — record a 👍/👎 vote from an email link.
GET  /api/feedback/export (function-key auth) — read a project's votes back (JSONL).

Email sends go through Azure Communication Services using a user-assigned managed
identity, gated by a recipient allowlist so it can never be an open relay. Feedback
votes are appended to a per-project append-blob in the Function's storage account
(same managed identity, Storage Blob Data Owner) — no separate database.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import azure.functions as func
from azure.communication.email import EmailClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

MAX_BODY_BYTES = 256 * 1024  # cap payload size (HTML newsletters are small)

# --- Feedback ledger (append-blob per project) ------------------------------
FEEDBACK_CONTAINER = "feedback"
_FEEDBACK_VERDICTS = ("up", "down")
_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MAX_FEEDBACK_URL = 2048


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
    except Exception as exc:  # noqa: BLE001
        logging.exception("ACS email send failed")
        # The endpoint is key-protected and single-caller, so echoing the ACS error is worth
        # far more than hiding it: without this the only symptom is a bare 502 and the next
        # debugging session starts from zero.
        return func.HttpResponse(
            json.dumps({"error": "send failed", "detail": f"{type(exc).__name__}: {exc}"[:500]}),
            status_code=502,
            mimetype="application/json",
        )

    status = result.get("status") if isinstance(result, dict) else getattr(result, "status", "Unknown")
    op_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
    status_str = str(status)
    # `begin_send().result()` blocks until the operation is terminal; "Succeeded"
    # means ACS accepted the message for delivery. Any other terminal status
    # (Failed/Canceled) is a real failure — surface it as 502 so best-effort
    # callers can log/alert instead of silently treating a 202 as success.
    accepted = status_str.lower() == "succeeded"
    logging.info(
        "Email send status=%s id=%s recipients=%d accepted=%s",
        status_str,
        op_id,
        len(to),
        accepted,
    )

    if str(status).lower() != "succeeded":
        err = result.get("error") if isinstance(result, dict) else getattr(result, "error", None)
        logging.error("ACS email send did not succeed: status=%s id=%s error=%s", status, op_id, err)
        return func.HttpResponse(
            json.dumps({"error": "send failed", "status": str(status), "detail": str(err)[:500] if err else None}),
            status_code=502,
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps({"status": status_str, "id": op_id}),
        status_code=202 if accepted else 502,
        mimetype="application/json",
    )


# --- Feedback ledger --------------------------------------------------------


def _blob_service() -> BlobServiceClient:
    """BlobServiceClient for the Function's storage account (managed-identity auth)."""
    account = os.environ["AzureWebJobsStorage__accountName"]
    client_id = os.environ.get("AZURE_CLIENT_ID")
    credential = (
        DefaultAzureCredential(managed_identity_client_id=client_id)
        if client_id
        else DefaultAzureCredential()
    )
    return BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential)


def _feedback_line(project: str, verdict: str, url: str) -> str:
    """One JSONL row for a vote (UTC-timestamped)."""
    return (
        json.dumps(
            {
                "project": project,
                "verdict": verdict,
                "url": url,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        + "\n"
    )


def _append_feedback(project: str, verdict: str, url: str) -> None:
    """Append one vote to feedback/{project}.jsonl, creating the container/blob if new."""
    service = _blob_service()
    container = service.get_container_client(FEEDBACK_CONTAINER)
    try:
        container.create_container()
    except ResourceExistsError:
        pass
    blob = container.get_blob_client(f"{project}.jsonl")
    if not blob.exists():
        try:
            blob.create_append_blob()
        except ResourceExistsError:
            pass
    blob.append_block(_feedback_line(project, verdict, url).encode("utf-8"))


def _read_feedback(project: str) -> str:
    """Return the raw JSONL for a project, or '' if it has no votes yet."""
    blob = _blob_service().get_blob_client(FEEDBACK_CONTAINER, f"{project}.jsonl")
    try:
        return blob.download_blob().readall().decode("utf-8")
    except ResourceNotFoundError:
        return ""


@app.route(route="feedback", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def feedback(req: func.HttpRequest) -> func.HttpResponse:
    """Record a 👍/👎 vote clicked from an email link. Anonymous, write-only."""
    project = (req.params.get("p") or "").strip().lower()
    verdict = (req.params.get("v") or "").strip().lower()
    url = (req.params.get("u") or "").strip()
    if (
        not _PROJECT_RE.match(project)
        or verdict not in _FEEDBACK_VERDICTS
        or not url.startswith(("http://", "https://"))
        or len(url) > MAX_FEEDBACK_URL
    ):
        return func.HttpResponse(
            "<!DOCTYPE html><html><body><h1>Invalid feedback link</h1></body></html>",
            status_code=400,
            mimetype="text/html",
        )

    try:
        _append_feedback(project, verdict, url)
    except Exception:  # noqa: BLE001 — best-effort; log details server-side
        logging.exception("feedback record failed")
        return func.HttpResponse(
            "<!DOCTYPE html><html><body><h1>Could not record — try again later</h1></body></html>",
            status_code=502,
            mimetype="text/html",
        )

    emoji = "👍" if verdict == "up" else "👎"
    return func.HttpResponse(
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,-apple-system,"
        "'Segoe UI',Roboto,sans-serif;text-align:center;padding:3rem;\">"
        f"<h1>{emoji} Thanks — recorded</h1><p>You can close this tab.</p></body></html>",
        mimetype="text/html",
    )


@app.route(route="feedback/export", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def feedback_export(req: func.HttpRequest) -> func.HttpResponse:
    """Return a project's recorded votes as JSONL (function-key auth)."""
    project = (req.params.get("p") or "").strip().lower()
    if not _PROJECT_RE.match(project):
        return func.HttpResponse(
            '{"error":"invalid project"}', status_code=400, mimetype="application/json"
        )
    try:
        data = _read_feedback(project)
    except Exception:  # noqa: BLE001 — best-effort; log details server-side
        logging.exception("feedback export failed")
        return func.HttpResponse(
            '{"error":"read failed"}', status_code=502, mimetype="application/json"
        )
    return func.HttpResponse(data, status_code=200, mimetype="application/x-ndjson")
