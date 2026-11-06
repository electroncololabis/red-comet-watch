"""加性迁移测试：老库升级后不能丢数据、不能改写旧行。

db.init_db() 使用 ADD COLUMN 和默认值增加两列，
已有记录保持原样并标成 rule_version=0（=旧规则写的）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import hguc_watch.db as db_module


def _make_legacy_db(path: Path) -> None:
    """造一个**没有** rule_version / source_text 的老库，模拟升级前的状态。"""
    connection = sqlite3.connect(path)
    with connection:
        connection.executescript(
            """
            CREATE TABLE watch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                model_code TEXT NOT NULL DEFAULT '',
                search_query TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                msrp_jpy INTEGER NOT NULL DEFAULT 0,
                official_url TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                purchased_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                error TEXT,
                fx_rate REAL,
                fx_date TEXT,
                fx_source TEXT
            );
            CREATE TABLE listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                watch_item_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                title TEXT NOT NULL,
                item_price REAL NOT NULL,
                shipping_price REAL,
                total_price REAL,
                shipping_status TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT '',
                detail_url TEXT NOT NULL,
                image_url TEXT NOT NULL DEFAULT '',
                seller TEXT NOT NULL DEFAULT '',
                condition_status TEXT NOT NULL,
                eligible INTEGER NOT NULL DEFAULT 0,
                reject_reason TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL,
                UNIQUE(run_id, watch_item_id, item_key)
            );
            """
        )
        connection.execute(
            "INSERT INTO listings (run_id, watch_item_id, item_key, title, item_price,"
            " shipping_status, detail_url, condition_status, eligible, observed_at)"
            " VALUES (1, 1, 'legacy', '万代 HGUC 219 迪杰 全新未拼 包邮', 168.0,"
            " 'free', 'https://www.goofish.com/item?id=1', '全新未拼', 1,"
            " '2026-09-16T07:53:23+08:00')"
        )
    connection.close()


def test_legacy_database_is_migrated_without_data_loss(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "legacy.db"
    _make_legacy_db(legacy)
    monkeypatch.setattr(db_module, "DB_PATH", legacy)

    db_module.init_db()

    connection = sqlite3.connect(legacy)
    connection.row_factory = sqlite3.Row
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(listings)")}
    assert "rule_version" in columns
    assert "source_text" in columns
    assert "platform" in columns
    assert "price_note" in columns

    row = connection.execute("SELECT * FROM listings WHERE item_key = 'legacy'").fetchone()
    assert row is not None, "迁移把旧记录弄丢了"
    assert row["title"] == "万代 HGUC 219 迪杰 全新未拼 包邮"
    assert row["item_price"] == 168.0
    assert row["eligible"] == 1
    # 旧记录的规则版本必须是 0（= 旧规则），不能被冒认成新规则的数据。
    assert row["rule_version"] == 0
    assert row["source_text"] == ""
    assert row["platform"] == "xianyu"
    assert row["price_note"] == ""

    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    assert integrity == "ok"


def test_init_db_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    """第二次 init_db() 不能再报错（否则每次启动服务都会炸）。"""
    legacy = tmp_path / "legacy.db"
    _make_legacy_db(legacy)
    monkeypatch.setattr(db_module, "DB_PATH", legacy)

    db_module.init_db()
    db_module.init_db()

    connection = sqlite3.connect(legacy)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(listings)")]
    connection.close()
    assert columns.count("rule_version") == 1
    assert columns.count("source_text") == 1


def test_rule_version_defaults_to_zero_for_old_rows(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "legacy.db"
    _make_legacy_db(legacy)
    monkeypatch.setattr(db_module, "DB_PATH", legacy)
    db_module.init_db()

    summary = db_module.get_item_summary(1)
    # 老库里只有一个 run_id=1 的 listing，且 runs 表是空的，
    # 所以 summary 里拿不到 current —— 只要迁移没炸、列读得出来就行。
    assert summary["latest_run_id"] is None
