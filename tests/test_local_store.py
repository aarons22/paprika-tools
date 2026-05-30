from __future__ import annotations

import sqlite3
from pathlib import Path

from paprika_mcp.client import PaprikaRetryExhausted
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


class RevisionPaprikaClient(FakePaprikaClient):
    def __init__(
        self,
        stubs: list[dict],
        recipes: dict[str, dict],
        revisions: dict[str, int],
    ) -> None:
        super().__init__(stubs, recipes)
        self.revisions = revisions
        self.sync_status_calls = 0
        self.category_calls = 0
        self.grocery_list_calls = 0
        self.grocery_item_calls = 0
        self.meal_plan_calls = 0

    def get_sync_status(self) -> dict[str, int]:
        self.sync_status_calls += 1
        return self.revisions

    def list_categories(self) -> list[dict]:
        self.category_calls += 1
        return super().list_categories()

    def list_grocery_lists(self) -> list[dict]:
        self.grocery_list_calls += 1
        return super().list_grocery_lists()

    def list_grocery_items(self) -> list[dict]:
        self.grocery_item_calls += 1
        return super().list_grocery_items()

    def list_meal_plans(self) -> list[dict]:
        self.meal_plan_calls += 1
        return super().list_meal_plans()


class InterruptingPaprikaClient(FakePaprikaClient):
    def __init__(self, stubs: list[dict], recipes: dict[str, dict], fail_uid: str) -> None:
        super().__init__(stubs, recipes)
        self.fail_uid = fail_uid
        self.failed_once = False

    def get_recipe(self, uid: str) -> dict:
        self.get_recipe_calls.append(uid)
        if uid == self.fail_uid and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("interrupted")
        return self.recipes[uid]


class RetryExhaustedPaprikaClient(FakePaprikaClient):
    def get_recipe(self, uid: str) -> dict:
        self.get_recipe_calls.append(uid)
        raise PaprikaRetryExhausted(503, 3)


class PhotoPaprikaClient(FakePaprikaClient):
    def __init__(self, stubs: list[dict], recipes: dict[str, dict]) -> None:
        super().__init__(stubs, recipes)
        self.photo_calls = 0
        self.download_photo_calls = 0

    def get_sync_status(self) -> dict[str, int]:
        return {"recipes": 1, "photos": 2}

    def list_recipe_photos(self) -> list[dict]:
        self.photo_calls += 1
        return [
            {
                "uid": "p1",
                "name": "front",
                "filename": "front.jpg",
                "photo_hash": "hash1",
                "recipe_uid": "r1",
                "is_downloaded": False,
                "is_download_errored": True,
                "download_error_message": "not downloaded",
                "is_uploaded": True,
                "is_pending_deletion": False,
                "sync_hash": "A" * 64,
            }
        ]

    def download_photo(self, uid: str) -> bytes:
        self.download_photo_calls += 1
        raise AssertionError(f"unexpected photo binary download: {uid}")


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


def test_sync_all_uses_revision_gating_for_first_changed_and_unchanged_syncs(
    tmp_path: Path,
) -> None:
    local = store(tmp_path)
    first = RevisionPaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes")},
        {
            "recipes": 10,
            "categories": 20,
            "grocerylists": 30,
            "groceries": 40,
            "meals": 50,
        },
    )

    first_summary = local.sync_all(first)

    assert first_summary["recipes"]["fetched"] == 1
    assert first.category_calls == 1
    assert first.grocery_list_calls == 1
    assert first.grocery_item_calls == 1
    assert first.meal_plan_calls == 1
    assert local.sync_status()["resource_revisions"] == {
        "categories": 20,
        "groceries": 40,
        "grocerylists": 30,
        "meals": 50,
        "recipes": 10,
    }

    unchanged = RevisionPaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes")},
        first.revisions,
    )

    unchanged_summary = local.sync_all(unchanged)

    assert unchanged.get_recipe_calls == []
    assert unchanged.category_calls == 0
    assert unchanged.grocery_list_calls == 0
    assert unchanged.grocery_item_calls == 0
    assert unchanged.meal_plan_calls == 0
    assert unchanged_summary["resources"]["categories"]["skipped"] is True
    assert unchanged_summary["recipes"]["fetched"] == 0
    assert unchanged_summary["recipes"]["skipped"] == 1

    changed = RevisionPaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes")},
        {**first.revisions, "categories": 21},
    )

    changed_summary = local.sync_all(changed)

    assert changed.category_calls == 1
    assert changed.grocery_list_calls == 0
    assert changed_summary["resources"]["categories"] == {"count": 1}
    assert local.sync_status()["resource_revisions"]["categories"] == 21


def test_interrupted_recipe_sync_resumes_without_refetching_completed_details(
    tmp_path: Path,
) -> None:
    local = store(tmp_path)
    stubs = [{"uid": "r1", "hash": "h1"}, {"uid": "r2", "hash": "h2"}]
    recipes = {"r1": recipe("r1", "Pancakes"), "r2": recipe("r2", "Soup")}
    interrupted = InterruptingPaprikaClient(stubs, recipes, fail_uid="r2")

    first = local.sync_recipes(interrupted)

    assert first.fetched == 1
    assert first.failed == 1
    assert first.pending == 0
    assert interrupted.get_recipe_calls == ["r1", "r2"]

    resumed = FakePaprikaClient(stubs, recipes)
    second = local.sync_recipes(resumed)

    assert second.fetched == 1
    assert second.unchanged == 1
    assert resumed.get_recipe_calls == ["r2"]
    assert local.list_recipes() == [
        {"uid": "r1", "hash": "h1", "name": "Pancakes"},
        {"uid": "r2", "hash": "h2", "name": "Soup"},
    ]


def test_retry_exhaustion_preserves_pending_recipe_progress(tmp_path: Path) -> None:
    local = store(tmp_path)
    stubs = [{"uid": "r1", "hash": "h1"}]
    failing = RetryExhaustedPaprikaClient(stubs, {"r1": recipe("r1", "Pancakes")})

    first = local.sync_recipes(failing)

    assert first.fetched == 0
    assert first.failed == 1
    assert first.pending == 1
    assert local.sync_status()["pending_recipe_count"] == 1

    resumed = FakePaprikaClient(stubs, {"r1": recipe("r1", "Pancakes")})
    second = local.sync_recipes(resumed)

    assert second.fetched == 1
    assert second.pending == 0
    assert resumed.get_recipe_calls == ["r1"]
    assert local.get_recipe("r1")["name"] == "Pancakes"


def test_sync_now_stores_photo_metadata_without_downloading_binaries(
    tmp_path: Path,
) -> None:
    local = store(tmp_path)
    client = PhotoPaprikaClient(
        [{"uid": "r1", "hash": "h1"}],
        {"r1": recipe("r1", "Pancakes")},
    )

    summary = local.sync_all(client)

    assert summary["resources"]["recipe_photos"] == {"count": 1}
    assert client.photo_calls == 1
    assert client.download_photo_calls == 0
    assert local.list_recipe_photos() == [
        {
            "uid": "p1",
            "name": "front",
            "filename": "front.jpg",
            "photo_hash": "hash1",
            "recipe_uid": "r1",
            "is_downloaded": False,
            "is_download_errored": True,
            "download_error_message": "not downloaded",
            "is_uploaded": True,
            "is_pending_deletion": False,
            "sync_hash": "A" * 64,
        }
    ]
    assert local.list_recipe_photos("r1")[0]["uid"] == "p1"

    with sqlite3.connect(local.db_path) as conn:
        row = conn.execute(
            """
            SELECT filename, photo_hash, recipe_uid, is_downloaded,
                   is_download_errored, download_error_message, is_uploaded,
                   is_pending_deletion, sync_hash
            FROM recipe_photos
            WHERE uid = 'p1'
            """
        ).fetchone()

    assert row == (
        "front.jpg",
        "hash1",
        "r1",
        0,
        1,
        "not downloaded",
        1,
        0,
        "A" * 64,
    )
