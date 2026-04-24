"""JSON file persistence under DATA_DIR."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from app.models import AppSettings, Recipe


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Storage:
    def __init__(self, data_dir: str) -> None:
        self.root = Path(data_dir)
        self.config_path = self.root / "config.json"
        self.recipes_dir = self.root / "recipes"
        self.captures_dir = self.root / "captures"

    def ensure_dirs(self) -> None:
        self.recipes_dir.mkdir(parents=True, exist_ok=True)
        self.captures_dir.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> AppSettings:
        self.ensure_dirs()
        if not self.config_path.exists():
            s = AppSettings()
            self.save_settings(s)
            return s
        with open(self.config_path, encoding="utf-8") as f:
            raw = json.load(f)
        return AppSettings.model_validate(raw)

    def save_settings(self, settings: AppSettings) -> None:
        self.ensure_dirs()
        _atomic_write(self.config_path, settings.model_dump_json(indent=2))

    def list_recipe_ids(self) -> list[str]:
        self.ensure_dirs()
        ids: list[str] = []
        if not self.recipes_dir.exists():
            return ids
        for p in sorted(self.recipes_dir.glob("*.json")):
            ids.append(p.stem)
        return ids

    def load_recipe(self, recipe_id: str) -> Recipe | None:
        path = self.recipes_dir / f"{recipe_id}.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        r = Recipe.model_validate(raw)
        r.id = recipe_id
        return r

    def save_recipe(self, recipe: Recipe) -> Recipe:
        self.ensure_dirs()
        rid = recipe.id or uuid.uuid4().hex[:12]
        recipe = recipe.model_copy(update={"id": rid})
        path = self.recipes_dir / f"{rid}.json"
        _atomic_write(path, recipe.model_dump_json(indent=2))
        return recipe

    def delete_recipe(self, recipe_id: str) -> bool:
        path = self.recipes_dir / f"{recipe_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_recipes(self) -> list[Recipe]:
        return [r for rid in self.list_recipe_ids() if (r := self.load_recipe(rid)) is not None]
