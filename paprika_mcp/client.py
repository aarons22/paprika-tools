import base64
import gzip
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

import httpx

BASE_URL = "https://www.paprikaapp.com/api"
DEFAULT_USER_AGENT = "Paprika Recipe Manager 3/3.3.1 (Microsoft Windows NT 10.0.26100.0)"
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
}


class PaprikaClient:
    """HTTP client for the Paprika Recipe Manager API."""

    def __init__(
        self,
        email: str,
        password: str,
        token_cache_path: Path | None = None,
        user_agent: str | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self._default_headers = self._build_default_headers(user_agent, default_headers)
        self._token: Optional[str] = None
        self._token_cache_path = token_cache_path
        self._load_cached_token()

    @staticmethod
    def _build_default_headers(
        user_agent: str | None,
        overrides: Mapping[str, str] | None,
    ) -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        if user_agent is not None:
            headers["User-Agent"] = user_agent
        if overrides:
            headers.update(overrides)
        return headers

    def _headers(self, *header_groups: Mapping[str, str] | None) -> dict[str, str]:
        headers = httpx.Headers(self._default_headers)
        for group in header_groups:
            if not group:
                continue
            for key, value in group.items():
                headers[key] = value
        return dict(headers)

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
            headers=self._headers(
                {
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                }
            ),
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
        extra_headers = kwargs.pop("headers", None)
        response = httpx.request(
            method,
            f"{BASE_URL}{path}",
            headers=self._headers({"Authorization": f"Bearer {token}"}, extra_headers),
            timeout=30,
            **kwargs,
        )
        if response.status_code == 401:
            self._token = None
            token = self._authenticate()
            response = httpx.request(
                method,
                f"{BASE_URL}{path}",
                headers=self._headers({"Authorization": f"Bearer {token}"}, extra_headers),
                timeout=30,
                **kwargs,
            )
        response.raise_for_status()

        content = response.content
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        return json.loads(content)

    # --- Public API methods ---

    def get_sync_status(self) -> dict:
        """Return change counters for all Paprika resource types."""
        return self._request("GET", "/v2/sync/status/")["result"]

    def list_recipes(self) -> list:
        """Return lightweight list of {uid, hash} pairs for all recipes."""
        return self._request("GET", "/v2/sync/recipes/")["result"]

    def get_recipe(self, uid: str) -> dict:
        """Return full details for a single recipe by UID."""
        return self._request("GET", f"/v2/sync/recipe/{uid}/")["result"]

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
        payload = gzip.compress(json.dumps([item]).encode("utf-8"))
        return self._request(
            "POST",
            "/v2/sync/groceries/",
            files={"data": ("data", payload)},
        )
