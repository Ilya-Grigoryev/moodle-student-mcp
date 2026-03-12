"""Entry point for the Moodle MCP server."""

import logging
import os

from .tools import mcp

logging.basicConfig(level=logging.INFO)


def main() -> None:
    try:
        mcp.run()
    except KeyboardInterrupt:
        os._exit(0)


if __name__ == "__main__":
    main()
