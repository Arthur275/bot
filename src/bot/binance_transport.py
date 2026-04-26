from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
from datetime import UTC, datetime
from typing import Any, Callable, Protocol
from urllib import error, parse, request

from pydantic import BaseModel, ConfigDict, Field


class AdapterCredentialsLike(Protocol):
    api_key_env: str
    api_secret_env: str
    recv_window_ms: int
    timeout_sec: float
    proxy_url: str | None
    api_base_url: str


class PreparedRequestLike(Protocol):
    method: str
    path: str
    requires_auth: bool
    params: dict[str, Any]
    body: dict[str, Any]
    idempotency_key: str


class SignedAdapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    path: str
    url: str
    requires_auth: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = ""


class TransportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    http_status: int
    payload: Any = None
    headers: dict[str, str] = Field(default_factory=dict)


class BinanceRequestConfigError(RuntimeError):
    pass


class BinanceTransportError(RuntimeError):
    def __init__(
        self,
        *,
        kind: str,
        message: str,
        http_status: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status
        self.payload = payload


class BinanceRequestSigner:
    def __init__(
        self,
        credentials: AdapterCredentialsLike,
        *,
        env_getter: Callable[[str], str | None] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._credentials = credentials
        self._env_getter = env_getter or os.environ.get
        self._clock = clock or self._utc_timestamp_ms

    def sign(self, prepared_request: PreparedRequestLike) -> SignedAdapterRequest:
        params = {**prepared_request.params, **prepared_request.body}
        headers: dict[str, str] = {}
        if prepared_request.requires_auth:
            api_key = self._read_required_env(self._credentials.api_key_env)
            api_secret = self._read_required_env(self._credentials.api_secret_env)
            params["timestamp"] = self._clock()
            params["recvWindow"] = self._credentials.recv_window_ms
            query_string = parse.urlencode(self._normalize_params(params), doseq=True)
            signature = hmac.new(api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers["X-MBX-APIKEY"] = api_key
        return SignedAdapterRequest(
            method=prepared_request.method,
            path=prepared_request.path,
            url=f"{self._credentials.api_base_url.rstrip('/')}{prepared_request.path}",
            requires_auth=prepared_request.requires_auth,
            headers=headers,
            params=params,
            idempotency_key=prepared_request.idempotency_key,
        )

    def _read_required_env(self, key: str) -> str:
        value = self._env_getter(key)
        if not value:
            raise BinanceRequestConfigError(f"Missing required environment variable: {key}")
        return value

    @staticmethod
    def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                normalized[key] = "true" if value else "false"
                continue
            normalized[key] = value
        return normalized

    @staticmethod
    def _utc_timestamp_ms() -> int:
        return int(datetime.now(UTC).timestamp() * 1000)


class BinanceTransport:
    def __init__(
        self,
        credentials: AdapterCredentialsLike,
        *,
        opener_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._credentials = credentials
        self._opener_factory = opener_factory or request.build_opener

    def send(self, signed_request: SignedAdapterRequest) -> TransportResponse:
        opener = self._build_opener()
        query = parse.urlencode(BinanceRequestSigner._normalize_params(signed_request.params), doseq=True)
        url = signed_request.url if not query else f"{signed_request.url}?{query}"
        req = request.Request(url, headers=signed_request.headers, method=signed_request.method.upper())
        try:
            with opener.open(req, timeout=self._credentials.timeout_sec) as response:
                raw = response.read().decode("utf-8")
                payload = self._parse_payload(raw)
                return TransportResponse(
                    http_status=int(getattr(response, "status", 200)),
                    payload=payload,
                    headers=dict(getattr(response, "headers", {}).items()),
                )
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise BinanceTransportError(
                kind="http_error",
                message=f"HTTP {exc.code}",
                http_status=int(exc.code),
                payload=self._parse_payload(raw),
            ) from exc
        except BinanceTransportError:
            raise
        except Exception as exc:
            if self._is_timeout_error(exc):
                raise BinanceTransportError(kind="timeout", message=str(exc) or "request timed out") from exc
            if isinstance(exc, error.URLError):
                raise BinanceTransportError(kind="transport_error", message=str(exc.reason or exc)) from exc
            raise BinanceTransportError(kind="transport_error", message=str(exc) or exc.__class__.__name__) from exc

    def _build_opener(self):
        proxy_url = self._credentials.proxy_url
        if proxy_url:
            return self._opener_factory(request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        return self._opener_factory()

    @staticmethod
    def _parse_payload(raw: str) -> Any:
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BinanceTransportError(kind="json_error", message="Malformed JSON response", payload=raw) from exc

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        if isinstance(exc, socket.timeout | TimeoutError):
            return True
        if isinstance(exc, error.URLError):
            reason = exc.reason
            return isinstance(reason, socket.timeout | TimeoutError) or "timed out" in str(reason).lower()
        return "timed out" in str(exc).lower()
