# Paprika

Tools for interacting with the [Paprika Recipe Manager](https://www.paprikaapp.com).

| | What it is |
|---|---|
| [`openapi.yaml`](./openapi.yaml) | OpenAPI 3.0.3 spec — machine-readable definition of the unofficial Paprika API |
| [`paprika/`](./paprika/) | Go CLI — resource-grouped commands with table + JSON output and shell completions |
| [`paprika_mcp/`](./paprika_mcp/) | Python FastMCP server — exposes Paprika data as MCP tools for AI agents |

---

## CLI

### Install

**Build from source** (requires Go 1.21+):

```bash
git clone https://github.com/aarons22/paprika-tools
cd paprika-tools/paprika
go build -o paprika .
mv paprika /usr/local/bin/
```

**go install** (once the repo is tagged):

```bash
go install github.com/aarons22/paprika-tools/paprika@latest
```

### Authenticate

```bash
paprika account login --email you@example.com --password yourpassword
```

The token is saved automatically to `~/.config/paprika/config.yaml` (mode 0600). All subsequent commands read it from there — no manual copy-paste required.

The token is also accepted via the `PAPRIKA_TOKEN` environment variable.

### Commands

```
paprika account login                    # authenticate and get a Bearer token

paprika recipes listRecipes              # all recipes as {uid, hash} pairs
paprika recipes getRecipe <uid>          # full recipe details
paprika recipes upsertRecipe <uid>       # create or update a recipe (gzip multipart)

paprika categories listCategories        # all recipe categories

paprika grocerylists listGroceryLists    # all grocery lists
paprika groceries listGroceryItems       # all grocery items across all lists
paprika groceries createGroceryItems     # add or update grocery items (gzip multipart)

paprika meals listMealPlans              # full meal calendar

paprika pantry listPantryItems           # pantry inventory

paprika status getSyncStatus             # change counters for all resource types
```

### Output & global flags

```bash
# Raw JSON (pipe-friendly)
paprika recipes listRecipes --json | jq '.[].uid'

# Override API base URL
paprika recipes listRecipes --base-url https://www.paprikaapp.com/api/v2/sync

# Disable colour
paprika recipes listRecipes --no-color

# Shell completions (bash, zsh, or fish)
paprika completion bash >> ~/.bashrc
```

---

## MCP Server

FastMCP server that exposes Paprika data as tools for AI agents (Claude, Cursor, etc.).

### Quick Install (macOS)

```bash
curl -sSL https://raw.githubusercontent.com/aarons22/paprika-tools/main/install.sh | bash
```

Then run:

```bash
paprika-mcp setup
paprika-mcp install
```

If `paprika-mcp` isn't on your PATH, use:

```bash
$HOME/.local/bin/paprika-mcp --help
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `paprika-mcp setup` | Interactive credential and port setup |
| `paprika-mcp run` | Run the MCP server in the foreground |
| `paprika-mcp install` | Install as a macOS LaunchAgent (background service) |
| `paprika-mcp uninstall` | Remove the LaunchAgent |
| `paprika-mcp update` | Pull latest changes, reinstall, and restart the LaunchAgent |
| `paprika-mcp status` | Check LaunchAgent status |
| `paprika-mcp logs` | View server logs |

### Available Tools

| Tool | Description |
|------|-------------|
| `get_sync_status` | Get Paprika cloud change counters for all resource types |
| `get_local_sync_status` | Get local SQLite cache status and recipe counts |
| `sync_recipes` / `sync_now` | Sync changed Paprika recipes into SQLite |
| `list_recipes` | List locally cached recipes as lightweight `{uid, hash, name}` rows |
| `get_recipe(uid)` | Get full locally cached recipe details by UID |
| `search_recipes(query, limit?)` | Search locally cached recipes |
| `list_categories` | List all recipe categories |
| `list_grocery_lists` | List all grocery lists |
| `list_grocery_items(list_uid, include_checked?)` | List grocery items for a specific list |
| `list_meal_plans(start_date?, end_date?)` | List meal plan entries, optionally filtered by date |
| `get_meals_for_date(date)` | Get meal plan entries for a specific date |
| `add_grocery_item(list_uid, name, ...)` | Add a grocery item to a specific list |

### Config & Logs

- Config: `~/Library/Application Support/paprika-mcp/config.toml`
- Token cache: `~/Library/Application Support/paprika-mcp/.paprika_token.json`
- SQLite cache: `~/Library/Application Support/paprika-mcp/paprika.sqlite`
- Stdout log: `~/Library/Logs/paprika-mcp.out.log`
- Stderr log: `~/Library/Logs/paprika-mcp.err.log`

Environment variables:

- `PAPRIKA_EMAIL`: Paprika account email
- `PAPRIKA_PASSWORD`: Paprika account password
- `PAPRIKA_HOST`: HTTP bind host for the MCP server, defaults to `127.0.0.1`
- `PAPRIKA_PORT`: HTTP bind port for the MCP server, defaults to `8000`
- `PAPRIKA_DB_PATH`: SQLite cache path, defaults to `paprika.sqlite` in the config directory

Recipe tools read from SQLite to reduce Paprika sync API calls. Run
`sync_recipes` or `sync_now` to populate or refresh the local cache. The sync
uses Paprika's lightweight `{uid, hash}` recipe list and fetches full recipe
data only for recipes that are new or changed.

### Notes

- Mostly read-only; `sync_recipes`, `sync_now`, and `add_grocery_item` are the only write tools
- The Paprika API is unofficial and undocumented; see [`API_REFERENCE.md`](./API_REFERENCE.md) for details
- Tokens are cached on disk and refreshed automatically on 401

---

## OpenAPI Spec

`openapi.yaml` is the authoritative machine-readable definition of the Paprika API. It covers authentication, recipes, categories, grocery lists and items, meal plans, pantry, and sync status.

Use it with any OpenAPI-compatible tooling — code generators, HTTP clients, documentation renderers, or to regenerate the CLI:

```bash
go install github.com/theaiteam-dev/commandspec@latest
commandspec validate --schema ./openapi.yaml
commandspec init --schema ./openapi.yaml --name paprika --output-dir ./paprika
```
