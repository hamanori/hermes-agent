from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.platforms.discord import DISCORD_AVAILABLE, NewsArticleFeedbackView


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_news_article_button_defers_before_adding_reaction():
    view = NewsArticleFeedbackView(allowed_user_ids=set())
    calls = []

    async def defer(**kwargs):
        calls.append(("defer", kwargs))

    async def add_reaction(emoji):
        calls.append(("add_reaction", emoji))

    async def followup_send(content, **kwargs):
        calls.append(("followup", content, kwargs))

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        message=SimpleNamespace(add_reaction=add_reaction),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=followup_send),
    )

    await view._mark(interaction, "read", "読んだ")

    assert calls[0] == ("defer", {"ephemeral": True, "thinking": False})
    assert calls[1] == ("add_reaction", "✅")
    assert calls[2] == ("followup", "✅ 読んだ として記録しました〜", {"ephemeral": True})


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_news_article_button_reports_reaction_failure_after_defer():
    view = NewsArticleFeedbackView(allowed_user_ids=set())
    calls = []

    async def defer(**kwargs):
        calls.append(("defer", kwargs))

    async def add_reaction(_emoji):
        calls.append(("add_reaction", _emoji))
        raise RuntimeError("Missing Permissions")

    async def followup_send(content, **kwargs):
        calls.append(("followup", content, kwargs))

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        message=SimpleNamespace(add_reaction=add_reaction),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=followup_send),
    )

    await view._mark(interaction, "skip", "減らす候補")

    assert calls[0][0] == "defer"
    assert calls[1] == ("add_reaction", "👎")
    assert calls[2] == (
        "followup",
        "記録に失敗しました: Missing Permissions",
        {"ephemeral": True},
    )


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_news_article_button_denies_unauthorized_without_defer():
    view = NewsArticleFeedbackView(allowed_user_ids={"999"})
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        message=SimpleNamespace(add_reaction=AsyncMock()),
        response=response,
    )

    await view._mark(interaction, "read", "読んだ")

    response.send_message.assert_awaited_once_with("このボタンを使う権限がないみたいです〜", ephemeral=True)
    interaction.message.add_reaction.assert_not_awaited()
