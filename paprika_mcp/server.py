import hashlib
import json
import uuid
from datetime import date as date_type
from datetime import datetime

import httpx
from fastmcp import FastMCP

from .client import PaprikaAPIError, PaprikaClient
from .config import get_settings, token_cache_path

mcp = FastMCP("Paprika Recipe Manager")

MEAL_TYPES = {0: "Breakfast", 1: "Lunch", 2: "Dinner", 3: "Snack"}
MEAL_TYPE_VALUES = {name.lower(): value for value, name in MEAL_TYPES.items()}


def _client() -> PaprikaClient:
    settings = get_settings()
    return PaprikaClient(
        email=settings.paprika_email,
        password=settings.paprika_password,
        token_cache_path=token_cache_path(),
    )


def _recipe_hash(recipe: dict) -> str:
    """Deterministic 64-char hex change-detection hash for a recipe object."""
    material = {key: value for key, value in recipe.items() if key != "hash"}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _fetch_recipe(client: PaprikaClient, uid: str) -> dict | None:
    """Return the stored recipe, or None if it does not exist yet."""
    try:
        recipe = client.get_recipe(uid)
    except PaprikaAPIError as exc:
        if "not found" in str(exc).lower():
            return None
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return recipe if isinstance(recipe, dict) and recipe.get("uid") else None


def _normalize_meal_type(value) -> int:
    """Accept 0-3 or Breakfast/Lunch/Dinner/Snack; return the numeric type."""
    if isinstance(value, bool):
        raise ValueError(f"invalid meal type: {value!r}")
    if isinstance(value, int) and value in MEAL_TYPES:
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() and int(text) in MEAL_TYPES:
            return int(text)
        if text.lower() in MEAL_TYPE_VALUES:
            return MEAL_TYPE_VALUES[text.lower()]
    raise ValueError(
        f"invalid meal type: {value!r} (expected 0-3 or one of "
        f"{', '.join(MEAL_TYPES.values())})"
    )


def _normalize_meal_date(value) -> str:
    """Accept YYYY-MM-DD or YYYY-MM-DD HH:MM:SS; return the full timestamp."""
    text = str(value).strip()
    try:
        if " " in text:
            datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            return text
        date_type.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"invalid meal date: {value!r} (expected YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)"
        ) from exc
    return f"{text} 00:00:00"


@mcp.tool()
def get_sync_status() -> dict:
    """Get sync status counters for all Paprika resource types.

    Returns change counters (not total counts) that increment on each
    modification. Useful for detecting which resource types have changed
    since the last sync.
    """
    return _client().get_sync_status()


@mcp.tool()
def list_recipes() -> list[dict]:
    """List all recipes as lightweight {uid, hash} pairs.

    Returns only uid and hash for each recipe — not full recipe data.
    Use get_recipe(uid) to fetch complete details for a specific recipe.
    The hash field can be used to detect changes without re-fetching unchanged recipes.
    """
    return _client().list_recipes()


@mcp.tool()
def get_recipe(uid: str) -> dict:
    """Get full details for a specific recipe by its UID.

    Returns the complete recipe object including name, ingredients, directions,
    notes, nutrition, timing, rating, categories, source, and metadata.

    Args:
        uid: The recipe's unique identifier (uppercase UUID4 format).
    """
    return _client().get_recipe(uid)


@mcp.tool()
def list_categories() -> list[dict]:
    """List all recipe categories.

    Returns category uid, name, order_flag, and parent_uid (for nested categories).
    Recipe objects reference categories by name, not UID.
    """
    return _client().list_categories()


@mcp.tool()
def list_grocery_lists() -> list[dict]:
    """List all grocery lists.

    Returns each list's uid, name, order_flag, is_default, and reminders_list fields.
    Use the uid from this response to filter grocery items by list.
    """
    return _client().list_grocery_lists()


@mcp.tool()
def list_grocery_items(list_uid: str, include_checked: bool = False) -> list[dict]:
    """List grocery items for a specific grocery list.

    Returns items with name, quantity, ingredient, aisle, purchased status,
    recipe reference, and list membership.

    Args:
        list_uid: Grocery list UID to filter items.
        include_checked: Include checked/purchased items when true.
    """
    items = _client().list_grocery_items()
    items = [item for item in items if item.get("list_uid") == list_uid]
    if not include_checked:
        items = [item for item in items if not item.get("purchased")]
    return items


@mcp.tool()
def list_meal_plans(start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """List meal plan entries, optionally filtered by date range.

    Returns meal entries with name, date, meal type, and optional recipe reference.
    Each entry includes a human-readable meal_type_name field in addition to the
    numeric type field (0=Breakfast, 1=Lunch, 2=Dinner, 3=Snack).

    Args:
        start_date: Optional start date (YYYY-MM-DD) inclusive.
        end_date: Optional end date (YYYY-MM-DD) inclusive.
    """
    meals = _client().list_meal_plans()
    if start_date or end_date:
        from datetime import date

        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None

        filtered: list[dict] = []
        for meal in meals:
            meal_date_str = meal.get("date", "").split(" ")[0]
            try:
                meal_date = date.fromisoformat(meal_date_str)
            except Exception:
                continue
            if start and meal_date < start:
                continue
            if end and meal_date > end:
                continue
            filtered.append(meal)
        meals = filtered
    for meal in meals:
        meal["meal_type_name"] = MEAL_TYPES.get(meal.get("type"), "Unknown")
    return meals


@mcp.tool()
def get_meals_for_date(date: str) -> list[dict]:
    """Get meal plan entries for a specific date (YYYY-MM-DD).

    Returns meals scheduled on the given date with an added meal_type_name field.
    """
    meals = _client().list_meal_plans()
    filtered: list[dict] = []
    for meal in meals:
        meal_date = meal.get("date", "").split(" ")[0]
        if meal_date == date:
            meal["meal_type_name"] = MEAL_TYPES.get(meal.get("type"), "Unknown")
            filtered.append(meal)
    return filtered


@mcp.tool()
def add_grocery_item(
    list_uid: str,
    name: str,
    quantity: str | None = None,
    instruction: str | None = None,
    purchased: bool = False,
    ingredient: str | None = None,
    order_flag: int = 0,
    separate: bool = False,
    recipe_uid: str | None = None,
) -> dict:
    """Add a grocery item to a specific list.

    Requires list_uid. If ingredient is not provided, it defaults to name.lower().
    """
    return _client().create_grocery_item(
        list_uid=list_uid,
        name=name,
        quantity=quantity,
        instruction=instruction,
        purchased=purchased,
        ingredient=ingredient,
        order_flag=order_flag,
        separate=separate,
        recipe_uid=recipe_uid,
    )


@mcp.tool()
def upsert_recipe(
    name: str,
    ingredients: str = "",
    directions: str = "",
    description: str = "",
    notes: str = "",
    nutritional_info: str = "",
    servings: str = "",
    difficulty: str = "",
    prep_time: str = "",
    cook_time: str = "",
    total_time: str = "",
    rating: int = 0,
    categories: list[str] = [],
    tags: list[str] = [],
    source: str = "",
    source_url: str = "",
    image_url: str = "",
    uid: str | None = None,
    in_trash: bool = False,
) -> dict:
    """Create or update a recipe.

    Omit `uid` to create a new recipe (a fresh uppercase UUID4 is generated).
    Pass an existing `uid` to update it: the stored recipe is fetched first and
    only the arguments you supply are overlaid, so unset arguments keep their
    stored values and `created` is preserved. An unknown `uid` creates a recipe
    with that uid.

    Because unset arguments fall back to the stored value, an existing text
    field cannot be cleared by passing "" — set it to a new value instead.

    Notes:
        - `categories` are matched by category NAME, not UID (see list_categories).
        - `source` / `source_url` preserve attribution; keep them when capturing
          a recipe from the web.
        - Soft-delete with `in_trash=True` — Paprika has no true delete endpoint
          for recipes. Pass `in_trash=False` on a later update to restore it.

    Returns {"created": bool, "recipe": {...}} where `recipe` is read back from
    the API after the write, so a returned recipe is always a verified write.
    """
    client = _client()

    existing: dict | None = None
    if uid:
        existing = _fetch_recipe(client, uid)
    else:
        uid = str(uuid.uuid4()).upper()
    created = existing is None
    stored_before = existing or {}

    def overlay(field: str, value, default):
        """Supplied (non-default) arguments win; otherwise keep the stored value."""
        if value != default:
            return value
        current = stored_before.get(field, default)
        return default if current is None else current

    recipe = {
        "uid": uid,
        "name": overlay("name", name, ""),
        "ingredients": overlay("ingredients", ingredients, ""),
        "directions": overlay("directions", directions, ""),
        "description": overlay("description", description, ""),
        "notes": overlay("notes", notes, ""),
        "nutritional_info": overlay("nutritional_info", nutritional_info, ""),
        "servings": overlay("servings", servings, ""),
        "difficulty": overlay("difficulty", difficulty, ""),
        "prep_time": overlay("prep_time", prep_time, ""),
        "cook_time": overlay("cook_time", cook_time, ""),
        "total_time": overlay("total_time", total_time, ""),
        "rating": overlay("rating", rating, 0),
        "categories": list(overlay("categories", categories, [])),
        "tags": list(overlay("tags", tags, [])),
        "source": overlay("source", source, ""),
        "source_url": overlay("source_url", source_url, ""),
        "image_url": overlay("image_url", image_url, ""),
        "photo": stored_before.get("photo") or "",
        "photo_hash": stored_before.get("photo_hash") or "",
        "photo_large": stored_before.get("photo_large"),
        "photo_url": stored_before.get("photo_url"),
        "created": stored_before.get("created") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "on_favorites": bool(stored_before.get("on_favorites", False)),
        "on_grocery_list": stored_before.get("on_grocery_list"),
        "in_trash": in_trash,
        "is_pinned": bool(stored_before.get("is_pinned", False)),
        "scale": stored_before.get("scale"),
    }
    if not recipe["name"]:
        raise ValueError("recipe name is required")
    recipe["hash"] = _recipe_hash(recipe)

    client.upsert_recipe(uid, recipe)

    stored = _fetch_recipe(client, uid)
    if stored is None:
        raise RuntimeError(f"recipe {uid} could not be read back after the write")
    if stored.get("uid") != uid or stored.get("name") != recipe["name"]:
        raise RuntimeError(
            f"readback mismatch for recipe {uid}: stored name "
            f"{stored.get('name')!r} != written name {recipe['name']!r}"
        )
    return {"created": created, "recipe": stored}


@mcp.tool()
def create_meal_plans(meals: list[dict]) -> dict:
    """Create or update meal plan entries.

    Each entry accepts:
        date:       "YYYY-MM-DD" (normalised to midnight) or "YYYY-MM-DD HH:MM:SS"
        type:       0-3 or "Breakfast" | "Lunch" | "Dinner" | "Snack"
        name:       meal display name (required unless recipe_uid is given)
        recipe_uid: link to a recipe (optional; omit for a text-only meal)
        uid:        existing entry uid to update (optional; generated otherwise)
        order_flag: display order within the same date/type (default 0)
        deleted:    True to soft-delete an existing entry (requires uid)

    Soft-delete is the only removal path — Paprika has no true delete endpoint
    for meal plan entries. Deleted entries are excluded from the created count.

    Returns {"created": N, "deleted": N, "meals": [...]} where `meals` are the
    entries read back from the API after the write.
    """
    if not meals:
        raise ValueError("meals must contain at least one entry")

    entries: list[dict] = []
    created_uids: list[str] = []
    deleted_uids: list[str] = []

    # Validate everything before any network call.
    for index, meal in enumerate(meals):
        if not isinstance(meal, dict):
            raise ValueError(f"meals[{index}] must be an object")
        deleted = bool(meal.get("deleted", False))
        uid = (meal.get("uid") or "").strip()
        if deleted and not uid:
            raise ValueError(f"meals[{index}] has deleted=true but no uid to delete")
        if not uid:
            uid = str(uuid.uuid4()).upper()
        name = (meal.get("name") or "").strip()
        recipe_uid = meal.get("recipe_uid") or None
        if not name and not recipe_uid:
            raise ValueError(f"meals[{index}] requires name or recipe_uid")

        entries.append(
            {
                "uid": uid,
                "recipe_uid": recipe_uid,
                "date": _normalize_meal_date(meal.get("date")),
                "type": _normalize_meal_type(meal.get("type")),
                "name": name,
                "order_flag": int(meal.get("order_flag", 0)),
                "type_uid": meal.get("type_uid", ""),
                "scale": meal.get("scale"),
                "is_ingredient": bool(meal.get("is_ingredient", False)),
                "deleted": deleted,
            }
        )
        (deleted_uids if deleted else created_uids).append(uid)

    client = _client()

    # Fill in a display name from the linked recipe when only recipe_uid is given.
    for entry in entries:
        if entry["name"] or not entry["recipe_uid"]:
            continue
        recipe = _fetch_recipe(client, entry["recipe_uid"])
        if recipe:
            entry["name"] = recipe.get("name", "")

    client.create_meal_plans(entries)

    stored = {meal.get("uid"): meal for meal in client.list_meal_plans()}
    missing = [uid for uid in created_uids if uid not in stored]
    if missing:
        raise RuntimeError(f"meal plan entries missing after write: {', '.join(missing)}")

    readback = []
    for uid in created_uids:
        meal = stored[uid]
        meal["meal_type_name"] = MEAL_TYPES.get(meal.get("type"), "Unknown")
        readback.append(meal)
    return {"created": len(readback), "deleted": len(deleted_uids), "meals": readback}


@mcp.tool()
def delete_grocery_item(item_uid: str) -> dict:
    """Delete a grocery item by marking it purchased.

    This is a soft delete — the Paprika API has no true delete endpoint for
    grocery items, so the item is set to `purchased: true` (checked off) and
    remains retrievable via list_grocery_items(include_checked=True).

    Args:
        item_uid: UID of the grocery item to delete.

    Returns {"deleted": true, "item": {...}} with the item read back from the
    API after the write.
    """
    client = _client()

    item = next(
        (candidate for candidate in client.list_grocery_items() if candidate.get("uid") == item_uid),
        None,
    )
    if item is None:
        raise ValueError(f"grocery item {item_uid} not found")

    item["purchased"] = True
    client.update_grocery_item(item)

    stored = next(
        (candidate for candidate in client.list_grocery_items() if candidate.get("uid") == item_uid),
        None,
    )
    if stored is None:
        raise RuntimeError(f"grocery item {item_uid} could not be read back after the write")
    if not stored.get("purchased"):
        raise RuntimeError(f"grocery item {item_uid} is still unpurchased after the write")
    return {"deleted": True, "item": stored}


def main() -> None:
    settings = get_settings()
    mcp.run(transport="http", host="127.0.0.1", port=settings.paprika_port)


if __name__ == "__main__":
    main()
