"""Guard: cannot delete the recipe currently running by the scheduler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.models import Recipe, RecipeActions, SchedulerStateResponse
from app.storage import Storage


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = Storage(str(tmp_path))
    storage.ensure_dirs()

    storage.save_recipe(
        Recipe(
            id="abc",
            name="Active recipe",
            enabled=False,
            actions=RecipeActions(take_snapshot=True),
        )
    )
    storage.save_recipe(
        Recipe(
            id="other",
            name="Other recipe",
            enabled=False,
            actions=RecipeActions(take_snapshot=True),
        )
    )

    fake_scheduler = MagicMock()
    fake_scheduler.get_state.return_value = SchedulerStateResponse(
        state="running",
        current_recipe_id="abc",
        current_recipe_name="Active recipe",
        message="running",
    )
    monkeypatch.setattr(app_main, "_storage", storage)
    monkeypatch.setattr(app_main, "_scheduler", fake_scheduler)

    return TestClient(app_main.app)


def test_delete_running_recipe_returns_409(client):
    r = client.delete("/api/v1/recipes/abc")
    assert r.status_code == 409
    detail = r.json().get("detail", "")
    assert "Active recipe" in detail
    assert "currently running" in detail


def test_delete_other_recipe_succeeds_when_one_is_running(client):
    r = client.delete("/api/v1/recipes/other")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_delete_unknown_recipe_returns_404_when_other_is_running(client):
    r = client.delete("/api/v1/recipes/does-not-exist")
    assert r.status_code == 404
