"""macOS default-browser login via a temporary, per-attempt URL handler."""

from __future__ import annotations

import asyncio
import json
import math
import os
import plistlib
import secrets
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from .browser_login import BrowserLoginError, decode_callback
from .config import config_path

LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def _register_handler(folder: Path, scheme: str) -> tuple[Path, Path]:
    """Compile an AppleScript app that writes the callback directly to a private file.

    Credentials are never interpolated into shell commands or process arguments.
    The caller owns the mode-0700 temporary directory and removes it after login.
    """
    callback = folder / "callback.txt"
    fd = os.open(callback, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    app = folder / "Moodle MCP Login.app"
    literal = json.dumps(str(callback), ensure_ascii=False)
    source = f'''on open location callbackURL
    set callbackFile to open for access POSIX file {literal} with write permission
    try
        set eof callbackFile to 0
        write callbackURL to callbackFile as «class utf8»
        close access callbackFile
    on error
        try
            close access callbackFile
        end try
    end try
end open location
'''
    subprocess.run(["/usr/bin/osacompile", "-o", str(app)], input=source, text=True,
                   capture_output=True, check=True, timeout=30)
    info_path = app / "Contents" / "Info.plist"
    with info_path.open("rb") as fh:
        info = plistlib.load(fh)
    info.update({
        "CFBundleIdentifier": "org.moodle-mcp.login." + scheme,
        "CFBundleURLTypes": [{"CFBundleURLName": "Moodle MCP Login", "CFBundleURLSchemes": [scheme]}],
        "LSUIElement": True,
    })
    with info_path.open("wb") as fh:
        plistlib.dump(info, fh)
    # osacompile signs its app; changing Info.plist requires a fresh ad-hoc signature.
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)],
                   capture_output=True, check=True, timeout=15)
    subprocess.run([LSREGISTER, "-f", str(app)], capture_output=True, check=True, timeout=15)
    return app, callback


async def system_browser_login(site_url: str, timeout: float = 300) -> tuple[str, str | None]:
    """Use the user's existing browser profile, including its passkeys and SSO."""
    if sys.platform != "darwin":
        raise BrowserLoginError("System-browser login currently requires macOS. Use `setup --browser` instead.")
    if not math.isfinite(timeout) or timeout <= 0:
        raise BrowserLoginError("The browser login timeout must be positive.")
    passport = secrets.token_hex(24)
    scheme = "moodlemcp-" + secrets.token_hex(12)
    # LaunchServices ignores applications in the system temporary directory.
    parent = config_path().parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="login-", dir=parent) as tmp:
        folder = Path(tmp)
        app = folder / "Moodle MCP Login.app"
        try:
            app, callback_file = _register_handler(folder, scheme)
            launch = site_url + "/admin/tool/mobile/launch.php?" + urlencode({
                "service": "moodle_mobile_app", "passport": passport, "urlscheme": scheme,
            })
            if not webbrowser.open(launch):
                raise BrowserLoginError("Could not open the default browser. Try `setup --browser` instead.")

            async def wait_for_callback():
                while True:
                    if callback_file.stat().st_size:
                        # The helper may still be writing; accept only a complete, bound callback.
                        value = callback_file.read_text(encoding="utf-8")
                        try:
                            return decode_callback(value, site_url, passport)
                        except BrowserLoginError:
                            pass
                    await asyncio.sleep(0.2)

            return await asyncio.wait_for(wait_for_callback(), timeout=timeout)
        except asyncio.TimeoutError:
            raise BrowserLoginError(
                "Browser login timed out. Retry setup and allow the browser to open Moodle MCP Login. "
                "If your site forces its own app scheme, use `setup --browser` instead."
            ) from None
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise BrowserLoginError("Could not prepare system-browser login. Try `setup --browser` instead.") from None
        finally:
            if app.exists():
                try:
                    subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True, timeout=15)
                except (OSError, subprocess.SubprocessError):
                    pass
