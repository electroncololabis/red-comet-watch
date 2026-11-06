from __future__ import annotations

import asyncio

from filelock import FileLock

from .config import LOGIN_LOCK_PATHS
from .db import init_db
from .xianyu import open_login_window


def main() -> None:
    with FileLock(str(LOGIN_LOCK_PATHS[0]), timeout=0):
        init_db()
        asyncio.run(open_login_window())


if __name__ == "__main__":
    main()
