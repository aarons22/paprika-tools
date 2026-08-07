import base64
import gzip
import json
import uuid
from pathlib import Path
from typing import Optional

import httpx

BASE_URL = "https://www.paprikaapp.com/api"


class PaprikaAPIError(RuntimeError):
    """The API returned HTTP 200 with an error payload instead of a result.

    Paprika signals some failures (e.g. an unknown recipe uid) in the body
    rather than the status code: `{"error": {"code": 0, "message": "..."}}`.
    """


class PaprikaClient:
    """HTTP client for the Paprika Recipe Manager API."""

    def __init__(self, email: str, password: str, token_cache_path: Path | None = None) -> None:
        self.email = email
        self.password = password
        self._token: Optional[str] = None
        self._token_cache_path = token_cache_path
        self._load_cached_token()

    def _load_cached_token(self) -> None:
        if not self._token_cache_path:
            return
        try:
            if not self._token_cache_path.exists():
                return
            data = json.loads(self._token_cache_path.read_text())
            token = data.get("token") if isinstance(data, dict) else None
            if token:
                self._token = token
        except Exception:
            # Ignore cache errors; we'll re-authenticate.
            self._token = None

    def _save_cached_token(self, token: str) -> None:
        if not self._token_cache_path:
            return
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_cache_path.write_text(json.dumps({"token": token}))
            self._token_cache_path.chmod(0o600)
        except Exception:
            # Best-effort cache; auth still works without it.
            return

    def _authenticate(self) -> str:
        """Obtain a bearer token using V1 Basic Auth + form data login."""
        credentials = base64.b64encode(f"{self.email}:{self.password}".encode()).decode()
        response = httpx.post(
            f"{BASE_URL}/v1/account/login/",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"email": self.email, "password": self.password},
            timeout=30,
        )
        response.raise_for_status()
        self._token = response.json()["result"]["token"]
        self._save_cached_token(self._token)
        return self._token

    def _get_token(self) -> str:
        if not self._token:
            self._authenticate()
        return self._token  # type: ignore[return-value]

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an authenticated request, retrying once on 401."""
        token = self._get_token()
        response = httpx.request(
            method,
            f"{BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            **kwargs,
        )
        if response.status_code == 401:
            self._token = None
            token = self._authenticate()
            response = httpx.request(
                method,
                f"{BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
                **kwargs,
            )
        response.raise_for_status()

        content = response.content
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        return json.loads(content)

    def _gzip_upload(self, path: str, payload: dict | list) -> dict:
        """POST gzip-compressed JSON as multipart/form-data field `data`.

        Every Paprika write endpoint uses this shape. Recipes take a single
        object; groceries and meals take an array.
        """
        data = gzip.compress(json.dumps(payload).encode("utf-8"))
        return self._request("POST", path, files={"data": ("data", data)})

    # --- Public API methods ---

    def get_sync_status(self) -> dict:
        """Return change counters for all Paprika resource types."""
        return self._request("GET", "/v2/sync/status/")["result"]

    def list_recipes(self) -> list:
        """Return lightweight list of {uid, hash} pairs for all recipes."""
        return self._request("GET", "/v2/sync/recipes/")["result"]

    def get_recipe(self, uid: str) -> dict:
        """Return full details for a single recipe by UID.

        Raises PaprikaAPIError if the recipe does not exist — the API answers
        with HTTP 200 and an error body rather than a 404.
        """
        payload = self._request("GET", f"/v2/sync/recipe/{uid}/")
        if "result" not in payload:
            error = payload.get("error") or {}
            raise PaprikaAPIError(error.get("message") or f"get_recipe({uid}) failed")
        return payload["result"]

    def list_categories(self) -> list:
        """Return all recipe categories."""
        return self._request("GET", "/v2/sync/categories/")["result"]

    def list_grocery_lists(self) -> list:
        """Return all grocery lists."""
        return self._request("GET", "/v2/sync/grocerylists/")["result"]

    def list_grocery_items(self) -> list:
        """Return all grocery items across all lists."""
        return self._request("GET", "/v2/sync/groceries/")["result"]

    def list_meal_plans(self) -> list:
        """Return all meal plan entries."""
        return self._request("GET", "/v2/sync/meals/")["result"]

    def create_grocery_item(
        self,
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
        """Create a new grocery item on the specified list."""
        item = {
            "uid": str(uuid.uuid4()).upper(),
            "recipe_uid": recipe_uid,
            "name": name,
            "order_flag": order_flag,
            "purchased": purchased,
            "aisle": "",
            "ingredient": ingredient if ingredient is not None else name.lower(),
            "recipe": None,
            "instruction": instruction or "",
            "quantity": quantity or "",
            "separate": separate,
            "list_uid": list_uid,
        }
        return self._gzip_upload("/v2/sync/groceries/", [item])

    def update_grocery_item(self, item: dict) -> dict:
        """Update an existing grocery item.

        The item must be a full grocery object (as returned by
        `list_grocery_items`). Soft-delete by setting `purchased` to true —
        the API has no true delete endpoint for grocery items.
        """
        return self._gzip_upload("/v2/sync/groceries/", [item])

    def upsert_recipe(self, uid: str, recipe: dict) -> dict:
        """Create or update a single recipe.

        `recipe` must be the full recipe object (all fields present) — the
        endpoint replaces the stored recipe rather than merging. Soft-delete by
        setting `in_trash` to true; there is no true delete endpoint.
        """
        return self._gzip_upload(f"/v2/sync/recipe/{uid}/", recipe)

    def create_meal_plans(self, meals: list[dict]) -> dict:
        """Create or update meal plan entries.

        Always POSTs the array to `/v2/sync/meals/` — there is no per-uid
        endpoint (it returns 404). Soft-delete by setting `deleted` to true on
        an entry and posting it again.
        """
        return self._gzip_upload("/v2/sync/meals/", meals)
