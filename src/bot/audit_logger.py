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
            payload=payload,
        )
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._output_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
        return event
