import base64
import gzip
import json
import random
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Optional

import httpx

from .sync_hash import generate_sync_hash

BASE_URL = "https://www.paprikaapp.com/api"
DEFAULT_USER_AGENT = "Paprika Recipe Manager 3/3.3.1 (Microsoft Windows NT 10.0.26100.0)"
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
}


class PaprikaRetryExhausted(RuntimeError):
    """Raised when retryable Paprika server-pressure responses keep failing."""

    def __init__(self, status_code: int, attempts: int) -> None:
        super().__init__(f"Paprika request failed with {status_code} after {attempts} attempts")
        self.status_code = status_code
        self.attempts = attempts


class PaprikaClient:
    """HTTP client for the Paprika Recipe Manager API."""

    def __init__(
        self,
        email: str,
        password: str,
        token_cache_path: Path | None = None,
        user_agent: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
        retry_backoff_max: float = 30.0,
        retry_jitter: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.email = email
        self.password = password
        self._default_headers = self._build_default_headers(user_agent, default_headers)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_base = max(0.0, float(retry_backoff_base))
        self.retry_backoff_max = max(0.0, float(retry_backoff_max))
        self.retry_jitter = max(0.0, float(retry_jitter))
        self._sleep = sleep
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

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            response = httpx.request(method, url, **kwargs)
            if response.status_code != 503:
                return response
            if attempt == self.max_retries:
                raise PaprikaRetryExhausted(response.status_code, attempts)
            self._sleep(self._retry_delay(attempt))
        raise PaprikaRetryExhausted(503, attempts)

    def _retry_delay(self, attempt: int) -> float:
        delay = min(self.retry_backoff_max, self.retry_backoff_base * (2**attempt))
        if self.retry_jitter:
            delay += random.uniform(0, self.retry_jitter)
        return delay

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an authenticated request, retrying once on 401."""
        token = self._get_token()
        extra_headers = kwargs.pop("headers", None)
        response = self._send(
            method,
            f"{BASE_URL}{path}",
            headers=self._headers({"Authorization": f"Bearer {token}"}, extra_headers),
            timeout=30,
            **kwargs,
        )
        if response.status_code == 401:
            self._token = None
            token = self._authenticate()
            response = self._send(
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

    def list_recipe_photos(self) -> list:
        """Return synced recipe photo metadata."""
        return self._request("GET", "/v2/sync/photos/")["result"]

    def list_grocery_lists(self) -> list:
        """Return all grocery lists."""
        return self._request("GET", "/v2/sync/grocerylists/")["result"]

    def list_grocery_aisles(self) -> list:
        """Return all grocery aisles."""
        return self._request("GET", "/v2/sync/groceryaisles/")["result"]

    def list_grocery_ingredients(self) -> list:
        """Return all grocery ingredients."""
        return self._request("GET", "/v2/sync/groceryingredients/")["result"]

    def list_grocery_items(self) -> list:
        """Return all grocery items across all lists."""
        return self._request("GET", "/v2/sync/groceries/")["result"]

    def list_meal_types(self) -> list:
        """Return all meal types."""
        return self._request("GET", "/v2/sync/mealtypes/")["result"]

    def list_meal_plans(self) -> list:
        """Return all meal plan entries."""
        return self._request("GET", "/v2/sync/meals/")["result"]

    def list_menus(self) -> list:
        """Return all menus."""
        return self._request("GET", "/v2/sync/menus/")["result"]

    def list_menu_items(self) -> list:
        """Return all menu items."""
        return self._request("GET", "/v2/sync/menuitems/")["result"]

    def list_bookmarks(self) -> list:
        """Return all bookmarks."""
        return self._request("GET", "/v2/sync/bookmarks/")["result"]

    def list_pantry_items(self) -> list:
        """Return all pantry items."""
        return self._request("GET", "/v2/sync/pantry/")["result"]

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
            "sync_hash": generate_sync_hash(),
        }
        payload = gzip.compress(json.dumps([item]).encode("utf-8"))
        return self._request(
            "POST",
            "/v2/sync/groceries/",
            files={"data": ("data", payload)},
        )
