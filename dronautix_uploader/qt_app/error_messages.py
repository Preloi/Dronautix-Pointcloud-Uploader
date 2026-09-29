"""UI-free translation of technical exceptions into actionable German messages."""

from __future__ import annotations

_S3_ERROR_MESSAGES = {
    "AccessDenied": "Zugriff verweigert. Bitte AWS-Zugangsdaten und Berechtigungen für den Bucket prüfen.",
    "403": "Zugriff verweigert. Bitte AWS-Zugangsdaten und Berechtigungen für den Bucket prüfen.",
    "InvalidAccessKeyId": "Der AWS Access Key ist ungültig. Bitte in den Einstellungen prüfen.",
    "SignatureDoesNotMatch": "Der AWS Secret Key passt nicht zum Access Key. Bitte in den Einstellungen prüfen.",
    "NoSuchBucket": "Der S3-Bucket existiert nicht. Bitte den Bucket-Namen in den Einstellungen prüfen.",
    "404": "Das angeforderte S3-Objekt oder der Bucket wurde nicht gefunden.",
    "RequestTimeTooSkewed": "Die Systemuhr dieses Rechners weicht zu stark ab. Bitte Datum und Uhrzeit synchronisieren.",
    "ExpiredToken": "Die AWS-Anmeldung ist abgelaufen. Bitte die Zugangsdaten erneuern.",
    "SlowDown": "AWS drosselt die Anfragen gerade. Bitte in einigen Minuten erneut versuchen.",
}

_NETWORK_ERROR_CLASSES = {
    "EndpointConnectionError",
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "ConnectionClosedError",
    "ProxyConnectionError",
    "SSLError",
    "URLError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionRefusedError",
    "TimeoutError",
    "timeout",
}


def describe_error(error: BaseException) -> str:
    """Return a user-facing message; unknown errors keep their own text.

    Messages the app raises itself are already German and actionable, so only
    well-known technical AWS, network and HTTP failures are rewritten.
    """

    s3_code = _s3_error_code(error)
    if s3_code and s3_code in _S3_ERROR_MESSAGES:
        return _S3_ERROR_MESSAGES[s3_code]
    http_code = getattr(error, "code", None)
    if error.__class__.__name__ == "HTTPError" and isinstance(http_code, int):
        if http_code == 404:
            return "Die Update-Datei wurde auf GitHub nicht gefunden (HTTP 404). Bitte später erneut versuchen."
        return f"Der Server hat mit HTTP {http_code} geantwortet. Bitte später erneut versuchen."
    # Only the top-level error: the app's own wrappers (e.g. rollback reports
    # listing orphaned S3 keys) must never be replaced by a generic text.
    if error.__class__.__name__ in _NETWORK_ERROR_CLASSES:
        return (
            "Keine Verbindung zum Server. Bitte Internetverbindung, Proxy oder Firewall prüfen "
            "und den Vorgang erneut starten."
        )
    return str(error) or error.__class__.__name__


def technical_details(error: BaseException) -> str:
    """Original error text for the activity log, only when it differs from the summary."""

    original = str(error) or error.__class__.__name__
    return "" if original == describe_error(error) else f"{error.__class__.__name__}: {original}"


def _s3_error_code(error: BaseException) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code", "") or "")
    return ""


__all__ = ["describe_error", "technical_details"]
