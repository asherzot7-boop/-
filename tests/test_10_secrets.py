"""10. API калитлари ёки бот токени логларга чиқмайди."""
import io
import logging

from app.config import Settings
from app.logging_setup import RedactingFilter, redact, setup_logging

BOT_TOKEN = "1234567890:AAH9xYzSecretTokenValue_abcdefghijkl"
OPENAI_KEY = "sk-proj-SuperSecretKeyValue1234567890"


def _logger_with_buffer(secrets):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactingFilter(secrets))
    logger = logging.getLogger("test.secrets")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, buf


def test_settings_repr_hides_secrets():
    s = Settings(_env_file=None, bot_token=BOT_TOKEN, openai_api_key=OPENAI_KEY)
    dump = repr(s) + str(s) + str(s.model_dump())
    assert BOT_TOKEN not in dump and OPENAI_KEY not in dump
    assert s.bot_token.get_secret_value() == BOT_TOKEN


def test_log_messages_and_exceptions_are_redacted():
    logger, buf = _logger_with_buffer([BOT_TOKEN, OPENAI_KEY])
    logger.info("url https://api.telegram.org/bot%s/getMe", BOT_TOKEN)
    logger.warning(f"key={OPENAI_KEY}")
    try:
        raise RuntimeError(f"401 for token {BOT_TOKEN}")
    except RuntimeError:
        logger.exception("failed")
    out = buf.getvalue()
    assert BOT_TOKEN not in out and OPENAI_KEY not in out
    assert "***" in out and "failed" in out


def test_pattern_redaction_without_known_secret():
    text = redact("token 987654321:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789 and sk-abcdefghijklmnopqrstuvwxyz")
    assert "ABCdef" not in text and "sk-abc" not in text


def test_setup_logging_installs_filter(capsys):
    root = logging.getLogger()
    old = root.handlers[:]
    root.handlers = []
    try:
        setup_logging("INFO", [BOT_TOKEN])
        logging.getLogger("aiogram").error("Bot %s failed", BOT_TOKEN)
        err = capsys.readouterr().err
        assert BOT_TOKEN not in err and "***" in err
    finally:
        root.handlers = old


def test_no_secrets_in_repository_files():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    pattern = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b|sk-[A-Za-z0-9]{32,}")
    for path in list((root / "app").rglob("*.py")) + [root / ".env.example", root / "docker-compose.yml"]:
        if path.exists():
            assert not pattern.search(path.read_text(encoding="utf-8")), path
