from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
from datetime import UTC, datetime
from typing import Any, Callable, Protocol
from urllib import error, parse, request

from .binance_transport import SignedAdapterRequest, TransportResponse


class AdapterCredentialsLike(Protocol):
    api_key_env: str
    api_secret_env: str
    api_passphrase_env: str
    timeout_sec: float
    proxy_url: str | None
    api_base_url: str


class PreparedRequestLike(Protocol):
    method: str
    path: str
    requires_auth: bool
    params: dict[str, Any]
    body: Any
    idempotency_key: str


class OkxRequestConfigError(RuntimeError):
    pass


class OkxTransportError(RuntimeError):
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


class OkxRequestSigner:
    def __init__(
        self,
        credentials: AdapterCredentialsLike,
        *,
        env_getter: Callable[[str], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._credentials = credentials
        self._env_getter = env_getter or os.environ.get
        self._clock = clock or (lambda: datetime.now(UTC))

    def sign(self, prepared_request: PreparedRequestLike) -> SignedAdapterRequest:
        params = self._normalize_params(dict(prepared_request.params or {}))
        body = self._normalize_body(prepared_request.body)
        path = str(prepared_request.path or "")
        query = parse.urlencode(params, doseq=True)
        request_path = path if not query else f"{path}?{query}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "eth-trading-bot/1.0",
        }
        if prepared_request.requires_auth:
            api_key = self._read_required_env(self._credentials.api_key_env)
            api_secret = self._read_required_env(self._credentials.api_secret_env)
            passphrase = self._read_required_env(self._credentials.api_passphrase_env)
            timestamp = self._timestamp()
            body_text = self._body_text(body) if prepared_request.method.upper() != "GET" else ""
            prehash = f"{timestamp}{prepared_request.method.upper()}{request_path}{body_text}"
            signature = base64.b64encode(
                hmac.new(api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
            ).decode("utf-8")
            headers.update(
                {
                    "OK-ACCESS-KEY": api_key,
                    "OK-ACCESS-SIGN": signature,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": passphrase,
                }
            )
        return SignedAdapterRequest(
            method=prepared_request.method,
            path=path,
            url=f"{self._credentials.api_base_url.rstrip('/')}{path}",
            requires_auth=prepared_request.requires_auth,
            headers=headers,
            params=params,
            body=body if prepared_request.method.upper() != "GET" else {},
            idempotency_key=prepared_request.idempotency_key,
        )

    def _read_required_env(self, key: str) -> str:
        value = self._env_getter(key)
        if not value:
            raise OkxRequestConfigError(f"Missing required environment variable: {key}")
        return value

    def _timestamp(self) -> str:
        value = self._clock().astimezone(UTC)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _body_text(body: Any) -> str:
        if not body:
            return ""
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _normalize_body(cls, body: Any) -> Any:
        if isinstance(body, dict):
            return cls._normalize_params(dict(body))
        if isinstance(body, list):
            normalized = []
            for item in body:
                normalized.append(cls._normalize_params(dict(item)) if isinstance(item, dict) else item)
            return normalized
        return body or {}

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


class OkxTransport:
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
        query = parse.urlencode(OkxRequestSigner._normalize_params(signed_request.params), doseq=True)
        url = signed_request.url if not query else f"{signed_request.url}?{query}"
        data = None
        headers = dict(signed_request.headers)
        if signed_request.method.upper() != "GET":
            data = OkxRequestSigner._body_text(signed_request.body or {}).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method=signed_request.method.upper())
        try:
            with opener.open(req, timeout=self._credentials.timeout_sec) as response:
                raw = response.read().decode("utf-8")
                payload = self._parse_payload(raw)
                self._raise_for_okx_error(payload=payload, http_status=int(getattr(response, "status", 200)))
                return TransportResponse(
                    http_status=int(getattr(response, "status", 200)),
                    payload=payload,
                    headers=dict(getattr(response, "headers", {}).items()),
                )
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            payload = self._parse_payload(raw)
            raise OkxTransportError(
                kind="http_error",
                message=f"HTTP {exc.code}",
                http_status=int(exc.code),
                payload=payload,
            ) from exc
        except OkxTransportError:
            raise
        except Exception as exc:
            if self._is_timeout_error(exc):
                raise OkxTransportError(kind="timeout", message=str(exc) or "request timed out") from exc
            if isinstance(exc, error.URLError):
                raise OkxTransportError(kind="transport_error", message=str(exc.reason or exc)) from exc
            raise OkxTransportError(kind="transport_error", message=str(exc) or exc.__class__.__name__) from exc

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
            raise OkxTransportError(kind="json_error", message="Malformed JSON response", payload=raw) from exc

    @staticmethod
    def _raise_for_okx_error(*, payload: Any, http_status: int) -> None:
        if not isinstance(payload, dict):
            return
        code = str(payload.get("code") or "")
        if code and code != "0":
            raise OkxTransportError(
                kind="http_error",
                message=str(payload.get("msg") or f"OKX API error {code}"),
                http_status=http_status,
                payload=payload,
            )
        data = payload.get("data")
        if not isinstance(data, list):
            return
        for item in data:
            if not isinstance(item, dict):
                continue
            sub_code = str(item.get("sCode") or "")
            if sub_code and sub_code != "0":
                raise OkxTransportError(
                    kind="http_error",
                    message=str(item.get("sMsg") or f"OKX API error {sub_code}"),
                    http_status=http_status,
                    payload=payload,
                )

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        if isinstance(exc, socket.timeout | TimeoutError):
            return True
        if isinstance(exc, error.URLError):
            reason = exc.reason
            return isinstance(reason, socket.timeout | TimeoutError) or "timed out" in str(reason).lower()
        return "timed out" in str(exc).lower()
