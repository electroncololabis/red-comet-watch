"""pytest 全局配置（Windows 沙箱下的 tmp_path 兼容层）。

## 为什么要这个文件

本项目目前是在 DSH 沙箱（Windows，workspace-write 模式）里跑测试的。
这个沙箱下有两个连在一起的环境问题会让 `tmp_path` 直接报错——
注意是 **fixture 阶段 ERROR，不是断言失败**，看起来像"测试挂了"，其实是环境：

1. `os.mkdir(path, 0o700)` 建出来的目录**创建者本人也进不去**。

   实测（2026-10-01）：
       os.mkdir(d)         -> 可写，OK
       os.mkdir(d, 0o777)  -> 可写，OK
       os.mkdir(d, 0o700)  -> 目录建出来了，往里写文件却报
                              PermissionError: [Errno 13] Permission denied，
                              连 `icacls` 读它的 DACL 都被拒绝

   pytest 的 `tmp_path` / basetemp 机制写死了 `mode=0o700`
   （`pathlib.Path.mkdir(mode=0o700)`），于是 3 个用 `tmp_path` 的数据库测试
   必然在 fixture 阶段报 `PermissionError: [WinError 5] 拒绝访问: '...\\pytest-of-llz'`。

2. 一旦这样建坏过一次，`%TEMP%` 下就留下一个**谁都删不掉**的
   `pytest-of-<user>` 目录。之后每次运行都会撞上它（`mkdir(exist_ok=True)`
   连它是否已存在都 stat 不了）。

## 补丁做了什么

都只在 Windows 上生效，都只影响 pytest 进程自己：

- 丢掉传给 `os.mkdir` 的 mode（等价于系统默认权限，即 0o777 的效果）；
- 把 `tempfile` 的临时根目录指到项目内的 `_diag/pytest-temp`，
  绕开 `%TEMP%` 里那个已经坏掉的残留目录。

POSIX 上原样不动。想关掉：设环境变量 `HGUC_KEEP_MKDIR_MODE=1`。

## 这不是被测代码的问题

`FILTER_RULE_VERSION` 之类的业务逻辑与本文件无关。测试本身没有为了迁就
本补丁而改动任何断言。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32" and os.environ.get("HGUC_KEEP_MKDIR_MODE") != "1":
    # --- 1) os.mkdir 不再接受 mode -------------------------------------
    _real_mkdir = os.mkdir

    def _mkdir_without_mode(path, mode=0o777, *args, **kwargs):  # noqa: ANN001
        # 丢掉 mode；其余参数（dir_fd 等）原样透传。
        return _real_mkdir(path, *args, **kwargs)

    os.mkdir = _mkdir_without_mode

    # --- 2) 临时根目录挪进工作区 ---------------------------------------
    # `%TEMP%` 由沙箱重定向，且可能已经残留一个不可访问的 pytest-of-<user>。
    try:
        _temp_root = Path(__file__).resolve().parents[1] / "_diag" / "pytest-temp"
        _temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(_temp_root)
        os.environ["TEMP"] = str(_temp_root)
        os.environ["TMP"] = str(_temp_root)
    except OSError:
        # 工作区不可写时保持原样，让 pytest 用系统默认值。
        pass
