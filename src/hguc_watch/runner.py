from __future__ import annotations

import asyncio
import json
import sys

from .db import init_db
from .service import run_tracking

# 退出码约定。Windows 计划任务的 "Last Result" 会显示这个值。
# 只有成功完成搜索时才返回成功退出码。
EXIT_SUCCESS = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2


def main() -> int:
    init_db()
    result = asyncio.run(run_tracking())
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))

    if result.status == "failed":
        print(
            f"business failure: run {result.run_id} failed with "
            f"{len(result.errors)} error(s), causes={result.causes}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if result.status == "partial":
        print(
            f"partial run {result.run_id}: listing_count={result.listing_count}, "
            f"causes={result.causes}",
            file=sys.stderr,
        )
        return EXIT_PARTIAL
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
