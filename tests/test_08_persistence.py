"""8. Ботни қайта ишга туширганда сақланган маълумотлар йўқолмайди."""
from tests.conftest import DIRECTOR, build_env, make_settings


async def test_data_survives_restart(tmp_path):
    settings = make_settings(tmp_path)
    first = await build_env(settings)
    await first.service.dispatch(DIRECTOR, "/books new «Наманган эртаклари»", idempotency_key="a")
    await first.service.dispatch(DIRECTOR, "/clients new 12-мактаб | +998900000001", idempotency_key="b")
    await first.service.dispatch(DIRECTOR, "/task Янги дарслик лойиҳасини оч", idempotency_key="tg:1:9")
    await first.service.dispatch(DIRECTOR, "/pause")
    await first.engine.dispose()  # "restart"

    second = await build_env(settings, create=False)
    assert "Наманган эртаклари" in (await second.service.dispatch(DIRECTOR, "/books")).text
    assert "12-мактаб" in (await second.service.dispatch(DIRECTOR, "/clients")).text
    assert "тўхтатилган" in (await second.service.dispatch(DIRECTOR, "/agents")).text
    # the same Telegram message delivered again after restart is still recognised
    assert "аллақачон" in (await second.service.dispatch(DIRECTOR, "/task Янги дарслик лойиҳасини оч", idempotency_key="tg:1:9")).text
    await second.engine.dispose()
