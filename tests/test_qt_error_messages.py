import urllib.error

from dronautix_uploader.qt_app.error_messages import describe_error, technical_details


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(f"An error occurred ({code}) when calling the PutObject operation: Access Denied")
        self.response = {"Error": {"Code": code}}


class EndpointConnectionError(Exception):
    pass


def test_known_s3_error_codes_become_actionable_german_messages():
    message = describe_error(FakeClientError("AccessDenied"))

    assert message.startswith("Zugriff verweigert")
    assert "AccessDenied" in technical_details(FakeClientError("AccessDenied"))


def test_network_errors_are_translated_but_own_wrappers_keep_their_details():
    network = EndpointConnectionError('Could not connect to the endpoint URL: "https://s3..."')
    assert describe_error(network).startswith("Keine Verbindung zum Server")

    try:
        try:
            raise network
        except EndpointConnectionError as inner:
            raise RuntimeError("Upload fehlgeschlagen; verwaiste S3-Keys: a, b") from inner
    except RuntimeError as outer:
        assert describe_error(outer) == "Upload fehlgeschlagen; verwaiste S3-Keys: a, b"


def test_http_404_and_urllib_errors_are_translated():
    http_error = urllib.error.HTTPError("https://github.com/x", 404, "Not Found", {}, None)
    assert "HTTP 404" in describe_error(http_error)
    assert describe_error(urllib.error.URLError("getaddrinfo failed")).startswith("Keine Verbindung")


def test_own_german_messages_are_kept_unchanged():
    error = ValueError("Kunde darf nicht leer sein.")

    assert describe_error(error) == "Kunde darf nicht leer sein."
    assert technical_details(error) == ""
