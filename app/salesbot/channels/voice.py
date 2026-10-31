"""Phone calls through Twilio Programmable Voice.

Call flow: /webhooks/twilio/voice greets and opens <Gather input="speech">;
every recognised phrase goes to /webhooks/twilio/voice/gather, through the
same SalesAgent, and the answer is spoken back. The caller's number is the
lead's phone, so the agent never has to ask for it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from xml.sax.saxutils import escape, quoteattr

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⬀-⯿️✅]")


def verify_signature(auth_token: str, url: str, params: dict[str, str], header: str | None) -> bool:
    """Twilio: base64(HMAC-SHA1(url + sorted key/value pairs))."""
    if not auth_token or not header:
        return False
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(digest, header)


def speakable(text: str) -> str:
    t = _EMOJI.sub("", text)
    t = t.replace("•", ",").replace("×", " ").replace("№", " ").replace("«", "").replace("»", "")
    t = re.sub(r"\s*\n\s*", ". ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _say(text: str, settings) -> str:
    voice = f" voice={quoteattr(settings.sales_voice_name)}" if settings.sales_voice_name else ""
    return f"<Say language={quoteattr(settings.sales_voice_tts_language)}{voice}>{escape(speakable(text))}</Say>"


def gather_twiml(text: str, settings, action: str, noinput: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Gather input="speech" action={quoteattr(action)} method="POST" language={quoteattr(settings.sales_voice_stt_language)} '
        'speechTimeout="auto" timeout="6">'
        f"{_say(text, settings)}</Gather>"
        f"{_say(noinput, settings)}<Redirect method=\"POST\">{escape(action)}?silent=1</Redirect></Response>"
    )


def hangup_twiml(text: str, settings, bye: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{_say(text, settings)}{_say(bye, settings)}<Hangup/></Response>'
