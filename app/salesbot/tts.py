"""Speech for replies: Azure neural voices (Uzbek Madina/Sardor, Turkish, Russian).

Telegram/WhatsApp voice notes use OGG/Opus; phone calls use MP3 played by Twilio <Play>.
"""
from __future__ import annotations

import logging
import re
from xml.sax.saxutils import escape

from app.salesbot import lang as L

log = logging.getLogger(__name__)

VOICES = {"uz": ("uz-UZ", "uz-UZ-MadinaNeural"), "tr": ("tr-TR", "tr-TR-EmelNeural"), "ru": ("ru-RU", "ru-RU-SvetlanaNeural")}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⬀-⯿️]")
_CYR2LAT = {"ш": "sh", "ч": "ch", "ё": "yo", "ю": "yu", "я": "ya", "ў": "o'", "ғ": "g'", "қ": "q", "ҳ": "h", "х": "x", "ж": "j", "й": "y",
            "ц": "ts", "щ": "sh", "ъ": "'", "ь": "", "э": "e", "ы": "i"}


def latin_uz(text: str) -> str:
    """Uzbek Cyrillic -> Latin, keeping capitals (Azure's Uzbek voice reads Latin script)."""
    base = dict(zip("абвгдезиклмнопрстуф", "abvgdeziklmnoprstuf"))
    out = []
    for ch in text:
        low = ch.lower()
        rep = _CYR2LAT.get(low) or base.get(low)
        if rep is None:
            out.append(ch)
            continue
        out.append(rep[:1].upper() + rep[1:] if ch != low else rep)
    return "".join(out)


def clean(text: str) -> str:
    t = _EMOJI.sub("", text).replace("•", ",").replace("×", " ").replace("№", " ").replace("«", "").replace("»", "")
    return re.sub(r"\s{2,}", " ", re.sub(r"\s*\n\s*", ". ", t)).strip()


class AzureTTS:
    def __init__(self, key: str, region: str, uz_voice: str | None = None, client=None):
        self.key, self.region, self._client = key, region, client
        self.voices = dict(VOICES)
        if uz_voice:
            self.voices["uz"] = ("uz-UZ", uz_voice)

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=20)
        return self._client

    async def synth(self, text: str, lang: str, fmt: str = "ogg") -> bytes | None:
        base = "uz" if lang in ("uz", "uzc") else lang
        locale, voice = self.voices.get(base, self.voices["uz"])
        body = clean(latin_uz(text) if lang == "uzc" else text)[:1500]
        ssml = f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}'>{escape(body)}</voice></speak>"
        out = "ogg-24khz-16bit-mono-opus" if fmt == "ogg" else "audio-24khz-48kbitrate-mono-mp3"
        try:
            resp = await self._http().post(f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1", content=ssml.encode(),
                                           headers={"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/ssml+xml",
                                                    "X-Microsoft-OutputFormat": out, "User-Agent": "namtour-agent"})
        except Exception as exc:
            log.warning("Azure TTS хатоси: %s", type(exc).__name__)
            return None
        if resp.status_code >= 400:
            log.warning("Azure TTS рад этди: %s", resp.status_code)
            return None
        return resp.content


def build_tts(settings) -> AzureTTS | None:
    k = settings.azure_speech_key
    if k is None or not k.get_secret_value() or not settings.azure_speech_region:
        return None
    return AzureTTS(k.get_secret_value(), settings.azure_speech_region, settings.sales_uz_voice)


__all__ = ["AzureTTS", "build_tts", "latin_uz", "clean", "L"]
