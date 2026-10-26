"""Exercise streaming aborts and a shared collection deadline without networking."""
import httpx
import pytest
from app.core.auth import AuthError, verify_access_token
from app.core.config import Settings
from app.integrations.http_json import InvalidUpstreamJSON, read_json
from app.integrations.pandascore import PandaScoreClient, PandaScoreError


class CountedBody(httpx.SyncByteStream):
    def __init__(self, chunks=100):
        self.chunks, self.reads, self.closed = chunks, 0, False

    def __iter__(self):
        for _ in range(self.chunks):
            self.reads += 1
            yield b" " * 65536

    def close(self):
        self.closed = True


def test_unknown_length_stream_stops_before_entire_body_is_downloaded():
    body = CountedBody()
    with httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, stream=body)
    )) as client:
        with client.stream("GET", "https://example.test/") as response:
            with pytest.raises(InvalidUpstreamJSON):
                read_json(response, 65536)
    assert body.reads == 2 and body.closed


def test_declared_oversize_body_is_rejected_before_reading_bytes():
    body = CountedBody()
    with httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, stream=body, headers={"Content-Length": "10000000"})
    )) as client:
        with client.stream("GET", "https://example.test/") as response:
            with pytest.raises(InvalidUpstreamJSON):
                read_json(response, 65536)
    assert body.reads == 0 and body.closed


def test_oversized_auth_body_returns_sanitized_service_error():
    body = CountedBody()
    with httpx.Client(base_url="https://project.supabase.co", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, stream=body)
    )) as client:
        with pytest.raises(AuthError) as error:
            verify_access_token("header.payload.signature", client=client)
    assert error.value.status_code == 503 and error.value.code == "auth_unavailable"
    assert body.reads == 5 and body.closed


def test_oversized_pandascore_body_is_closed_without_retry():
    body = CountedBody()
    with PandaScoreClient(Settings(_env_file=None,pandascore_token="test-secret"),
            transport=httpx.MockTransport(lambda r: httpx.Response(200,stream=body))) as client:
        with pytest.raises(PandaScoreError) as error:
            client.list_leagues()
        assert error.value.code == "pandascore_invalid_response" and client.requests == 1
    assert body.reads == 77 and body.closed


def test_collection_deadline_is_shared_by_pages_and_endpoint_calls():
    now = [0.]
    calls = []

    def handler(request):
        calls.append(request)
        now[0] = 61.
        return httpx.Response(200,json=[{"id":1}])

    with PandaScoreClient(Settings(_env_file=None,pandascore_token="test-secret",pandascore_page_size=1),
            transport=httpx.MockTransport(handler),clock=lambda:now[0]) as client:
        with pytest.raises(PandaScoreError,match="pandascore_collection_timeout"):
            client.list_leagues()
        with pytest.raises(PandaScoreError,match="pandascore_collection_timeout"):
            client.list_series()
    assert len(calls) == 1


def test_retry_delay_cannot_exceed_remaining_collection_budget():
    now = [0.]
    sleeps = []

    def handler(request):
        now[0] = 59.5
        return httpx.Response(503)

    with PandaScoreClient(Settings(_env_file=None,pandascore_token="test-secret"),
            transport=httpx.MockTransport(handler),clock=lambda:now[0],sleep=sleeps.append) as client:
        with pytest.raises(PandaScoreError,match="pandascore_collection_timeout"):
            client.list_leagues()
    assert not sleeps


def test_compressed_response_is_rejected_before_decompression_or_body_read():
    body = CountedBody()
    with httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, stream=body, headers={"Content-Encoding": "gzip"})
    )) as client:
        with client.stream("GET", "https://example.test/") as response:
            with pytest.raises(InvalidUpstreamJSON):
                read_json(response, 65536)
    assert body.reads == 0 and body.closed


@pytest.mark.parametrize("data", [b'{"id":1,"id":2}', b'{"value":NaN}', b'{"value":Infinity}',
                                  b'{"value":-Infinity}', b'{"value":1e999}', b'{"value":-1e999}'])
def test_ambiguous_or_nonfinite_upstream_json_is_rejected(data):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data))) as client:
        with client.stream("GET", "https://example.test/") as response:
            with pytest.raises(InvalidUpstreamJSON):
                read_json(response, 65536)
