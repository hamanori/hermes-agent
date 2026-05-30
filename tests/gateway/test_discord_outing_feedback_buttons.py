import json
import sqlite3
from types import SimpleNamespace

import pytest

from plugins.platforms.discord.adapter import DISCORD_AVAILABLE, OutingFeedbackView


def init_outings_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE outing_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL
        );
        CREATE TABLE outing_publications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          discord_message_id TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE outing_feedback_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_id INTEGER,
          publication_id INTEGER,
          feedback_type TEXT NOT NULL,
          actor TEXT NOT NULL DEFAULT 'hiro',
          created_at TEXT NOT NULL,
          todoist_task_id TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    con.execute("INSERT INTO outing_items(id, title) VALUES (42, '上野の展示')")
    con.execute(
        "INSERT INTO outing_publications(id, discord_message_id, metadata_json) VALUES (7, '789', ?)",
        (json.dumps({"item_id": 42}),),
    )
    con.commit()
    con.close()


@pytest.mark.skipif(not DISCORD_AVAILABLE, reason="discord.py is not installed")
@pytest.mark.asyncio
async def test_outing_want_to_go_button_persists_feedback(tmp_path, monkeypatch):
    db_path = tmp_path / "outings.sqlite3"
    init_outings_db(db_path)
    monkeypatch.setenv("HERMES_OUTINGS_DB", str(db_path))

    view = OutingFeedbackView(allowed_user_ids=set())
    calls = []

    async def defer(**kwargs):
        calls.append(("defer", kwargs))

    async def add_reaction(emoji):
        calls.append(("add_reaction", emoji))

    async def followup_send(content, **kwargs):
        calls.append(("followup", content, kwargs))

    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(id=123, display_name="hiro"),
        channel=SimpleNamespace(id=456),
        message=SimpleNamespace(
            id=789,
            add_reaction=add_reaction,
        ),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=followup_send),
    )

    await view._mark(interaction, "want_to_go", "行きたい")

    assert calls[0] == ("defer", {"ephemeral": True, "thinking": False})
    assert calls[1] == ("add_reaction", "🙋")
    assert calls[2] == ("followup", "🙋 行きたい として記録しました〜", {"ephemeral": True})

    con = sqlite3.connect(db_path)
    event = con.execute("SELECT item_id, publication_id, feedback_type, metadata_json FROM outing_feedback_events").fetchone()
    assert event[:3] == (42, 7, "want_to_go")
    assert json.loads(event[3])["custom_id"] == "outing_want_to_go"
