#!/usr/bin/env python
"""End-to-end smoke test for the paprika_mcp write tools.

Exercises upsert_recipe, create_meal_plans and delete_grocery_item against the
real Paprika account configured for this machine, then cleans up after itself
using soft-deletes only (the API has no true delete endpoints).

Run with the repo venv:

    .venv/bin/python scripts/smoke_test_writes.py

Prints PASS/FAIL per step and exits non-zero if any step fails. Every run uses
unique names, so it is safe to re-run.
"""

from __future__ import annotations

import sys
import traceback
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paprika_mcp import server  # noqa: E402
from paprika_mcp.client import PaprikaClient  # noqa: E402

RUN_ID = str(uuid.uuid4())[:8].upper()
RECIPE_NAME = f"MCP Write Tools Smoke Test {RUN_ID}"
MEAL_NAME = f"MCP Smoke Meal {RUN_ID}"
GROCERY_NAME = f"MCP Smoke Item {RUN_ID}"

failures: list[str] = []
recipe_uid: str | None = None
explicit_recipe_uid: str | None = None
meal_uid: str | None = None
grocery_uid: str | None = None


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS  {label}")
    else:
        failures.append(label)
        print(f"FAIL  {label}{f' — {detail}' if detail else ''}")


def find_grocery_item(client: PaprikaClient, uid: str) -> dict | None:
    return next((item for item in client.list_grocery_items() if item.get("uid") == uid), None)


def test_recipe(client: PaprikaClient) -> None:
    global recipe_uid, explicit_recipe_uid

    result = server.upsert_recipe(
        name=RECIPE_NAME,
        ingredients="1 cup smoke\n2 tsp test",
        directions="1. Write.\n2. Read back.",
        servings="4 servings",
        source="paprika-tools smoke test",
        rating=3,
    )
    recipe_uid = result["recipe"]["uid"]
    check("recipe create", result["created"] is True, f"created={result['created']}")
    check(
        "recipe create readback name",
        result["recipe"].get("name") == RECIPE_NAME,
        f"got {result['recipe'].get('name')!r}",
    )
    check(
        "recipe create readback fields",
        result["recipe"].get("servings") == "4 servings"
        and result["recipe"].get("rating") == 3,
        f"servings={result['recipe'].get('servings')!r} rating={result['recipe'].get('rating')!r}",
    )

    created_at = result["recipe"].get("created")
    updated = server.upsert_recipe(name=RECIPE_NAME, servings="8 servings", uid=recipe_uid)
    check("recipe update", updated["created"] is False, f"created={updated['created']}")
    check(
        "recipe update changed servings",
        updated["recipe"].get("servings") == "8 servings",
        f"got {updated['recipe'].get('servings')!r}",
    )
    check(
        "recipe update kept unset fields",
        updated["recipe"].get("directions") == "1. Write.\n2. Read back."
        and updated["recipe"].get("rating") == 3,
        f"directions={updated['recipe'].get('directions')!r} rating={updated['recipe'].get('rating')!r}",
    )
    check(
        "recipe update preserved created timestamp",
        updated["recipe"].get("created") == created_at,
        f"{updated['recipe'].get('created')!r} != {created_at!r}",
    )

    trashed = server.upsert_recipe(name=RECIPE_NAME, uid=recipe_uid, in_trash=True)
    check(
        "recipe soft-delete (in_trash)",
        trashed["recipe"].get("in_trash") is True,
        f"in_trash={trashed['recipe'].get('in_trash')!r}",
    )
    recipe_uid = None  # cleaned up

    # True upsert: an unknown uid creates a recipe with that uid.
    supplied_uid = str(uuid.uuid4()).upper()
    explicit_recipe_uid = supplied_uid
    upserted = server.upsert_recipe(name=f"{RECIPE_NAME} (upsert)", uid=supplied_uid)
    check(
        "recipe upsert with unknown uid creates",
        upserted["created"] is True and upserted["recipe"].get("uid") == supplied_uid,
        f"created={upserted['created']} uid={upserted['recipe'].get('uid')}",
    )
    server.upsert_recipe(name=f"{RECIPE_NAME} (upsert)", uid=supplied_uid, in_trash=True)
    explicit_recipe_uid = None  # cleaned up


def test_meal_plan(client: PaprikaClient) -> None:
    global meal_uid

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    result = server.create_meal_plans(
        [{"date": tomorrow, "type": "Dinner", "name": MEAL_NAME}]
    )
    check("meal plan create", result["created"] == 1, f"created={result['created']}")
    meal = result["meals"][0]
    meal_uid = meal["uid"]
    check(
        "meal plan readback",
        meal.get("name") == MEAL_NAME
        and meal.get("type") == 2
        and meal.get("date", "").startswith(tomorrow),
        f"name={meal.get('name')!r} type={meal.get('type')!r} date={meal.get('date')!r}",
    )

    try:
        server.create_meal_plans([{"date": tomorrow, "type": "Brunch", "name": MEAL_NAME}])
        check("meal plan rejects bad type", False, "no ValueError raised")
    except ValueError:
        check("meal plan rejects bad type", True)

    deleted = server.create_meal_plans(
        [
            {
                "uid": meal_uid,
                "date": tomorrow,
                "type": "Dinner",
                "name": MEAL_NAME,
                "deleted": True,
            }
        ]
    )
    check("meal plan soft-delete posted", deleted["deleted"] == 1, f"deleted={deleted['deleted']}")
    remaining = [m for m in client.list_meal_plans() if m.get("uid") == meal_uid]
    check("meal plan gone after soft-delete", not remaining, f"still present: {remaining}")
    meal_uid = None  # cleaned up


def test_grocery(client: PaprikaClient) -> None:
    global grocery_uid

    lists = client.list_grocery_lists()
    default_list = next((lst for lst in lists if lst.get("is_default")), None) or lists[0]

    before = {item.get("uid") for item in client.list_grocery_items()}
    server.add_grocery_item(list_uid=default_list["uid"], name=GROCERY_NAME)
    new_items = [
        item
        for item in client.list_grocery_items()
        if item.get("uid") not in before and item.get("name") == GROCERY_NAME
    ]
    check("grocery item created", len(new_items) == 1, f"found {len(new_items)} matching items")
    if not new_items:
        return
    grocery_uid = new_items[0]["uid"]

    result = server.delete_grocery_item(grocery_uid)
    check("grocery soft-delete", result["deleted"] is True and result["item"]["purchased"] is True,
          f"item={result['item']}")
    stored = find_grocery_item(client, grocery_uid)
    check(
        "grocery item purchased after re-fetch",
        bool(stored and stored.get("purchased")),
        f"stored={stored}",
    )
    grocery_uid = None  # cleaned up


def cleanup(client: PaprikaClient) -> None:
    """Soft-delete anything a failed step left behind."""
    for uid in (recipe_uid, explicit_recipe_uid):
        if not uid:
            continue
        try:
            server.upsert_recipe(name=RECIPE_NAME, uid=uid, in_trash=True)
            print(f"CLEAN recipe {uid} moved to trash")
        except Exception as exc:  # pragma: no cover - best effort
            print(f"CLEAN failed for recipe {uid}: {exc}")
    if meal_uid:
        try:
            client.create_meal_plans(
                [
                    {
                        "uid": meal_uid,
                        "recipe_uid": None,
                        "date": (date.today() + timedelta(days=1)).isoformat() + " 00:00:00",
                        "type": 2,
                        "name": MEAL_NAME,
                        "order_flag": 0,
                        "type_uid": "",
                        "scale": None,
                        "is_ingredient": False,
                        "deleted": True,
                    }
                ]
            )
            print(f"CLEAN meal plan {meal_uid} deleted")
        except Exception as exc:  # pragma: no cover - best effort
            print(f"CLEAN failed for meal plan {meal_uid}: {exc}")
    if grocery_uid:
        try:
            server.delete_grocery_item(grocery_uid)
            print(f"CLEAN grocery item {grocery_uid} marked purchased")
        except Exception as exc:  # pragma: no cover - best effort
            print(f"CLEAN failed for grocery item {grocery_uid}: {exc}")


def main() -> int:
    client = server._client()
    print(f"Paprika write-tools smoke test (run {RUN_ID})\n")

    for label, test in (
        ("recipe", test_recipe),
        ("meal plan", test_meal_plan),
        ("grocery", test_grocery),
    ):
        try:
            test(client)
        except Exception:
            failures.append(f"{label} (exception)")
            print(f"FAIL  {label} raised:")
            traceback.print_exc()
        print()

    cleanup(client)

    if failures:
        print(f"\nFAIL — {len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nPASS — all write tools verified end-to-end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
