from urllib import error

import pytest

from bot.binance_transport import (
    BinanceRequestConfigError,
    BinanceRequestSigner,
    BinanceTransport,
    BinanceTransportError,
    SignedAdapterRequest,
)
from bot.exchange_adapter import AdapterCredentials, PreparedAdapterRequest


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
        super().__init__(url="https://fapi.binance.com/fapi/v1/order", code=code, msg="bad request", hdrs=None, fp=None)
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body


def _credentials() -> AdapterCredentials:
    return AdapterCredentials(
        venue="binance_usdt_perp",
        api_key_env="BINANCE_API_KEY",
        api_secret_env="BINANCE_API_SECRET",
        recv_window_ms=5000,
        timeout_sec=12.5,
        proxy_url="http://127.0.0.1:7897",
        api_base_url="https://fapi.binance.com",
    )


def test_signer_adds_signature_headers_and_auth_params() -> None:
    signer = BinanceRequestSigner(
        _credentials(),
        env_getter=lambda key: {"BINANCE_API_KEY": "key123", "BINANCE_API_SECRET": "secret456"}.get(key),
        clock=lambda: 1714132800000,
    )
    prepared = PreparedAdapterRequest(
        method="POST",
        path="/fapi/v1/order",
        params={"symbol": "ETHUSDT", "side": "BUY", "newClientOrderId": "abc"},
        idempotency_key="abc",
    )

    signed = signer.sign(prepared)

    assert signed.headers == {"X-MBX-APIKEY": "key123"}
    assert signed.params["timestamp"] == 1714132800000
    assert signed.params["recvWindow"] == 5000
    assert signed.params["signature"] == "6664b21c4e78a4f1524c7adfd684b3a2a3be07dcc736de57e3d2923d75abeda1"
    assert signed.url == "https://fapi.binance.com/fapi/v1/order"


def test_signer_skips_signature_for_public_request() -> None:
    signer = BinanceRequestSigner(_credentials(), env_getter=lambda key: None, clock=lambda: 1714132800000)
    prepared = PreparedAdapterRequest(method="GET", path="/fapi/v1/ping", requires_auth=False)

    signed = signer.sign(prepared)

    assert signed.headers == {}
    assert signed.params == {}


def test_signer_requires_env_vars_for_auth_request() -> None:
    signer = BinanceRequestSigner(_credentials(), env_getter=lambda key: None)

    with pytest.raises(BinanceRequestConfigError, match="BINANCE_API_KEY"):
        signer.sign(PreparedAdapterRequest(method="GET", path="/fapi/v1/userTrades", params={"symbol": "ETHUSDT"}))


def test_transport_sends_request_with_proxy_and_timeout() -> None:
    fake_opener = FakeOpener(response=FakeResponse(body='{"ok": true}', status=200))
    transport = BinanceTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)
    signed = SignedAdapterRequest(
        method="GET",
        path="/fapi/v1/userTrades",
        url="https://fapi.binance.com/fapi/v1/userTrades",
        headers={"X-MBX-APIKEY": "key123"},
        params={"symbol": "ETHUSDT", "timestamp": 1, "signature": "sig"},
    )

    response = transport.send(signed)

    assert response.http_status == 200
    assert response.payload == {"ok": True}
    assert fake_opener.last_timeout == 12.5
    assert fake_opener.last_request.full_url == "https://fapi.binance.com/fapi/v1/userTrades?symbol=ETHUSDT&timestamp=1&signature=sig"


def test_transport_maps_http_error_to_transport_error() -> None:
    fake_opener = FakeOpener(exc=FakeHttpError(code=400, body='{"code": -1102, "msg": "bad param"}'))
    transport = BinanceTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)

    with pytest.raises(BinanceTransportError) as exc_info:
        transport.send(SignedAdapterRequest(method="GET", path="/fapi/v1/userTrades", url="https://fapi.binance.com/fapi/v1/userTrades"))

    assert exc_info.value.kind == "http_error"
    assert exc_info.value.http_status == 400
    assert exc_info.value.payload == {"code": -1102, "msg": "bad param"}


def test_transport_maps_malformed_json_to_transport_error() -> None:
    fake_opener = FakeOpener(response=FakeResponse(body="not-json", status=200))
    transport = BinanceTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)

    with pytest.raises(BinanceTransportError) as exc_info:
        transport.send(SignedAdapterRequest(method="GET", path="/fapi/v1/userTrades", url="https://fapi.binance.com/fapi/v1/userTrades"))

    assert exc_info.value.kind == "json_error"


def test_transport_maps_timeout_to_transport_error() -> None:
    fake_opener = FakeOpener(exc=TimeoutError("timed out"))
    transport = BinanceTransport(_credentials(), opener_factory=lambda *handlers: fake_opener)

    with pytest.raises(BinanceTransportError) as exc_info:
        transport.send(SignedAdapterRequest(method="GET", path="/fapi/v1/userTrades", url="https://fapi.binance.com/fapi/v1/userTrades"))

    assert exc_info.value.kind == "timeout"
