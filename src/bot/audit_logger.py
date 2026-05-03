from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    generated_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditLogger:
    _REDACTED = "<redacted>"
    _SENSITIVE_HEADER_KEYS = {"X-MBX-APIKEY", "Authorization", "Proxy-Authorization"}
    _SENSITIVE_PARAM_KEYS = {"signature", "timestamp", "recvWindow"}
    _SENSITIVE_TOP_LEVEL_KEYS = {"signed_request"}

    def __init__(self, output_path: str | Path) -> None:
        self._output_path = Path(output_path)

    @property
    def output_path(self) -> Path:
        return self._output_path

    def append(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        generated_at: datetime | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            generated_at=generated_at or datetime.now().replace(microsecond=0),
            payload=self._sanitize_value(payload),
        )
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._output_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return event

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return cls._sanitize_dict(value)
        if isinstance(value, list):
            return [cls._sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize_value(item) for item in value]
        return value

    @classmethod
    def _sanitize_dict(cls, value: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in cls._SENSITIVE_TOP_LEVEL_KEYS and isinstance(item, dict):
                sanitized[key] = cls._sanitize_signed_request(item)
                continue
            if key == "headers" and isinstance(item, dict):
                sanitized[key] = cls._sanitize_headers(item)
                continue
            if key == "params" and isinstance(item, dict):
                sanitized[key] = cls._sanitize_params(item)
                continue
            sanitized[key] = cls._sanitize_value(item)
        return sanitized

    @classmethod
    def _sanitize_signed_request(cls, value: dict[str, Any]) -> dict[str, Any]:
        sanitized = cls._sanitize_dict(value)
        if "headers" in value and isinstance(value["headers"], dict):
            sanitized["headers"] = cls._sanitize_headers(value["headers"])
        if "params" in value and isinstance(value["params"], dict):
            sanitized["params"] = cls._sanitize_params(value["params"])
        return sanitized

    @classmethod
    def _sanitize_headers(cls, headers: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in headers.items():
            sanitized[key] = cls._REDACTED if key in cls._SENSITIVE_HEADER_KEYS else cls._sanitize_value(value)
        return sanitized

    @classmethod
    def _sanitize_params(cls, params: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in params.items():
            sanitized[key] = cls._REDACTED if key in cls._SENSITIVE_PARAM_KEYS else cls._sanitize_value(value)
        return sanitized
