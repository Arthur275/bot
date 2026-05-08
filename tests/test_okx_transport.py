import base64
import hashlib
import hmac
from datetime import UTC, datetime
from urllib import error

import pytest

from bot.exchange_adapter import AdapterCredentials, PreparedAdapterRequest
from bot.okx_transport import OkxRequestConfigError, OkxRequestSigner, OkxTransport, OkxTransportError


class FakeResponse:
    def __init__(self, *, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._body = body.encode("utf-8")
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FakeOpener:
    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc
        self.last_request = None
        self.last_timeout = None

    def open(self, req, timeout):
        self.last_request = req
        self.last_timeout = timeout
        if self._exc is not None:
            raise self._exc
        return self._response


class FakeHttpError(error.HTTPError):
    def __init__(self, *, code: int, body: str) -> None:
        super().__init__(url="https://www.okx.com/api/v5/trade/order", code=code, msg="bad request", hdrs=None, fp=None)
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body


def _credentials() -> AdapterCredentials:
    return AdapterCredentials(
        venue="okx_usdt_swap",
        api_key_env="OKX_API_KEY",
        api_secret_env="OKX_API_SECRET",
        api_passphrase_env="OKX_API_PASSPHRASE",
        recv_window_ms=60000,
        timeout_sec=12.5,
        proxy_url="http://127.0.0.1:7897",
        api_base_url="https://www.okx.com",
    )


def test_signer_adds_okx_auth_headers_and_json_body() -> None:
    timestamp = datetime(2026, 5, 7, 1, 2, 3, 456000, tzinfo=UTC)
    signer = OkxRequestSigner(
        _credentials(),
        env_getter=lambda key: {
            "OKX_API_KEY": "key123",
            "OKX_API_SECRET": "secret456",
            "OKX_API_PASSPHRASE": "pass789",
        }.get(key),
        clock=lambda: timestamp,
    )
    prepared = PreparedAdapterRequest(
        method="POST",
        path="/api/v5/trade/order",
        body={"instId": "ETH-USDT-SWAP", "tdMode": "cross", "side": "buy", "ordType": "market", "sz": "1"},
        idempotency_key="abc",
    )

    signed = signer.sign(prepared)

    body_text = '{"instId":"ETH-USDT-SWAP","tdMode":"cross","side":"buy","ordType":"market","sz":"1"}'
    prehash = f"2026-05-07T01:02:03.456ZPOST/api/v5/trade/order{body_text}"
    expected_signature = base64.b64encode(hmac.new(b"secret456", prehash.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
    assert signed.headers["OK-ACCESS-KEY"] == "key123"
    assert signed.headers["OK-ACCESS-PASSPHRASE"] == "pass789"
    assert signed.headers["OK-ACCESS-TIMESTAMP"] == "2026-05-07T01:02:03.456Z"
    assert signed.headers["OK-ACCESS-SIGN"] == expected_signature
    assert signed.body["sz"] == "1"
    assert signed.url == "https://www.okx.com/api/v5/trade/order"


def test_signer_includes_get_query_in_prehash_and_omits_body() -> None:
    signer = OkxRequestSigner(
        _credentials(),
        env_getter=lambda key: {
            "OKX_API_KEY": "key123",
            "OKX_API_SECRET": "secret456",
            "OKX_API_PASSPHRASE": "pass789",
        }.get(key),
        clock=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=UTC),
    )

    signed = signer.sign(
        PreparedAdapterRequest(
            method="GET",
            path="/api/v5/account/positions",
            params={"instId": "ETH-USDT-SWAP"},
            body={"ignored": True},
        )
    )

    prehash = "2026-05-07T01:02:03.000ZGET/api/v5/account/positions?instId=ETH-USDT-SWAP"
    expected_signature = base64.b64encode(hmac.new(b"secret456", prehash.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
    assert signed.headers["OK-ACCESS-SIGN"] == expected_signature
    assert signed.body == {}


def test_signer_requires_passphrase_for_auth_request() -> None:
    signer = OkxRequestSigner(_credentials(), env_getter=lambda key: None)

    with pytest.raises(OkxRequestConfigError, match="OKX_API_KEY"):
        signer.sign(PreparedAdapterRequest(method="GET", path="/api/v5/account/positions"))


def test_transport_sends_json_body_with_proxy_and_timeout() -> None:
    fake_opener = FakeOpener(response=FakeResponse(body='{"code":"0","data":[{"ordId":"1"}]}', status=200))
    transport = OkxTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)
    signed = OkxRequestSigner(
        _credentials(),
        env_getter=lambda key: {
            "OKX_API_KEY": "key123",
            "OKX_API_SECRET": "secret456",
            "OKX_API_PASSPHRASE": "pass789",
        }.get(key),
        clock=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=UTC),
    ).sign(
        PreparedAdapterRequest(
            method="POST",
            path="/api/v5/trade/order",
            body={"instId": "ETH-USDT-SWAP", "tdMode": "cross"},
        )
    )

    response = transport.send(signed)

    assert response.payload == {"code": "0", "data": [{"ordId": "1"}]}
    assert fake_opener.last_timeout == 12.5
    assert fake_opener.last_request.full_url == "https://www.okx.com/api/v5/trade/order"
    assert fake_opener.last_request.data == b'{"instId":"ETH-USDT-SWAP","tdMode":"cross"}'


def test_transport_rejects_okx_nonzero_top_level_code() -> None:
    fake_opener = FakeOpener(response=FakeResponse(body='{"code":"51000","msg":"Parameter error","data":[]}', status=200))
    transport = OkxTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)

    with pytest.raises(OkxTransportError) as exc_info:
        transport.send(
            OkxRequestSigner(
                _credentials(),
                env_getter=lambda key: {
                    "OKX_API_KEY": "key123",
                    "OKX_API_SECRET": "secret456",
                    "OKX_API_PASSPHRASE": "pass789",
                }.get(key),
                clock=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=UTC),
            ).sign(PreparedAdapterRequest(method="GET", path="/api/v5/account/positions"))
        )

    assert exc_info.value.kind == "http_error"
    assert exc_info.value.payload["code"] == "51000"


def test_transport_rejects_okx_nonzero_sub_code() -> None:
    fake_opener = FakeOpener(response=FakeResponse(body='{"code":"0","data":[{"sCode":"51008","sMsg":"insufficient balance"}]}', status=200))
    transport = OkxTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)

    with pytest.raises(OkxTransportError) as exc_info:
        transport.send(
            OkxRequestSigner(
                _credentials(),
                env_getter=lambda key: {
                    "OKX_API_KEY": "key123",
                    "OKX_API_SECRET": "secret456",
                    "OKX_API_PASSPHRASE": "pass789",
                }.get(key),
                clock=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=UTC),
            ).sign(PreparedAdapterRequest(method="POST", path="/api/v5/trade/order", body={"instId": "ETH-USDT-SWAP"}))
        )

    assert exc_info.value.kind == "http_error"
    assert "insufficient balance" in str(exc_info.value)


def test_transport_maps_http_error_to_transport_error() -> None:
    fake_opener = FakeOpener(exc=FakeHttpError(code=400, body='{"code":"51000","msg":"bad param","data":[]}'))
    transport = OkxTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)

    with pytest.raises(OkxTransportError) as exc_info:
        transport.send(
            OkxRequestSigner(
                _credentials(),
                env_getter=lambda key: {
                    "OKX_API_KEY": "key123",
                    "OKX_API_SECRET": "secret456",
                    "OKX_API_PASSPHRASE": "pass789",
                }.get(key),
                clock=lambda: datetime(2026, 5, 7, 1, 2, 3, tzinfo=UTC),
            ).sign(PreparedAdapterRequest(method="GET", path="/api/v5/account/positions"))
        )

    assert exc_info.value.kind == "http_error"
    assert exc_info.value.http_status == 400
    assert exc_info.value.payload == {"code": "51000", "msg": "bad param", "data": []}
