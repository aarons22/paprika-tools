from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from .client import PaprikaRetryExhausted


SCHEMA_VERSION = 4

DEFAULT_ROW_STATUS = "unmodified"
DEFAULT_IS_SYNCED = 1

RESOURCE_TABLES = {
    "categories": "recipe_categories",
    "recipe_photos": "recipe_photos",
    "grocery_lists": "grocery_lists",
    "grocery_aisles": "grocery_aisles",
    "grocery_ingredients": "grocery_ingredients",
    "grocery_items": "grocery_items",
    "meal_plans": "meals",
    "meal_types": "meal_types",
    "menus": "menus",
    "menu_items": "menu_items",
    "bookmarks": "bookmarks",
    "pantry": "pantry_items",
}

RESOURCE_SORT_COLUMNS = {
    "meals": "date",
}

RESOURCE_SYNC_GROUPS = [
    ("categories", "categories", "list_categories"),
    ("photos", "recipe_photos", "list_recipe_photos"),
    ("grocerylists", "grocery_lists", "list_grocery_lists"),
    ("groceryaisles", "grocery_aisles", "list_grocery_aisles"),
    ("groceryingredients", "grocery_ingredients", "list_grocery_ingredients"),
    ("groceries", "grocery_items", "list_grocery_items"),
    ("mealtypes", "meal_types", "list_meal_types"),
    ("meals", "meal_plans", "list_meal_plans"),
    ("menus", "menus", "list_menus"),
    ("menuitems", "menu_items", "list_menu_items"),
    ("bookmarks", "bookmarks", "list_bookmarks"),
    ("pantry", "pantry", "list_pantry_items"),
]


@dataclass(frozen=True)
class RecipeSyncSummary:
    total_remote: int
    fetched: int
    unchanged: int
    skipped: int
    removed: int
    pending: int
    failed: int
    failures: list[dict[str, str]]


class PaprikaLocalStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_status (
                    name TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recipe_sync_queue (
                    uid TEXT PRIMARY KEY,
                    hash TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recipes (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
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

                CREATE INDEX IF NOT EXISTS idx_recipes_hash ON recipes(hash);
                CREATE INDEX IF NOT EXISTS idx_recipes_name ON recipes(name);
                CREATE INDEX IF NOT EXISTS idx_recipes_source ON recipes(source);

                CREATE TABLE IF NOT EXISTS recipe_categories (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recipes_to_categories (
                    recipe_uid TEXT NOT NULL,
                    category_uid TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (recipe_uid, category_uid),
                    FOREIGN KEY (recipe_uid) REFERENCES recipes(uid) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS recipe_photos (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    filename TEXT,
                    photo_hash TEXT,
                    recipe_uid TEXT,
                    download_error_message TEXT,
                    is_downloaded INTEGER NOT NULL DEFAULT 0,
                    is_download_errored INTEGER NOT NULL DEFAULT 0,
                    is_uploaded INTEGER NOT NULL DEFAULT 1,
                    is_pending_deletion INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    FOREIGN KEY (recipe_uid) REFERENCES recipes(uid) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS grocery_lists (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    reminders_list TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS grocery_aisles (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS grocery_ingredients (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    aisle_uid TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS grocery_items (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    purchased INTEGER NOT NULL DEFAULT 0,
                    aisle TEXT,
                    ingredient TEXT,
                    aisle_uid TEXT,
                    list_uid TEXT,
                    recipe_uid TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meal_types (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meals (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    date TEXT,
                    type INTEGER,
                    type_uid TEXT,
                    recipe_uid TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS menus (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS menu_items (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    menu_uid TEXT,
                    recipe_uid TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bookmarks (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    url TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pantry_items (
                    uid TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    order_flag INTEGER,
                    aisle TEXT,
                    ingredient TEXT,
                    quantity TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS resources (
                    kind TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unmodified',
                    is_synced INTEGER NOT NULL DEFAULT 1,
                    sync_hash TEXT,
                    name TEXT,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (kind, uid)
                );

                CREATE INDEX IF NOT EXISTS idx_resources_kind_name
                    ON resources(kind, name);
                """
            )
            self._migrate_schema(conn)
            self._set_metadata(conn, "schema_version", str(SCHEMA_VERSION))

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        previous_version = self._schema_version(conn)
        self._ensure_columns(
            conn,
            "recipes",
            {
                "status": "TEXT NOT NULL DEFAULT 'unmodified'",
                "is_synced": "INTEGER NOT NULL DEFAULT 1",
                "sync_hash": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "resources",
            {
                "status": "TEXT NOT NULL DEFAULT 'unmodified'",
                "is_synced": "INTEGER NOT NULL DEFAULT 1",
                "sync_hash": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "recipe_photos",
            {
                "download_error_message": "TEXT",
                "is_download_errored": "INTEGER NOT NULL DEFAULT 0",
                "is_pending_deletion": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        if previous_version < 2:
            self._migrate_legacy_resources(conn)

    def _schema_version(self, conn: sqlite3.Connection) -> int:
        try:
            row = conn.execute(
                "SELECT value FROM sync_metadata WHERE key = 'schema_version'"
            ).fetchone()
            return int(row["value"]) if row else 0
        except Exception:
            return 0

    def _ensure_columns(
        self,
        conn: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_legacy_resources(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT kind, uid, name, raw_json, synced_at, status, is_synced, sync_hash
            FROM resources
            """
        ).fetchall()
        for row in rows:
            table = RESOURCE_TABLES.get(row["kind"])
            if not table:
                continue
            item = parse_json(row["raw_json"])
            if not isinstance(item, dict):
                item = {"uid": row["uid"], "name": row["name"]}
            self._upsert_resource_row(
                conn,
                row["kind"],
                item,
                row["synced_at"],
                uid=row["uid"],
                raw_json=row["raw_json"],
                status=row["status"],
                is_synced=row["is_synced"],
                sync_hash=row["sync_hash"],
            )

    def sync_recipes(self, client: Any) -> RecipeSyncSummary:
        remote_stubs = client.list_recipes()
        remote_by_uid = {
            str(stub["uid"]): stub.get("hash")
            for stub in remote_stubs
            if isinstance(stub, dict) and stub.get("uid")
        }
        now = utc_now()
        fetched = 0
        failures: list[dict[str, str]] = []

        with self._connect() as conn:
            local_hashes = self._local_hashes(conn)
            unchanged = sum(
                1
                for uid, remote_hash in remote_by_uid.items()
                if uid in local_hashes and local_hashes[uid] == remote_hash
            )
            self._prepare_recipe_queue(conn, remote_by_uid, local_hashes, now)
            pending_rows = self._pending_recipe_queue(conn)

        retry_exhausted = False
        for row in pending_rows:
            uid = row["uid"]
            remote_hash = row["hash"]
            try:
                recipe = client.get_recipe(uid)
            except PaprikaRetryExhausted as exc:
                retry_exhausted = True
                failures.append({"uid": uid, "error": str(exc)})
                with self._connect() as conn:
                    self._mark_recipe_queue(conn, uid, "pending", str(exc))
                break
            except Exception as exc:
                failures.append({"uid": uid, "error": str(exc)})
                with self._connect() as conn:
                    self._mark_recipe_queue(conn, uid, "failed", str(exc))
                continue
            with self._connect() as conn:
                self._upsert_recipe(conn, uid, remote_hash, recipe, utc_now())
                self._mark_recipe_queue(conn, uid, "complete", None)
                fetched += 1

        with self._connect() as conn:
            pending = self._recipe_queue_count(conn, "pending")
            removed = self._remove_missing(conn, remote_by_uid.keys())
            self._set_metadata(conn, "last_recipe_sync_at", now)
            self._set_metadata(
                conn,
                "last_recipe_sync_summary",
                json.dumps(
                    {
                        "total_remote": len(remote_by_uid),
                        "fetched": fetched,
                        "unchanged": unchanged,
                        "skipped": unchanged,
                        "removed": removed,
                        "pending": pending,
                        "failed": len(failures),
                        "failures": failures,
                        "retry_exhausted": retry_exhausted,
                    },
                    sort_keys=True,
                ),
            )

        return RecipeSyncSummary(
            total_remote=len(remote_by_uid),
            fetched=fetched,
            unchanged=unchanged,
            skipped=unchanged,
            removed=removed,
            pending=pending,
            failed=len(failures),
            failures=failures,
        )

    def sync_all(self, client: Any) -> dict[str, Any]:
        remote_revisions = self._remote_revisions(client)
        resources: dict[str, dict[str, Any]] = {}

        recipe_revision = remote_revisions.get("recipes")
        if recipe_revision is not None and self._revision_unchanged(
            "recipes", recipe_revision
        ) and not self._has_unfinished_recipes():
            recipes = self._skipped_recipe_summary()
        else:
            recipes = self.sync_recipes(client)
            if recipe_revision is not None and recipes.pending == 0 and recipes.failed == 0:
                self._set_revision("recipes", recipe_revision)

        for revision_name, kind, method_name in RESOURCE_SYNC_GROUPS:
            method = getattr(client, method_name, None)
            if method is None:
                continue
            revision = remote_revisions.get(revision_name)
            if revision is not None and self._revision_unchanged(revision_name, revision):
                resources[kind] = {"count": self._resource_count(kind), "skipped": True}
                continue
            resources[kind] = self.replace_resources(kind, method())
            if revision is not None:
                self._set_revision(revision_name, revision)

        return {
            "recipes": {
                "total_remote": recipes.total_remote,
                "fetched": recipes.fetched,
                "unchanged": recipes.unchanged,
                "skipped": recipes.skipped,
                "removed": recipes.removed,
                "pending": recipes.pending,
                "failed": recipes.failed,
                "failures": recipes.failures,
            },
            "resources": resources,
        }

    def _prepare_recipe_queue(
        self,
        conn: sqlite3.Connection,
        remote_by_uid: dict[str, str | None],
        local_hashes: dict[str, str | None],
        now: str,
    ) -> None:
        remote_uids = set(remote_by_uid)
        conn.execute(
            "DELETE FROM recipe_sync_queue WHERE uid NOT IN (%s)"
            % ",".join("?" for _ in remote_uids),
            tuple(remote_uids),
        ) if remote_uids else conn.execute("DELETE FROM recipe_sync_queue")
        for uid, remote_hash in remote_by_uid.items():
            if uid in local_hashes and local_hashes[uid] == remote_hash:
                self._mark_recipe_queue(conn, uid, "complete", None, remote_hash)
                continue
            conn.execute(
                """
                INSERT INTO recipe_sync_queue (uid, hash, status, error, updated_at)
                VALUES (?, ?, 'pending', NULL, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    hash = excluded.hash,
                    status = CASE
                        WHEN recipe_sync_queue.hash IS NOT excluded.hash THEN 'pending'
                        WHEN recipe_sync_queue.status = 'complete' THEN 'complete'
                        ELSE 'pending'
                    END,
                    error = CASE
                        WHEN recipe_sync_queue.status = 'complete'
                             AND recipe_sync_queue.hash IS excluded.hash
                        THEN recipe_sync_queue.error
                        ELSE NULL
                    END,
                    updated_at = excluded.updated_at
                """,
                (uid, remote_hash, now),
            )

    def _pending_recipe_queue(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT uid, hash
            FROM recipe_sync_queue
            WHERE status = 'pending'
            ORDER BY uid
            """
        ).fetchall()

    def _mark_recipe_queue(
        self,
        conn: sqlite3.Connection,
        uid: str,
        status: str,
        error: str | None,
        recipe_hash: str | None = None,
    ) -> None:
        if recipe_hash is None:
            conn.execute(
                """
                UPDATE recipe_sync_queue
                SET status = ?, error = ?, updated_at = ?
                WHERE uid = ?
                """,
                (status, error, utc_now(), uid),
            )
            return
        conn.execute(
            """
            INSERT INTO recipe_sync_queue (uid, hash, status, error, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                hash = excluded.hash,
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (uid, recipe_hash, status, error, utc_now()),
        )

    def _recipe_queue_count(self, conn: sqlite3.Connection, status: str) -> int:
        return int(
            conn.execute(
                "SELECT count(*) AS count FROM recipe_sync_queue WHERE status = ?",
                (status,),
            ).fetchone()["count"]
        )

    def _has_unfinished_recipes(self) -> bool:
        with self._connect() as conn:
            return (
                int(
                    conn.execute(
                        """
                        SELECT count(*) AS count
                        FROM recipe_sync_queue
                        WHERE status IN ('pending', 'failed')
                        """
                    ).fetchone()["count"]
                )
                > 0
            )

    def _skipped_recipe_summary(self) -> RecipeSyncSummary:
        with self._connect() as conn:
            recipe_count = int(
                conn.execute(
                    "SELECT count(*) AS count FROM recipes WHERE in_trash = 0"
                ).fetchone()["count"]
            )
        return RecipeSyncSummary(
            total_remote=recipe_count,
            fetched=0,
            unchanged=recipe_count,
            skipped=recipe_count,
            removed=0,
            pending=0,
            failed=0,
            failures=[],
        )

    def _remote_revisions(self, client: Any) -> dict[str, int]:
        get_sync_status = getattr(client, "get_sync_status", None)
        if get_sync_status is None:
            return {}
        status = get_sync_status()
        if not isinstance(status, dict):
            return {}
        revisions: dict[str, int] = {}
        for name, revision in status.items():
            try:
                revisions[str(name)] = int(revision)
            except (TypeError, ValueError):
                continue
        return revisions

    def _revision_unchanged(self, name: str, revision: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT revision FROM sync_status WHERE name = ?",
                (name,),
            ).fetchone()
        return bool(row and int(row["revision"]) == revision)

    def _set_revision(self, name: str, revision: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_status (name, revision, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    revision = excluded.revision,
                    updated_at = excluded.updated_at
                """,
                (name, revision, utc_now()),
            )

    def replace_resources(self, kind: str, items: list[dict[str, Any]]) -> dict[str, int]:
        now = utc_now()
        inserted = 0
        table = RESOURCE_TABLES.get(kind)
        with self._connect() as conn:
            conn.execute("DELETE FROM resources WHERE kind = ?", (kind,))
            if table:
                conn.execute(f"DELETE FROM {table}")
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw_json = json.dumps(item, sort_keys=True)
                uid = resource_uid(item, raw_json)
                conn.execute(
                    """
                    INSERT INTO resources (
                        kind, uid, status, is_synced, sync_hash, name, raw_json, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kind,
                        uid,
                        resource_status(item),
                        resource_is_synced(item),
                        text(item.get("sync_hash")),
                        text(item.get("name")),
                        raw_json,
                        now,
                    ),
                )
                if table:
                    self._upsert_resource_row(
                        conn,
                        kind,
                        item,
                        now,
                        uid=uid,
                        raw_json=raw_json,
                    )
                inserted += 1
            self._set_metadata(conn, f"last_{kind}_sync_at", now)
        return {"count": inserted}

    def list_recipes(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT uid, hash, name
                FROM recipes
                WHERE in_trash = 0
                ORDER BY lower(coalesce(name, uid)), uid
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _upsert_resource_row(
        self,
        conn: sqlite3.Connection,
        kind: str,
        item: dict[str, Any],
        synced_at: str,
        uid: str | None = None,
        raw_json: str | None = None,
        status: str | None = None,
        is_synced: int | None = None,
        sync_hash: str | None = None,
    ) -> None:
        table = RESOURCE_TABLES.get(kind)
        if not table:
            return
        if raw_json is None:
            raw_json = json.dumps(item, sort_keys=True)
        if uid is None:
            uid = resource_uid(item, raw_json)

        values: dict[str, Any] = {
            "uid": uid,
            "status": status or resource_status(item),
            "is_synced": resource_is_synced(item) if is_synced is None else is_synced,
            "sync_hash": text(item.get("sync_hash")) if sync_hash is None else sync_hash,
            "name": text(item.get("name")),
            "order_flag": int_or_none(item.get("order_flag")),
            "raw_json": raw_json,
            "synced_at": synced_at,
            "is_default": bool_int(item.get("is_default")),
            "reminders_list": text(item.get("reminders_list")),
            "aisle_uid": text(item.get("aisle_uid")),
            "purchased": bool_int(item.get("purchased")),
            "aisle": text(item.get("aisle")),
            "ingredient": text(item.get("ingredient")),
            "list_uid": text(item.get("list_uid")),
            "recipe_uid": text(item.get("recipe_uid")),
            "date": text(item.get("date")),
            "type": int_or_none(item.get("type")),
            "type_uid": text(item.get("type_uid")),
            "menu_uid": text(item.get("menu_uid")),
            "url": text(item.get("url")),
            "quantity": text(item.get("quantity")),
            "filename": text(item.get("filename") or item.get("photo")),
            "photo_hash": text(item.get("photo_hash")),
            "download_error_message": text(item.get("download_error_message")),
            "is_downloaded": bool_int(item.get("is_downloaded")),
            "is_download_errored": bool_int(item.get("is_download_errored")),
            "is_uploaded": bool_int(item.get("is_uploaded"), default=1),
            "is_pending_deletion": bool_int(item.get("is_pending_deletion")),
        }
        columns = [
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            if row["name"] in values
        ]
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(
            f"{column} = excluded.{column}" for column in columns if column != "uid"
        )
        conn.execute(
            f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(uid) DO UPDATE SET {assignments}
            """,
            tuple(values[column] for column in columns),
        )

    def list_resources(self, kind: str) -> list[dict[str, Any]]:
        table = RESOURCE_TABLES.get(kind)
        with self._connect() as conn:
            if table:
                order_column = RESOURCE_SORT_COLUMNS.get(table, "name")
                rows = conn.execute(
                    f"""
                    SELECT raw_json
                    FROM {table}
                    ORDER BY lower(coalesce({order_column}, name, uid)), uid
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT raw_json
                    FROM resources
                    WHERE kind = ?
                    ORDER BY lower(coalesce(name, uid)), uid
                    """,
                    (kind,),
                ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]

    def list_recipe_photos(self, recipe_uid: str | None = None) -> list[dict[str, Any]]:
        photos = self.list_resources("recipe_photos")
        if recipe_uid is None:
            return photos
        return [photo for photo in photos if photo.get("recipe_uid") == recipe_uid]

    def list_grocery_items(
        self, list_uid: str, include_checked: bool = False
    ) -> list[dict[str, Any]]:
        items = self.list_resources("grocery_items")
        items = [item for item in items if item.get("list_uid") == list_uid]
        if not include_checked:
            items = [item for item in items if not item.get("purchased")]
        return items

    def list_meal_plans(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict[str, Any]]:
        meals = self.list_resources("meal_plans")
        if not start_date and not end_date:
            return meals

        filtered: list[dict[str, Any]] = []
        for meal in meals:
            meal_date = str(meal.get("date", "")).split(" ")[0]
            if start_date and meal_date < start_date:
                continue
            if end_date and meal_date > end_date:
                continue
            filtered.append(meal)
        return filtered

    def get_recipe(self, uid: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM recipes WHERE uid = ? AND in_trash = 0",
                (uid,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["raw_json"])

    def search_recipes(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        pattern = f"%{query.lower()}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT uid, hash, name, categories_json
                FROM recipes
                WHERE in_trash = 0
                  AND (
                    lower(coalesce(name, '')) LIKE ?
                    OR lower(coalesce(ingredients, '')) LIKE ?
                    OR lower(coalesce(directions, '')) LIKE ?
                    OR lower(coalesce(description, '')) LIKE ?
                    OR lower(coalesce(source, '')) LIKE ?
                    OR lower(coalesce(categories_json, '')) LIKE ?
                  )
                ORDER BY lower(coalesce(name, uid)), uid
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
        return [
            {
                "uid": row["uid"],
                "hash": row["hash"],
                "name": row["name"],
                "categories": json.loads(row["categories_json"]),
            }
            for row in rows
        ]

    def sync_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            metadata = {
                row["key"]: row["value"]
                for row in conn.execute("SELECT key, value FROM sync_metadata").fetchall()
            }
            recipe_count = conn.execute(
                "SELECT count(*) AS count FROM recipes WHERE in_trash = 0"
            ).fetchone()["count"]
            revisions = {
                row["name"]: row["revision"]
                for row in conn.execute(
                    "SELECT name, revision FROM sync_status ORDER BY name"
                ).fetchall()
            }
            pending_recipe_count = self._recipe_queue_count(conn, "pending")
        return {
            "db_path": str(self.db_path),
            "recipe_count": recipe_count,
            "category_count": self._resource_count("categories"),
            "grocery_list_count": self._resource_count("grocery_lists"),
            "grocery_item_count": self._resource_count("grocery_items"),
            "meal_plan_count": self._resource_count("meal_plans"),
            "resource_revisions": revisions,
            "pending_recipe_count": pending_recipe_count,
            "last_recipe_sync_at": metadata.get("last_recipe_sync_at"),
            "last_recipe_sync_summary": parse_json(metadata.get("last_recipe_sync_summary")),
        }

    def _resource_count(self, kind: str) -> int:
        table = RESOURCE_TABLES.get(kind)
        with self._connect() as conn:
            if table:
                return int(
                    conn.execute(f"SELECT count(*) AS count FROM {table}").fetchone()["count"]
                )
            return int(
                conn.execute(
                    "SELECT count(*) AS count FROM resources WHERE kind = ?", (kind,)
                ).fetchone()["count"]
            )

    def _local_hashes(self, conn: sqlite3.Connection) -> dict[str, str | None]:
        rows = conn.execute("SELECT uid, hash FROM recipes").fetchall()
        return {row["uid"]: row["hash"] for row in rows}

    def _upsert_recipe(
        self,
        conn: sqlite3.Connection,
        uid: str,
        recipe_hash: str | None,
        recipe: dict[str, Any],
        synced_at: str,
    ) -> None:
        recipe_uid = str(recipe.get("uid") or uid)
        categories = recipe.get("categories")
        if not isinstance(categories, list):
            categories = []
        conn.execute(
            """
            INSERT INTO recipes (
                uid, status, is_synced, sync_hash, hash,
                name, ingredients, directions, description,
                nutritional_info, servings, difficulty, prep_time, cook_time,
                rating, source, source_url, photo, photo_hash, image_url,
                categories_json, raw_json, in_trash, created_at, updated_at, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                status = excluded.status,
                is_synced = excluded.is_synced,
                sync_hash = excluded.sync_hash,
                hash = excluded.hash,
                name = excluded.name,
                ingredients = excluded.ingredients,
                directions = excluded.directions,
                description = excluded.description,
                nutritional_info = excluded.nutritional_info,
                servings = excluded.servings,
                difficulty = excluded.difficulty,
                prep_time = excluded.prep_time,
                cook_time = excluded.cook_time,
                rating = excluded.rating,
                source = excluded.source,
                source_url = excluded.source_url,
                photo = excluded.photo,
                photo_hash = excluded.photo_hash,
                image_url = excluded.image_url,
                categories_json = excluded.categories_json,
                raw_json = excluded.raw_json,
                in_trash = excluded.in_trash,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                synced_at = excluded.synced_at
            """,
            (
                recipe_uid,
                resource_status(recipe),
                resource_is_synced(recipe),
                text(recipe.get("sync_hash")),
                recipe_hash,
                text(recipe.get("name")),
                text(recipe.get("ingredients")),
                text(recipe.get("directions")),
                text(recipe.get("description")),
                text(recipe.get("nutritional_info")),
                text(recipe.get("servings")),
                text(recipe.get("difficulty")),
                text(recipe.get("prep_time")),
                text(recipe.get("cook_time")),
                int_or_none(recipe.get("rating")),
                text(recipe.get("source")),
                text(recipe.get("source_url")),
                text(recipe.get("photo")),
                text(recipe.get("photo_hash")),
                text(recipe.get("image_url")),
                json.dumps(categories, sort_keys=True),
                json.dumps(recipe, sort_keys=True),
                1 if recipe.get("in_trash") else 0,
                text(recipe.get("created")),
                text(recipe.get("updated")),
                synced_at,
            ),
        )
        self._replace_recipe_category_links(conn, recipe_uid, categories, synced_at)
        self._upsert_recipe_photo_metadata(conn, recipe_uid, recipe, synced_at)

    def _replace_recipe_category_links(
        self,
        conn: sqlite3.Connection,
        recipe_uid: str,
        categories: list[Any],
        synced_at: str,
    ) -> None:
        conn.execute(
            "DELETE FROM recipes_to_categories WHERE recipe_uid = ?",
            (recipe_uid,),
        )
        for category in categories:
            category_name = text(category)
            if not category_name:
                continue
            category_uid = sha256(category_name.encode("utf-8")).hexdigest()
            raw_json = json.dumps(
                {"recipe_uid": recipe_uid, "category": category_name},
                sort_keys=True,
            )
            conn.execute(
                """
                INSERT INTO recipes_to_categories (
                    recipe_uid, category_uid, status, is_synced, sync_hash,
                    raw_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_uid, category_uid) DO UPDATE SET
                    status = excluded.status,
                    is_synced = excluded.is_synced,
                    sync_hash = excluded.sync_hash,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at
                """,
                (
                    recipe_uid,
                    category_uid,
                    DEFAULT_ROW_STATUS,
                    DEFAULT_IS_SYNCED,
                    None,
                    raw_json,
                    synced_at,
                ),
            )

    def _upsert_recipe_photo_metadata(
        self,
        conn: sqlite3.Connection,
        recipe_uid: str,
        recipe: dict[str, Any],
        synced_at: str,
    ) -> None:
        photo = recipe.get("photo")
        photo_hash = recipe.get("photo_hash")
        if not photo and not photo_hash:
            conn.execute("DELETE FROM recipe_photos WHERE recipe_uid = ?", (recipe_uid,))
            return
        item = {
            "uid": photo or f"{recipe_uid}:photo",
            "name": photo,
            "filename": photo,
            "photo_hash": photo_hash,
            "recipe_uid": recipe_uid,
            "is_downloaded": recipe.get("photo_is_downloaded", False),
            "is_uploaded": recipe.get("photo_is_uploaded", True),
        }
        self._upsert_resource_row(
            conn,
            "recipe_photos",
            item,
            synced_at,
            raw_json=json.dumps(item, sort_keys=True),
        )

    def _remove_missing(self, conn: sqlite3.Connection, remote_uids: Iterable[str]) -> int:
        remote_uid_list = list(remote_uids)
        if not remote_uid_list:
            row = conn.execute("SELECT count(*) AS count FROM recipes").fetchone()
            conn.execute("DELETE FROM recipes")
            return int(row["count"])

        placeholders = ",".join("?" for _ in remote_uid_list)
        row = conn.execute(
            f"SELECT count(*) AS count FROM recipes WHERE uid NOT IN ({placeholders})",
            remote_uid_list,
        ).fetchone()
        conn.execute(
            f"DELETE FROM recipes WHERE uid NOT IN ({placeholders})",
            remote_uid_list,
        )
        return int(row["count"])

    def _set_metadata(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO sync_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def bool_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "y"} else 0
    return 1 if bool(value) else 0


def resource_status(item: dict[str, Any]) -> str:
    return text(item.get("status")) or DEFAULT_ROW_STATUS


def resource_is_synced(item: dict[str, Any]) -> int:
    return bool_int(item.get("is_synced"), default=DEFAULT_IS_SYNCED)


def resource_uid(item: dict[str, Any], raw_json: str) -> str:
    uid = item.get("uid") or item.get("id")
    if uid:
        return str(uid)
    return sha256(raw_json.encode("utf-8")).hexdigest()
