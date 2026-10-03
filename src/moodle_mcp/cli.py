"""Command line: run the server, set up the token, diagnose, call tools from the shell."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import re
import secrets
import sys
from typing import Any

from . import __version__


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _text_of(result: Any) -> str:
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(f"[{getattr(block, 'type', 'content')} block]")
    return "\n".join(parts)


async def _call_tool(name: str, args: dict[str, Any]) -> int:
    from mcp.server.mcpserver.exceptions import ToolError

    from .app import app
    from .server import mcp

    try:
        result = await mcp.call_tool(name, args)
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if app._client is not None:
            await app.client.aclose()
    print(_text_of(result))
    return 1 if getattr(result, "is_error", False) else 0


def cmd_call(ns: argparse.Namespace) -> int:
    args: dict[str, Any] = {}
    if ns.json:
        args.update(json.loads(ns.json))
    for item in ns.args:
        if "=" not in item:
            print(f"Arguments must look like key=value, got {item!r}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        args[key] = _parse_value(value)
    return asyncio.run(_call_tool(ns.tool, args))


def cmd_tools(_: argparse.Namespace) -> int:
    from .server import mcp

    async def run() -> None:
        for tool in await mcp.list_tools():
            desc = " ".join((tool.description or "").split())
            params = ", ".join((tool.input_schema or {}).get("properties", {}).keys())
            print(f"{tool.name}({params})\n    {desc}\n")

    asyncio.run(run())
    return 0


def cmd_serve(_: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    from .server import mcp

    try:
        mcp.run("stdio")
    except KeyboardInterrupt:
        pass
    return 0


def decode_launch_token(text: str) -> tuple[str, str | None]:
    """Extract (token, private_token) from a `moodlemobile://token=…` URL, its base64 part, or a raw token."""
    s = text.strip().strip("'\"")
    m = re.search(r"token=([A-Za-z0-9+/=_-]+)", s)
    candidate = m.group(1) if m else s
    if re.fullmatch(r"[0-9a-f]{32}", candidate):
        return candidate, None
    padded = candidate + "=" * (-len(candidate) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            decoded = decoder(padded).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            continue
        parts = decoded.split(":::")
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-zA-Z]{32}", parts[1]):
            private = parts[2] if len(parts) > 2 and parts[2] else None
            return parts[1], private
    raise ValueError("Could not find a token in the pasted text.")


async def _check(verbose: bool = True) -> int:
    from .app import app
    from .config import ConfigError
    from .tools.courses import status

    try:
        print(await status())
    except ConfigError as exc:
        print(f"Not configured: {exc}")
        return 1
    except Exception as exc:
        print(f"Connection failed: {exc}")
        return 1
    if app.settings.private_token and verbose:
        try:
            await app.web.ensure()
            print("- Web session: OK")
        except Exception as exc:
            print(f"- Web session: FAILED ({exc})")
    if app._client is not None:
        await app.client.aclose()
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    return asyncio.run(_check())


def cmd_setup(ns: argparse.Namespace) -> int:
    from .config import normalize_site_url, write_config_file

    print("Moodle MCP setup\n================\n")
    default_url = ns.url or "https://moodle.univie.ac.at"
    url = input(f"Moodle site URL [{default_url}]: ").strip() or default_url
    url = normalize_site_url(url)
    passport = secrets.randbelow(10**9)
    launch = f"{url}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport={passport}&urlscheme=moodlemobile"
    print(
        "\n1. (Optional, enables web-only pages) Log out of Moodle and log in again right before step 2 —\n"
        "   only a fresh login makes Moodle include the 'private token'.\n"
        "2. Open your browser's DevTools → Network tab (enable 'Preserve log'), then open:\n\n"
        f"   {launch}\n\n"
        "3. The page redirects to a URL starting with  moodlemobile://token=…  (shown as a failed request\n"
        "   in the Network tab, or in the 'Open app?' prompt). Copy that whole URL.\n"
        "   You can also paste a plain 32-character token if you already have one.\n"
    )
    pasted = input("Paste here: ").strip()
    try:
        token, private = decode_launch_token(pasted)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1
    print(f"\n✓ Token found{' (with private token)' if private else ''}. Checking it…")

    from .app import app
    from .config import Settings

    app.configure(Settings(site_url=url, token=token, private_token=private))

    async def verify() -> dict[str, Any]:
        try:
            return await app.client.site_info()
        finally:
            await app.client.aclose()

    try:
        info = asyncio.run(verify())
    except Exception as exc:
        print(f"✗ Moodle rejected the token: {exc}")
        return 1
    print(f"✓ Logged in as {info.get('fullname')} ({info.get('username')})")
    values: dict[str, Any] = {"url": url, "token": token}
    if private:
        values["private_token"] = private
    path = write_config_file(values)
    print(f"✓ Saved to {path} (permissions 600)\n")
    exe = sys.argv[0] if sys.argv and sys.argv[0].endswith("moodle-mcp") else "moodle-mcp"
    print("Add the server to your MCP client, e.g.:\n")
    print(f"  Claude Code:   claude mcp add moodle -s user -- {exe}")
    print(f'  Cursor/Claude Desktop JSON:  "moodle": {{"command": "{exe}"}}\n')
    print("Then run `moodle-mcp check` to verify.")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="moodle-mcp", description="MCP server for Moodle (students).")
    parser.add_argument("--version", action="version", version=f"moodle-student-mcp {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Run the MCP server on stdio (default)")
    p_setup = sub.add_parser("setup", help="Interactive token setup")
    p_setup.add_argument("--url", help="Moodle site URL")
    sub.add_parser("check", help="Check configuration and connection")
    sub.add_parser("tools", help="List available tools")
    p_call = sub.add_parser("call", help="Call a tool: moodle-mcp call get_deadlines days=7")
    p_call.add_argument("tool")
    p_call.add_argument("args", nargs="*", help="key=value (values parsed as JSON when possible)")
    p_call.add_argument("--json", help="Arguments as a JSON object")
    ns = parser.parse_args(argv)
    handlers = {
        None: cmd_serve, "serve": cmd_serve, "setup": cmd_setup, "check": cmd_check,
        "tools": cmd_tools, "call": cmd_call,
    }
    sys.exit(handlers[ns.cmd](ns))
