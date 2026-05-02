import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.platforms.discord import DISCORD_AVAILABLE, NewsArticleFeedbackView, ThreadLifecycleView


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_news_article_button_defers_before_adding_reaction():
    view = NewsArticleFeedbackView(allowed_user_ids=set())
    view._persist_news_feedback = lambda *_args, **_kwargs: None
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
    view._persist_news_feedback = lambda *_args, **_kwargs: None
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


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_deep_dive_button_creates_thread_and_dispatches_agent():
    calls = []

    async def add_reaction(emoji):
        calls.append(("add_reaction", emoji))

    async def defer(**kwargs):
        calls.append(("defer", kwargs))

    async def followup_send(content, **kwargs):
        calls.append(("followup", content, kwargs))

    async def create_thread(**kwargs):
        calls.append(("create_thread", kwargs))
        return SimpleNamespace(id=456, name=kwargs["name"], send=AsyncMock())

    async def dispatch_thread_session(interaction, thread_id, thread_name, text):
        calls.append(("dispatch", thread_id, thread_name, text))

    adapter = SimpleNamespace(
        _threads=SimpleNamespace(mark=lambda thread_id: calls.append(("mark_thread", thread_id))),
        _dispatch_thread_session=dispatch_thread_session,
    )
    view = NewsArticleFeedbackView(adapter=adapter, allowed_user_ids=set())
    view._persist_news_feedback = lambda *_args, **_kwargs: calls.append(("persist",))

    message = SimpleNamespace(
        id=123,
        content="1. **OpenAI releases new agent feature**\nhttps://example.com/news\n- why it matters",
        add_reaction=add_reaction,
        create_thread=create_thread,
        jump_url="https://discord.com/channels/1/2/123",
        embeds=[],
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, display_name="hiro"),
        message=message,
        channel=SimpleNamespace(create_thread=AsyncMock()),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=followup_send),
    )

    await view._mark(interaction, "deep", "Deep Dive")

    assert calls[0] == ("defer", {"ephemeral": True, "thinking": False})
    assert calls[1] == ("add_reaction", "🧵")
    assert calls[2] == ("persist",)
    assert calls[3][0] == "create_thread"
    assert calls[3][1]["name"] == "深掘り: OpenAI releases new agent feature"
    assert calls[4] == ("mark_thread", "456")
    assert calls[5][0] == "followup"
    assert "詳細スレッド <#456>" in calls[5][1]

    # The agent dispatch is scheduled in the background by the button handler.
    await asyncio.sleep(0)
    assert calls[6][0] == "dispatch"
    assert calls[6][1] == "456"
    assert "このニュース記事の詳細版スレッドとして深掘りしてください。" in calls[6][3]


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_resume_button_dispatches_continuation_request():
    view = ThreadLifecycleView(adapter=SimpleNamespace(), allowed_user_ids=set())
    calls = []

    thread = SimpleNamespace(
        name="途中の相談",
        edit=AsyncMock(side_effect=lambda **kwargs: calls.append(("edit", kwargs))),
    )

    async def thread_for_interaction(_interaction):
        return thread

    async def defer(**kwargs):
        calls.append(("defer", kwargs))

    async def followup_send(content, **kwargs):
        calls.append(("followup", content, kwargs))

    async def dispatch_agent_request(interaction, text):
        calls.append(("dispatch", interaction, text))

    view._thread = thread_for_interaction
    view._dispatch_agent_request = dispatch_agent_request
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123, display_name="hiro"),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=followup_send),
    )
    await view._resume_thread_action(interaction)

    assert calls[0] == ("defer", {"ephemeral": True, "thinking": False})
    assert calls[1][0] == "edit"
    assert calls[1][1]["name"] == "継続: 途中の相談"
    assert calls[2] == ("followup", "🔄 続きから応答します〜", {"ephemeral": True})
    assert calls[3][0] == "dispatch"
    assert "止まっていた続きの応答をしてください" in calls[3][2]
