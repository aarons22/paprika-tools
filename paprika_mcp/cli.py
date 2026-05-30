from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .client import PaprikaClient
from .config import CONFIG_FILE, get_settings, save_config, token_cache_path
from .server import main as server_main

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.paprika.mcp.plist"
LOG_DIR = Path.home() / "Library" / "Logs"
OUT_LOG = LOG_DIR / "paprika-mcp.out.log"
ERR_LOG = LOG_DIR / "paprika-mcp.err.log"


def _print(msg: str) -> None:
    print(msg)


def cmd_setup(args: argparse.Namespace) -> int:
    email = args.email or input("Paprika email: ").strip()
    password = args.password or getpass.getpass("Paprika password: ").strip()
    port = args.port

    if port is None:
        port_str = input("Local MCP port [8000]: ").strip()
        port = int(port_str) if port_str else 8000
    try:
        port = int(port)
    except Exception:
        _print("❌ Invalid port.")
        return 1

    if not email or not password:
        _print("❌ Email and password are required.")
        return 1

    save_config(email=email, password=password, port=port)

    try:
        settings = get_settings()
        client = PaprikaClient(
            email=email,
            password=password,
            token_cache_path=token_cache_path(),
            user_agent=settings.paprika_user_agent,
            max_retries=settings.paprika_max_retries,
            retry_backoff_base=settings.paprika_retry_backoff_base,
            retry_backoff_max=settings.paprika_retry_backoff_max,
            retry_jitter=settings.paprika_retry_jitter,
        )
        client.get_sync_status()
        _print("✅ Credentials verified and config saved.")
        _print(f"Config: {CONFIG_FILE}")
        return 0
    except Exception as exc:
        _print(f"⚠️ Saved config, but auth failed: {exc}")
        _print("Check credentials and try again.")
        return 1


def _resolve_entrypoint() -> str:
    argv0 = Path(sys.argv[0])
    if argv0.exists():
        return str(argv0.resolve())
    candidate = Path(sys.executable).resolve().parent / "paprika-mcp"
    if candidate.exists():
        return str(candidate)
    path = shutil.which("paprika-mcp")
    if path:
        return path
    raise RuntimeError("Could not find 'paprika-mcp' in PATH or alongside current Python interpreter.")


def _find_repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _resolve_repo_root() -> Path:
    env_root = os.environ.get("PAPRIKA_MCP_REPO")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        if (root / ".git").exists():
            return root
        raise RuntimeError(f"PAPRIKA_MCP_REPO is not a git repo: {root}")

    entrypoint = Path(sys.argv[0])
    if entrypoint.exists():
        repo_root = _find_repo_root(entrypoint.resolve())
        if repo_root:
            return repo_root

    repo_root = _find_repo_root(Path.cwd().resolve())
    if repo_root:
        return repo_root

    raise RuntimeError(
        "Could not locate the paprika-mcp git repository. "
        "Set PAPRIKA_MCP_REPO to the repo path or run the installer."
    )


def _write_plist(entrypoint: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
  <dict>
    <key>Label</key>
    <string>com.paprika.mcp</string>

    <key>ProgramArguments</key>
    <array>
      <string>{entrypoint}</string>
      <string>run</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{Path.home()}</string>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>{OUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>{ERR_LOG}</string>
  </dict>
</plist>
"""
    PLIST_PATH.write_text(content)
    PLIST_PATH.chmod(0o644)


def _launchctl(*args: str) -> int:
    result = subprocess.run(["launchctl", *args], check=False)
    return result.returncode


def cmd_install(_: argparse.Namespace) -> int:
    try:
        get_settings()
    except Exception as exc:
        _print(f"❌ {exc}")
        _print("Run 'paprika-mcp setup' first.")
        return 1

    try:
        entrypoint = _resolve_entrypoint()
    except Exception as exc:
        _print(f"❌ {exc}")
        return 1

    _write_plist(entrypoint)

    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(PLIST_PATH))
    _launchctl("bootstrap", domain, str(PLIST_PATH))
    _launchctl("enable", f"{domain}/com.paprika.mcp")
    _launchctl("kickstart", "-k", f"{domain}/com.paprika.mcp")

    _print("✅ LaunchAgent installed and started.")
    _print(f"Plist: {PLIST_PATH}")
    return 0


def cmd_uninstall(_: argparse.Namespace) -> int:
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(PLIST_PATH))
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    _print("✅ LaunchAgent removed.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    domain = f"gui/{os.getuid()}"
    code = _launchctl("print", f"{domain}/com.paprika.mcp")
    if code != 0:
        _print("❌ LaunchAgent not running.")
        return 1
    _print("✅ LaunchAgent appears to be running.")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    if args.follow:
        return subprocess.run(["tail", "-f", str(OUT_LOG), str(ERR_LOG)], check=False).returncode
    subprocess.run(["tail", "-n", "200", str(OUT_LOG)], check=False)
    subprocess.run(["tail", "-n", "200", str(ERR_LOG)], check=False)
    return 0


def cmd_update(_: argparse.Namespace) -> int:
    if not shutil.which("git"):
        _print("❌ git is required to update but was not found.")
        return 1

    try:
        repo_root = _resolve_repo_root()
    except Exception as exc:
        _print(f"❌ {exc}")
        return 1

    pull = subprocess.run(["git", "-C", str(repo_root), "pull", "--ff-only"], check=False)
    if pull.returncode != 0:
        _print("❌ git pull failed. Resolve any local changes and try again.")
        return pull.returncode

    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(repo_root)],
        check=False,
    )
    if pip.returncode != 0:
        _print("❌ pip install failed. Try re-running the installer.")
        return pip.returncode

    if PLIST_PATH.exists():
        try:
            entrypoint = _resolve_entrypoint()
            _write_plist(entrypoint)
        except Exception as exc:
            _print(f"⚠️ Updated, but could not refresh LaunchAgent: {exc}")
            return 0

        domain = f"gui/{os.getuid()}"
        _launchctl("bootout", domain, str(PLIST_PATH))
        _launchctl("bootstrap", domain, str(PLIST_PATH))
        _launchctl("enable", f"{domain}/com.paprika.mcp")
        _launchctl("kickstart", "-k", f"{domain}/com.paprika.mcp")
        _print("✅ Updated and restarted LaunchAgent.")
        return 0

    _print("✅ Updated. LaunchAgent not installed; run 'paprika-mcp install' to enable it.")
    return 0


def cmd_run(_: argparse.Namespace) -> int:
    server_main()
    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paprika-mcp",
        description="Paprika MCP server CLI",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    setup_parser = subparsers.add_parser("setup", help="Interactive setup")
    setup_parser.add_argument("--email", help="Paprika email")
    setup_parser.add_argument("--password", help="Paprika password")
    setup_parser.add_argument("--port", type=int, help="Local MCP port")

    subparsers.add_parser("run", help="Run MCP server in foreground")
    subparsers.add_parser("install", help="Install LaunchAgent")
    subparsers.add_parser("uninstall", help="Uninstall LaunchAgent")

    subparsers.add_parser("update", help="Update paprika-mcp and restart LaunchAgent")

    subparsers.add_parser("status", help="Show LaunchAgent status")

    logs_parser = subparsers.add_parser("logs", help="Show logs")
    logs_parser.add_argument("--follow", action="store_true", help="Follow logs")

    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "setup": cmd_setup,
        "run": cmd_run,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "status": cmd_status,
        "logs": cmd_logs,
        "update": cmd_update,
    }

    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
