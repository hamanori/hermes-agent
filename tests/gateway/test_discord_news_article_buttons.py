import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.discord import (
    DISCORD_AVAILABLE,
    DiscordAdapter,
    NewsArticleFeedbackView,
    ThreadLifecycleView,
)


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
    assert "x-browser / grok-browser" in calls[6][3]
    assert "X/Grok 由来の情報は一次情報ではなく signal" in calls[6][3]


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_news_digest_send_attaches_feedback_view_to_each_article():
    channel_id = "1498745024853053501"
    config = PlatformConfig(
        enabled=True,
        token="***",
        extra={"news_article_button_channels": [channel_id]},
    )
    adapter = DiscordAdapter(config)
    sent_messages = []

    async def send_message(**kwargs):
        sent_messages.append(kwargs)
        return SimpleNamespace(id=len(sent_messages))

    channel = SimpleNamespace(
        id=int(channel_id),
        parent=None,
        send=send_message,
    )
    adapter._client = SimpleNamespace(
        get_channel=lambda _id: channel,
        fetch_channel=AsyncMock(return_value=channel),
    )

    content = """AI News digest

1. **First article**
https://example.com/first
- why it matters

2. **Second article**
https://example.com/second
- why it matters

### SKIP
- low-signal item
"""

    result = await adapter.send(channel_id, content)

    assert result.success is True
    assert [item["content"].splitlines()[0] for item in sent_messages] == [
        "AI News digest",
        "1. **First article**",
        "2. **Second article**",
        "### SKIP",
    ]
    assert sent_messages[0].get("view") is None
    assert sent_messages[1].get("view") is not None
    assert sent_messages[2].get("view") is not None
    assert sent_messages[3].get("view") is None


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
