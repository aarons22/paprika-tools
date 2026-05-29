from fastmcp import FastMCP

from .client import PaprikaClient
from .config import get_settings, token_cache_path
from .local_store import PaprikaLocalStore

mcp = FastMCP("Paprika Recipe Manager")

MEAL_TYPES = {0: "Breakfast", 1: "Lunch", 2: "Dinner", 3: "Snack"}


def _client() -> PaprikaClient:
    settings = get_settings()
    return PaprikaClient(
        email=settings.paprika_email,
        password=settings.paprika_password,
        token_cache_path=token_cache_path(),
        user_agent=settings.paprika_user_agent,
        max_retries=settings.paprika_max_retries,
        retry_backoff_base=settings.paprika_retry_backoff_base,
        retry_backoff_max=settings.paprika_retry_backoff_max,
        retry_jitter=settings.paprika_retry_jitter,
    )


def _store() -> PaprikaLocalStore:
    settings = get_settings()
    return PaprikaLocalStore(settings.paprika_db_path)


@mcp.tool()
def get_sync_status() -> dict:
    """Get sync status counters for all Paprika resource types.

    Returns change counters (not total counts) that increment on each
    modification. Useful for detecting which resource types have changed
    since the last sync.
    """
    return _client().get_sync_status()


@mcp.tool()
def get_local_sync_status() -> dict:
    """Get local SQLite sync status and cached recipe counts."""
    return _store().sync_status()


@mcp.tool()
def sync_recipes() -> dict:
    """Sync changed Paprika recipes into the local SQLite database.

    This calls Paprika's lightweight recipe list endpoint, compares remote
    hashes against the local SQLite cache, and fetches full recipe data only
    for recipes that are new or changed.
    """
    summary = _store().sync_recipes(_client())
    return {
        "total_remote": summary.total_remote,
        "fetched": summary.fetched,
        "unchanged": summary.unchanged,
        "skipped": summary.skipped,
        "removed": summary.removed,
        "pending": summary.pending,
        "failed": summary.failed,
        "failures": summary.failures,
    }


@mcp.tool()
def sync_now() -> dict:
    """Sync recipes and read-only resource lists into the local SQLite database."""
    return _store().sync_all(_client())


@mcp.tool()
def list_recipes() -> list[dict]:
    """List locally synced recipes as lightweight {uid, hash, name} rows.

    This reads from SQLite and does not call Paprika's cloud API. Run
    sync_recipes() first to populate or refresh the local cache.
    """
    return _store().list_recipes()


@mcp.tool()
def get_recipe(uid: str) -> dict:
    """Get locally synced full details for a specific recipe by UID.

    This reads from SQLite and does not call Paprika's cloud API. Run
    sync_recipes() first to populate or refresh the local cache.

    Args:
        uid: The recipe's unique identifier (uppercase UUID4 format).
    """
    recipe = _store().get_recipe(uid)
    if recipe is None:
        raise ValueError(f"Recipe not found in local cache: {uid}")
    return recipe


@mcp.tool()
def search_recipes(query: str, limit: int = 25) -> list[dict]:
    """Search locally synced recipes by name, ingredients, directions, source, or categories.

    This reads from SQLite and does not call Paprika's cloud API. Run
    sync_recipes() first to populate or refresh the local cache.
    """
    return _store().search_recipes(query=query, limit=limit)


@mcp.tool()
def list_categories() -> list[dict]:
    """List locally synced recipe categories.

    Run sync_now() first to populate or refresh the local cache.
    """
    return _store().list_resources("categories")


@mcp.tool()
def list_grocery_lists() -> list[dict]:
    """List locally synced grocery lists.

    Run sync_now() first to populate or refresh the local cache.
    """
    return _store().list_resources("grocery_lists")


@mcp.tool()
def list_grocery_items(list_uid: str, include_checked: bool = False) -> list[dict]:
    """List locally synced grocery items for a specific grocery list.

    Run sync_now() first to populate or refresh the local cache.

    Args:
        list_uid: Grocery list UID to filter items.
        include_checked: Include checked/purchased items when true.
    """
    return _store().list_grocery_items(list_uid, include_checked)


@mcp.tool()
def list_meal_plans(start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """List locally synced meal plan entries, optionally filtered by date range.

    Each entry includes a human-readable meal_type_name field in addition to
    the numeric type field (0=Breakfast, 1=Lunch, 2=Dinner, 3=Snack). Run
    sync_now() first to populate or refresh the local cache.

    Args:
        start_date: Optional start date (YYYY-MM-DD) inclusive.
        end_date: Optional end date (YYYY-MM-DD) inclusive.
    """
    meals = _store().list_meal_plans(start_date, end_date)
    for meal in meals:
        meal["meal_type_name"] = MEAL_TYPES.get(meal.get("type"), "Unknown")
    return meals


@mcp.tool()
def get_meals_for_date(date: str) -> list[dict]:
    """Get locally synced meal plan entries for a specific date (YYYY-MM-DD).

    Returns meals scheduled on the given date with an added meal_type_name field.
    """
    meals = _store().list_meal_plans(date, date)
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


def main() -> None:
    settings = get_settings()
    mcp.run(transport="http", host=settings.paprika_host, port=settings.paprika_port)


if __name__ == "__main__":
    main()
