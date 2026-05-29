from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from paprika_mcp.local_store import PaprikaLocalStore


class FakePaprikaClient:
    def __init__(self, stubs: list[dict], recipes: dict[str, dict]) -> None:
        self.stubs = stubs
        self.recipes = recipes
        self.get_recipe_calls: list[str] = []

    def list_recipes(self) -> list[dict]:
        return self.stubs

    def get_recipe(self, uid: str) -> dict:
        self.get_recipe_calls.append(uid)
        return self.recipes[uid]

    def list_categories(self) -> list[dict]:
        return [{"uid": "c1", "name": "Breakfast"}]

    def list_grocery_lists(self) -> list[dict]:
        return [{"uid": "gl1", "name": "Groceries", "is_default": True}]

    def list_grocery_items(self) -> list[dict]:
        return [
            {"uid": "gi1", "list_uid": "gl1", "name": "Tomatoes", "purchased": False},
            {"uid": "gi2", "list_uid": "gl1", "name": "Onions", "purchased": True},
            {"uid": "gi3", "list_uid": "gl2", "name": "Flour", "purchased": False},
        ]

    def list_meal_plans(self) -> list[dict]:
        return [
            {"uid": "m1", "date": "2026-05-28 00:00:00", "type": 2, "name": "Soup"},
            {"uid": "m2", "date": "2026-05-29 00:00:00", "type": 1, "name": "Salad"},
        ]


def recipe(uid: str, name: str, ingredients: str = "1 cup flour") -> dict:
    return {
        "uid": uid,
        "name": name,
        "ingredients": ingredients,
        "directions": "Mix and cook",
        "description": "Test recipe",
        "nutritional_info": "200 cal",
        "servings": "4",
        "difficulty": "Easy",
        "prep_time": "10 min",
        "cook_time": "15 min",
        "rating": 5,
        "source": "Test Kitchen",
        "source_url": "https://example.test",
        "photo": None,
        "photo_hash": None,
        "image_url": "https://img.example.test",
        "categories": ["Breakfast"],
    }


def store(tmp_path: Path) -> PaprikaLocalStore:
    return PaprikaLocalStore(tmp_path / "paprika.sqlite")


def table_columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_initial_sync_fetches_and_lists_recipes(tmp_path: Path) -> None:
    local = store(tmp_path)
    client = FakePaprikaClient(
        [{"uid": "r1", "hash": "h1"}, {"uid": "r2", "hash": "h2"}],
        {"r1": recipe("r1", "Pancakes"), "r2": recipe("r2", "Soup", "1 onion")},
    )

    summary = local.sync_recipes(client)

    assert summary.total_remote == 2
    assert summary.fetched == 2
    assert summary.unchanged == 0
    assert client.get_recipe_calls == ["r1", "r2"]
    assert local.list_recipes() == [
        {"uid": "r1", "hash": "h1", "name": "Pancakes"},
        {"uid": "r2", "hash": "h2", "name": "Soup"},
    ]


def test_unchanged_sync_does_not_fetch_full_recipes(tmp_path: Path) -> None:
    local = store(tmp_path)
    first = FakePaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes")},
    )
    local.sync_recipes(first)

    second = FakePaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes changed but same hash")},
    )
    summary = local.sync_recipes(second)

    assert summary.fetched == 0
    assert summary.unchanged == 1
    assert second.get_recipe_calls == []
    assert local.get_recipe("r1")["name"] == "Pancakes"


def test_changed_hash_fetches_and_updates_recipe(tmp_path: Path) -> None:
    local = store(tmp_path)
    local.sync_recipes(
        FakePaprikaClient(
            [{"uid": "r1", "hash": "h1"}],
            {"r1": recipe("r1", "Pancakes")},
        )
    )
    changed = FakePaprikaClient(
        [{"uid": "r1", "hash": "h2"}],
        {"r1": recipe("r1", "Blueberry Pancakes", "1 cup blueberries")},
    )

    summary = local.sync_recipes(changed)

    assert summary.fetched == 1
    assert changed.get_recipe_calls == ["r1"]
    assert local.get_recipe("r1")["name"] == "Blueberry Pancakes"
    assert local.list_recipes() == [
        {"uid": "r1", "hash": "h2", "name": "Blueberry Pancakes"}
    ]


def test_sync_removes_recipes_missing_from_remote_list(tmp_path: Path) -> None:
    local = store(tmp_path)
    local.sync_recipes(
        FakePaprikaClient(
            [{"uid": "r1", "hash": "h1"}, {"uid": "r2", "hash": "h2"}],
            {"r1": recipe("r1", "Pancakes"), "r2": recipe("r2", "Soup")},
        )
    )

    summary = local.sync_recipes(
        FakePaprikaClient(
            [{"uid": "r1", "hash": "h1"}],
            {"r1": recipe("r1", "Pancakes")},
        )
    )

    assert summary.removed == 1
    assert local.list_recipes() == [{"uid": "r1", "hash": "h1", "name": "Pancakes"}]


def test_search_and_status_read_from_local_database(tmp_path: Path) -> None:
    local = store(tmp_path)
    local.sync_recipes(
        FakePaprikaClient(
            [{"uid": "r1", "hash": "h1"}, {"uid": "r2", "hash": "h2"}],
            {
                "r1": recipe("r1", "Pancakes", "1 cup flour"),
                "r2": recipe("r2", "Tomato Soup", "tomato onion basil"),
            },
        )
    )

    assert local.search_recipes("tomato") == [
        {"uid": "r2", "hash": "h2", "name": "Tomato Soup", "categories": ["Breakfast"]}
    ]
    status = local.sync_status()
    assert status["recipe_count"] == 2
    assert status["last_recipe_sync_summary"]["fetched"] == 2


def test_sync_all_caches_read_only_resources(tmp_path: Path) -> None:
    local = store(tmp_path)
    client = FakePaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes")},
    )

    summary = local.sync_all(client)

    assert summary["recipes"]["fetched"] == 1
    assert summary["resources"] == {
        "categories": {"count": 1},
        "grocery_lists": {"count": 1},
        "grocery_items": {"count": 3},
        "meal_plans": {"count": 2},
    }
    assert local.list_resources("categories") == [{"uid": "c1", "name": "Breakfast"}]
    assert local.list_resources("grocery_lists") == [
        {"uid": "gl1", "name": "Groceries", "is_default": True}
    ]
    assert local.list_grocery_items("gl1") == [
        {"uid": "gi1", "list_uid": "gl1", "name": "Tomatoes", "purchased": False}
    ]
    assert local.list_grocery_items("gl1", include_checked=True) == [
        {"uid": "gi2", "list_uid": "gl1", "name": "Onions", "purchased": True},
        {"uid": "gi1", "list_uid": "gl1", "name": "Tomatoes", "purchased": False},
    ]
    assert local.list_meal_plans("2026-05-29", "2026-05-29") == [
        {"uid": "m2", "date": "2026-05-29 00:00:00", "type": 1, "name": "Salad"}
    ]
    status = local.sync_status()
    assert status["category_count"] == 1
    assert status["grocery_list_count"] == 1
    assert status["grocery_item_count"] == 3
    assert status["meal_plan_count"] == 2


def test_schema_has_paprika_resource_tables_and_sync_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "paprika.sqlite"
    local = PaprikaLocalStore(db_path)
    client = FakePaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes")},
    )

    local.sync_all(client)

    expected_tables = [
        "recipes",
        "recipe_photos",
        "recipe_categories",
        "recipes_to_categories",
        "grocery_lists",
        "grocery_aisles",
        "grocery_ingredients",
        "grocery_items",
        "meal_types",
        "meals",
        "menus",
        "menu_items",
        "bookmarks",
        "pantry_items",
        "sync_status",
    ]
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert set(expected_tables).issubset(tables)
    for table in expected_tables:
        if table in {"recipes_to_categories", "sync_status"}:
            continue
        assert {"uid", "status", "is_synced", "sync_hash"}.issubset(
            table_columns(db_path, table)
        )

    assert table_count(db_path, "grocery_lists") == 1
    assert table_count(db_path, "grocery_items") == 3
    assert table_count(db_path, "meals") == 2
    assert table_count(db_path, "recipes_to_categories") == 1


def test_existing_generic_resource_cache_migrates_to_specific_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "paprika.sqlite"
    synced_at = "2026-05-29T12:00:00+00:00"
    category = {"uid": "c1", "name": "Breakfast"}

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sync_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE recipes (
                uid TEXT PRIMARY KEY,
                hash TEXT,
                name TEXT,
                ingredients TEXT,
                directions TEXT,
                description TEXT,
                nutritional_info TEXT,
                servings TEXT,
                difficulty TEXT,
                prep_time TEXT,
                cook_time TEXT,
                rating INTEGER,
                source TEXT,
                source_url TEXT,
                photo TEXT,
                photo_hash TEXT,
                image_url TEXT,
                categories_json TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT NOT NULL,
                in_trash INTEGER NOT NULL DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                synced_at TEXT NOT NULL
            );
            CREATE TABLE resources (
                kind TEXT NOT NULL,
                uid TEXT NOT NULL,
                name TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (kind, uid)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO resources (kind, uid, name, raw_json, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("categories", "c1", "Breakfast", json.dumps(category), synced_at),
        )

    local = PaprikaLocalStore(db_path)

    assert local.list_resources("categories") == [category]
    assert table_count(db_path, "recipe_categories") == 1
    assert {"status", "is_synced", "sync_hash"}.issubset(table_columns(db_path, "recipes"))
    assert {"status", "is_synced", "sync_hash"}.issubset(
        table_columns(db_path, "resources")
    )
