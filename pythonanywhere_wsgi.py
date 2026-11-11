import json
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs

from fastapi import HTTPException

from app.routers.webhook import process_webhook_payload
from app.services.setup_service import init_all_sheets


def _response(start_response, status: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    start_response(status, [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ])
    return [body]


def _payload_from_environ(environ) -> dict:
    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    if content_length > 1_000_000:
        raise ValueError("Request body too large")
    body = environ["wsgi.input"].read(content_length) if content_length else b""
    content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
    payload = {}
    if body and content_type == "application/json":
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict):
            payload.update(parsed)
    elif body and content_type == "multipart/form-data":
        raw_content_type = environ.get("CONTENT_TYPE", "")
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {raw_content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
        )
        for part in message.iter_parts():
            field_name = part.get_param("name", header="content-disposition")
            if not field_name or part.get_content_disposition() != "form-data":
                continue
            value = part.get_content()
            if isinstance(value, bytes):
                charset = part.get_content_charset() or "utf-8"
                value = value.decode(charset, errors="replace")
            payload[field_name] = str(value)
    elif body:
        form_values = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        payload.update({key: values[-1] for key, values in form_values.items() if values})
    query_values = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    for key, values in query_values.items():
        if values:
            payload.setdefault(key, values[-1])
    return payload


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"

    if method in {"GET", "HEAD"} and path in {"/", "/health"}:
        return _response(start_response, "200 OK", {"status": "ok", "message": "Bot Agrinova Aktif"})

    if method == "GET" and path == "/setup":
        try:
            init_all_sheets()
            return _response(start_response, "200 OK", {"status": "success", "message": "Setup sheets selesai."})
        except Exception as error:
            return _response(start_response, "500 Internal Server Error", {"status": "error", "message": str(error)})

    if method == "POST" and path == "/webhook":
        try:
            result = process_webhook_payload(_payload_from_environ(environ))
            return _response(start_response, "200 OK", result)
        except HTTPException as error:
            return _response(start_response, f"{error.status_code} {error.detail}", {"detail": error.detail})
        except Exception as error:
            return _response(start_response, "500 Internal Server Error", {"status": "error", "error": str(error)})

    return _response(start_response, "404 Not Found", {"detail": "Not Found"})
