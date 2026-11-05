"""Client files on disk and voice transcription."""
from __future__ import annotations

import io
import logging
import re
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^\w.\-]+", re.UNICODE)
EXT_BY_MIME = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "audio/ogg": ".ogg",
               "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac", "video/mp4": ".mp4"}


def safe_name(name: str | None, mime: str | None, kind: str) -> str:
    base = _SAFE.sub("_", (name or "").strip())[:120].strip("._")
    if not base:
        base = {"photo": "photo", "audio": "voice"}.get(kind, "document")
    if "." not in base:
        base += EXT_BY_MIME.get((mime or "").split(";")[0].strip(), "")
    return base


def store(root: str | Path, lead_id: int, name: str, data: bytes) -> Path:
    """Write under <root>/<lead_id>/<uuid>_<name>; the name is sanitised, so a client cannot choose the path."""
    folder = Path(root) / str(int(lead_id))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex[:8]}_{safe_name(name, None, 'document')}"
    path.write_bytes(data)
    return path


class Transcriber:
    """Voice message -> text with OpenAI speech-to-text. Without a key it is disabled."""

    def __init__(self, api_key: str | None, model: str = "whisper-1") -> None:
        self.model = model
        self._client = None
        if api_key:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key, timeout=60, max_retries=1)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def transcribe(self, data: bytes, file_name: str) -> str | None:
        if self._client is None:
            return None
        buf = io.BytesIO(data)
        buf.name = file_name if "." in file_name else file_name + ".ogg"
        try:
            resp = await self._client.audio.transcriptions.create(model=self.model, file=buf)
        except Exception as exc:
            log.warning("Овозни матнга айлантириб бўлмади: %s", type(exc).__name__)
            return None
        text = (getattr(resp, "text", None) or "").strip()
        return text or None
