"""Tests for /title gateway slash command.

Tests the _handle_title_command handler (set/show session titles)
across all gateway messenger platforms.
"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/title", platform=Platform.TELEGRAM,
                user_id="12345", chat_id="67890"):
    """Build a MessageEvent for testing."""
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner(session_db=None):
    """Create a bare GatewayRunner with a mock session_store and optional session_db."""
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_db = session_db

    # Mock session_store that returns a session entry with a known session_id
    mock_session_entry = MagicMock()
    mock_session_entry.session_id = "test_session_123"
    mock_session_entry.session_key = "telegram:12345:67890"
    mock_store = MagicMock()
    mock_store.get_or_create_session.return_value = mock_session_entry
    runner.session_store = mock_store

    return runner


# ---------------------------------------------------------------------------
# _handle_title_command
# ---------------------------------------------------------------------------


class TestHandleTitleCommand:
    """Tests for GatewayRunner._handle_title_command."""

    @pytest.mark.asyncio
    async def test_set_title(self, tmp_path):
        """Setting a title returns confirmation."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")

        runner = _make_runner(session_db=db)
        event = _make_event(text="/title My Research Project")
        result = await runner._handle_title_command(event)
        assert "My Research Project" in result
        assert "✏️" in result

        # Verify in DB
        assert db.get_session_title("test_session_123") == "My Research Project"
        db.close()

    @pytest.mark.asyncio
    async def test_show_title_when_set(self, tmp_path):
        """Showing title when one is set returns the title."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")
        db.set_session_title("test_session_123", "Existing Title")

        runner = _make_runner(session_db=db)
        event = _make_event(text="/title")
        result = await runner._handle_title_command(event)
        assert "Existing Title" in result
        assert "📌" in result
        db.close()

    @pytest.mark.asyncio
    async def test_show_title_when_not_set(self, tmp_path):
        """Showing title when none is set returns usage hint."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")

        runner = _make_runner(session_db=db)
        event = _make_event(text="/title")
        result = await runner._handle_title_command(event)
        assert "No title set" in result
        assert "/title" in result
        db.close()

    @pytest.mark.asyncio
    async def test_title_conflict(self, tmp_path):
        """Setting a title already used by another session returns error."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("other_session", "telegram")
        db.set_session_title("other_session", "Taken Title")
        db.create_session("test_session_123", "telegram")

        runner = _make_runner(session_db=db)
        event = _make_event(text="/title Taken Title")
        result = await runner._handle_title_command(event)
        assert "already in use" in result
        assert "⚠️" in result
        db.close()

    @pytest.mark.asyncio
    async def test_no_session_db(self):
        """Returns error when session database is not available."""
        runner = _make_runner(session_db=None)
        event = _make_event(text="/title My Title")
        result = await runner._handle_title_command(event)
        assert "not available" in result

    @pytest.mark.asyncio
    async def test_title_too_long(self, tmp_path):
        """Setting a title that exceeds max length returns error."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")

        runner = _make_runner(session_db=db)
        long_title = "A" * 150
        event = _make_event(text=f"/title {long_title}")
        result = await runner._handle_title_command(event)
        assert "too long" in result
        assert "⚠️" in result
        db.close()

    @pytest.mark.asyncio
    async def test_title_control_chars_sanitized(self, tmp_path):
        """Control characters are stripped and sanitized title is stored."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")

        runner = _make_runner(session_db=db)
        event = _make_event(text="/title hello\x00world")
        result = await runner._handle_title_command(event)
        assert "helloworld" in result
        assert db.get_session_title("test_session_123") == "helloworld"
        db.close()

    @pytest.mark.asyncio
    async def test_title_only_control_chars(self, tmp_path):
        """Title with only control chars returns empty error."""
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session("test_session_123", "telegram")

        runner = _make_runner(session_db=db)
        event = _make_event(text="/title \x00\x01\x02")
        result = await runner._handle_title_command(event)
        assert "empty after cleanup" in result
        db.close()

    @pytest.mark.asyncio
    async def test_works_across_platforms(self, tmp_path):
        """The /title command works for Discord, Slack, and WhatsApp too."""
        from hermes_state import SessionDB
        for platform in [Platform.DISCORD, Platform.TELEGRAM]:
            db = SessionDB(db_path=tmp_path / f"state_{platform.value}.db")
            db.create_session("test_session_123", platform.value)

            runner = _make_runner(session_db=db)
            event = _make_event(text="/title Cross-Platform Test", platform=platform)
            result = await runner._handle_title_command(event)
            assert "Cross-Platform Test" in result
            assert db.get_session_title("test_session_123") == "Cross-Platform Test"
            db.close()


# ---------------------------------------------------------------------------
# /title in help and known_commands
# ---------------------------------------------------------------------------


class TestTitleInHelp:
    """Verify /title appears in help text and known commands."""

    @pytest.mark.asyncio
    async def test_title_in_help_output(self):
        """The /help output includes /title."""
        runner = _make_runner()
        event = _make_event(text="/help")
        # Need hooks for help command
        from gateway.hooks import HookRegistry
        runner.hooks = HookRegistry()
        result = await runner._handle_help_command(event)
        assert "/title" in result

    def test_title_is_known_command(self):
        """The /title command is in the _known_commands set."""
        from gateway.run import GatewayRunner
        import inspect
        source = inspect.getsource(GatewayRunner._handle_message)
        assert '"title"' in source


# ---------------------------------------------------------------------------
# /new with title
# ---------------------------------------------------------------------------


class TestResetCommandWithTitle:
    """Tests for GatewayRunner._handle_reset_command with a title argument."""

    @pytest.mark.asyncio
    async def test_reset_command_with_title(self):
        """Sending /new <title> resets session and sets the title."""
        from datetime import datetime

        from gateway.run import GatewayRunner
        from gateway.session import SessionEntry, SessionSource, build_session_key

        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
        )
        adapter = MagicMock()
        adapter.send = AsyncMock()
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner._voice_mode = {}
        runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
        runner._session_model_overrides = {}
        runner._pending_model_notes = {}
        runner._background_tasks = set()

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )
        session_key = build_session_key(source)
        new_session_entry = SessionEntry(
            session_key=session_key,
            session_id="sess-new",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            platform=Platform.TELEGRAM,
            chat_type="dm",
        )
        runner.session_store = MagicMock()
        runner.session_store.get_or_create_session.return_value = new_session_entry
        runner.session_store.reset_session.return_value = new_session_entry
        runner.session_store._entries = {session_key: new_session_entry}
        runner.session_store._generate_session_key.return_value = session_key
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._pending_approvals = {}
        runner._session_db = MagicMock()
        runner._agent_cache = {}
        runner._agent_cache_lock = None
        runner._is_user_authorized = lambda _source: True
        runner._format_session_info = lambda: ""

        event = _make_event(text="/new Custom Name")
        result = await runner._handle_reset_command(event)

        runner.session_store.reset_session.assert_called_once()
        runner._session_db.set_session_title.assert_called_once_with(
            "sess-new", "Custom Name"
        )
        # Header reflects the applied title
        assert "Custom Name" in str(result)

    @pytest.mark.asyncio
    async def test_reset_command_duplicate_title_surfaces_warning(self):
        """/new <title> with an already-in-use title returns a warning in the reply."""
        from datetime import datetime

        from gateway.run import GatewayRunner
        from gateway.session import SessionEntry, SessionSource, build_session_key

        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig(
            platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
        )
        adapter = MagicMock()
        adapter.send = AsyncMock()
        runner.adapters = {Platform.TELEGRAM: adapter}
        runner._voice_mode = {}
        runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
        runner._session_model_overrides = {}
        runner._pending_model_notes = {}
        runner._background_tasks = set()

        source = SessionSource(
            platform=Platform.TELEGRAM,
            user_id="12345",
            chat_id="67890",
            user_name="testuser",
        )
        session_key = build_session_key(source)
        new_session_entry = SessionEntry(
            session_key=session_key,
            session_id="sess-new",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            platform=Platform.TELEGRAM,
            chat_type="dm",
        )
        runner.session_store = MagicMock()
        runner.session_store.get_or_create_session.return_value = new_session_entry
        runner.session_store.reset_session.return_value = new_session_entry
        runner.session_store._entries = {session_key: new_session_entry}
        runner.session_store._generate_session_key.return_value = session_key
        runner._running_agents = {}
        runner._pending_messages = {}
        runner._pending_approvals = {}
        runner._session_db = MagicMock()
        runner._session_db.set_session_title.side_effect = ValueError(
            "Title 'Dup' is already in use by session abc-123"
        )
        runner._agent_cache = {}
        runner._agent_cache_lock = None
        runner._is_user_authorized = lambda _source: True
        runner._format_session_info = lambda: ""

        event = _make_event(text="/new Dup")
        result = await runner._handle_reset_command(event)

        runner._session_db.set_session_title.assert_called_once()
        reply = str(result)
        assert "already in use" in reply
        assert "session started untitled" in reply
        # Header must NOT claim the rejected title as the session name
        assert "New session started: Dup" not in reply


# ---------------------------------------------------------------------------
# /new in help output
# ---------------------------------------------------------------------------


class TestNewInHelp:
    """Verify /new appears in help text with the [name] args hint."""

    def test_new_command_in_help_output(self):
        """The gateway help output includes /new with the [name] hint."""
        from hermes_cli.commands import gateway_help_lines
        lines = gateway_help_lines()
        new_line = next((line for line in lines if line.startswith("`/new ")), None)
        assert new_line is not None
        assert "[name]" in new_line


# ---------------------------------------------------------------------------
# Discord thread auto-title observation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_discord_thread_title_update_returns_without_waiting(monkeypatch):
    from gateway.run import GatewayRunner

    started = asyncio.Event()
    release = asyncio.Event()
    runner = object.__new__(GatewayRunner)
    runner._background_tasks = set()

    async def slow_update(**kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(runner, "_maybe_update_discord_thread_title", slow_update)
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="parent",
        chat_type="thread",
        thread_id="777",
    )

    task = runner._schedule_discord_thread_title_update(
        source=source,
        session_id="session-1",
        user_message="Discordのスレ名を直して",
        assistant_response="対応します",
        agent_messages=[],
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert task is not None
    assert not task.done()
    assert task in runner._background_tasks
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert task not in runner._background_tasks


@pytest.mark.asyncio
async def test_schedule_discord_thread_title_update_observes_task_exception(monkeypatch, caplog):
    from gateway.run import GatewayRunner
    import gateway.run as gateway_run

    runner = object.__new__(GatewayRunner)
    runner._background_tasks = set()

    async def failing_update(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_maybe_update_discord_thread_title", failing_update)
    caplog.set_level("INFO", logger=gateway_run.logger.name)
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="parent",
        chat_type="thread",
        thread_id="777",
    )

    task = runner._schedule_discord_thread_title_update(
        source=source,
        session_id="session-1",
        user_message="Discordのスレ名を直して",
        assistant_response="対応します",
        agent_messages=[],
    )

    try:
        await asyncio.wait_for(task, timeout=1)
    except RuntimeError:
        pass
    await asyncio.sleep(0)
    assert "reason=task_exception" in caplog.text
    assert "thread_id=777" in caplog.text
    assert "session_id=session-1" in caplog.text
    assert "boom" in caplog.text


@pytest.mark.asyncio
async def test_maybe_update_discord_thread_title_uses_long_background_timeout(monkeypatch):
    from gateway.run import GatewayRunner

    adapter = SimpleNamespace(update_thread_title=AsyncMock(return_value=True))
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: adapter}
    runner._session_db = None
    recorded = {}

    def fake_generate_title(*args, **kwargs):
        recorded["timeout"] = args[2]
        return "Discordスレ名仕様"

    monkeypatch.setattr("agent.title_generator.generate_title", fake_generate_title)
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="parent",
        chat_type="thread",
        thread_id="777",
    )

    await runner._maybe_update_discord_thread_title(
        source=source,
        session_id="session-1",
        user_message="Discordのスレ名を直して",
        assistant_response="対応します",
        agent_messages=[],
    )

    assert recorded["timeout"] == 120.0


@pytest.mark.asyncio
async def test_maybe_update_discord_thread_title_skips_when_generated_title_empty(monkeypatch, caplog):
    from gateway.run import GatewayRunner
    import gateway.run as gateway_run

    adapter = SimpleNamespace(update_thread_title=AsyncMock(return_value=True))
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: adapter}
    runner._session_db = None
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="parent",
        chat_type="thread",
        thread_id="777",
    )
    monkeypatch.setattr("agent.title_generator.generate_title", lambda *args, **kwargs: "")
    caplog.set_level("INFO", logger=gateway_run.logger.name)

    await runner._maybe_update_discord_thread_title(
        source=source,
        session_id="session-1",
        user_message="Discordのスレ名を直して",
        assistant_response="対応します",
        agent_messages=[],
    )

    assert "reason=empty_generated_title" in caplog.text
    assert "media_policy=text_only_no_image_analysis" in caplog.text
    assert "thread_id=777" in caplog.text
    adapter.update_thread_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_update_discord_thread_title_calls_adapter_when_title_generated(monkeypatch, caplog):
    from gateway.run import GatewayRunner
    import gateway.run as gateway_run

    adapter = SimpleNamespace(update_thread_title=AsyncMock(return_value=True))
    session_db = MagicMock()
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: adapter}
    runner._session_db = session_db
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="parent",
        chat_type="thread",
        thread_id="777",
    )
    monkeypatch.setattr("agent.title_generator.generate_title", lambda *args, **kwargs: "Discordスレ名仕様")
    caplog.set_level("INFO", logger=gateway_run.logger.name)

    await runner._maybe_update_discord_thread_title(
        source=source,
        session_id="session-1",
        user_message="Discordのスレ名アルゴリズムを確認して",
        assistant_response="スレ名更新の仕様を確認しました",
        agent_messages=[],
    )

    session_db.set_session_title.assert_called_once_with("session-1", "Discordスレ名仕様")
    adapter.update_thread_title.assert_awaited_once_with(
        "777",
        "Discordスレ名仕様",
        user_message="Discordのスレ名アルゴリズムを確認して",
    )
    assert "Discord thread title auto-update generated" in caplog.text
    assert "Discordスレ名仕様" in caplog.text
    assert "reason=adapter_returned_false" not in caplog.text


@pytest.mark.asyncio
async def test_maybe_update_discord_thread_title_logs_adapter_false(monkeypatch, caplog):
    from gateway.run import GatewayRunner
    import gateway.run as gateway_run

    adapter = SimpleNamespace(update_thread_title=AsyncMock(return_value=False))
    session_db = MagicMock()
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: adapter}
    runner._session_db = session_db
    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="parent",
        chat_type="thread",
        thread_id="777",
    )
    monkeypatch.setattr("agent.title_generator.generate_title", lambda *args, **kwargs: "Discordスレ名更新")
    caplog.set_level("INFO", logger=gateway_run.logger.name)

    await runner._maybe_update_discord_thread_title(
        source=source,
        session_id="session-1",
        user_message="Discordのスレ名を直して",
        assistant_response="対応します",
        agent_messages=[],
    )

    session_db.set_session_title.assert_called_once_with("session-1", "Discordスレ名更新")
    adapter.update_thread_title.assert_awaited_once_with("777", "Discordスレ名更新", user_message="Discordのスレ名を直して")
    assert "reason=adapter_returned_false" in caplog.text
    assert "title='Discordスレ名更新'" in caplog.text
