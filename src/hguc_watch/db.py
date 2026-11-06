from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from .catalog import DEFAULT_SETTINGS, INITIAL_WATCH_ITEMS
from .config import DB_PATH, ensure_data_dirs
from .filtering import FILTER_RULE_VERSION, classify_listing, watch_requirements, watch_edition, explicitly_paid_shipping
from .reference_prices import fill_reference_price

UNBLOCKED_LISTING_SQL = """NOT EXISTS (
    SELECT 1 FROM blocked_listings b
    WHERE b.platform = listings.platform AND b.item_key = listings.item_key
)"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_data_dirs()
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS watch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                model_code TEXT NOT NULL DEFAULT '',
                search_query TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                msrp_jpy INTEGER NOT NULL DEFAULT 0,
                official_url TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                purchased_at TEXT,
                purchase_price_cny REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                error TEXT,
                fx_rate REAL,
                fx_date TEXT,
                fx_source TEXT
            );

            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                watch_item_id INTEGER NOT NULL REFERENCES watch_items(id) ON DELETE CASCADE,
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
                -- 判定这条用的 filtering 规则版本。0 = 规则改版前的旧记录。
                -- 见 filtering.FILTER_RULE_VERSION。旧史不删，只标出来。
                rule_version INTEGER NOT NULL DEFAULT 0,
                -- 真正参与判定的那份文本（标题+卡片+作用域内的详情），截断保存。
                -- 用途：规则以后再改时，可以拿它重判历史记录，不必重新抓。
                source_text TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT 'xianyu',
                price_note TEXT NOT NULL DEFAULT '',
                UNIQUE(run_id, watch_item_id, item_key)
            );

            CREATE INDEX IF NOT EXISTS idx_listings_watch_run
                ON listings(watch_item_id, run_id);
            CREATE INDEX IF NOT EXISTS idx_listings_history_min
                ON listings(watch_item_id, eligible, total_price);

            CREATE TABLE IF NOT EXISTS blocked_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                item_key TEXT NOT NULL,
                title TEXT NOT NULL,
                detail_url TEXT NOT NULL,
                blocked_at TEXT NOT NULL,
                UNIQUE(platform, item_key)
            );
            """
        )

        # Keep existing local databases forward-compatible without destructive
        # migrations. SQLite cannot add this column conditionally in SQL, so we
        # inspect the schema before applying the small additive migration.
        item_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(watch_items)").fetchall()
        }
        if "purchase_price_cny" not in item_columns:
            db.execute("ALTER TABLE watch_items ADD COLUMN purchase_price_cny REAL")

        # 同样的加性迁移：老库里的 listings 没有 rule_version 列。
        # 已有记录一律留 0（=旧规则写的），不删不改，供审计。
        listing_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(listings)").fetchall()
        }
        if "rule_version" not in listing_columns:
            db.execute(
                "ALTER TABLE listings ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 0"
            )
        if "source_text" not in listing_columns:
            db.execute("ALTER TABLE listings ADD COLUMN source_text TEXT NOT NULL DEFAULT ''")
        if "platform" not in listing_columns:
            db.execute("ALTER TABLE listings ADD COLUMN platform TEXT NOT NULL DEFAULT 'xianyu'")
        if "price_note" not in listing_columns:
            db.execute("ALTER TABLE listings ADD COLUMN price_note TEXT NOT NULL DEFAULT ''")
        db.execute("""CREATE TABLE IF NOT EXISTS marketplace_runs (
            run_id INTEGER NOT NULL REFERENCES runs(id), platform TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(run_id, platform))""")

        timestamp = now_iso()
        for item in INITIAL_WATCH_ITEMS:
            db.execute(
                """
                INSERT OR IGNORE INTO watch_items
                    (name, model_code, search_query, aliases_json, msrp_jpy,
                     official_url, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    item["name"],
                    item["model_code"],
                    item["search_query"],
                    json.dumps(item["aliases"], ensure_ascii=False),
                    item["msrp_jpy"],
                    item["official_url"],
                    timestamp,
                    timestamp,
                ),
            )
        for key, value in DEFAULT_SETTINGS.items():
            db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))

    backfill_reference_prices()


def row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["aliases"] = json.loads(item.pop("aliases_json") or "[]")
    item["active"] = bool(item["active"])
    return item


def get_watch_item(item_id: int) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM watch_items WHERE id = ?", (item_id,)).fetchone()
    return row_to_item(row) if row else None


def list_watch_items(include_purchased: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM watch_items"
    params: tuple[Any, ...] = ()
    if not include_purchased:
        sql += " WHERE active = 1"
    sql += " ORDER BY active DESC, id ASC"
    with connect() as db:
        rows = db.execute(sql, params).fetchall()
    return [row_to_item(row) for row in rows]


def create_watch_item(data: dict[str, Any]) -> dict[str, Any]:
    data = fill_reference_price(data)
    timestamp = now_iso()
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO watch_items
                (name, model_code, search_query, aliases_json, msrp_jpy,
                 official_url, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                data["name"].strip(),
                data.get("model_code", "").strip(),
                data["search_query"].strip(),
                json.dumps(data.get("aliases", []), ensure_ascii=False),
                int(data.get("msrp_jpy", 0)),
                data.get("official_url", "").strip(),
                timestamp,
                timestamp,
            ),
        )
        item_id = cursor.lastrowid
    return get_watch_item(int(item_id))  # type: ignore[return-value]


def update_watch_item(item_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    current = get_watch_item(item_id)
    if not current:
        return None
    merged = fill_reference_price({**current, **data})
    with connect() as db:
        db.execute(
            """
            UPDATE watch_items SET
                name = ?, model_code = ?, search_query = ?, aliases_json = ?,
                msrp_jpy = ?, official_url = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(merged["name"]).strip(),
                str(merged.get("model_code", "")).strip(),
                str(merged["search_query"]).strip(),
                json.dumps(merged.get("aliases", []), ensure_ascii=False),
                int(merged.get("msrp_jpy", 0)),
                str(merged.get("official_url", "")).strip(),
                now_iso(),
                item_id,
            ),
        )
    return get_watch_item(item_id)


def backfill_reference_prices() -> int:
    """Repair missing prices for all known watches, including purchased items."""
    updated = 0
    with connect() as db:
        rows = db.execute("SELECT * FROM watch_items WHERE msrp_jpy <= 0").fetchall()
        for row in rows:
            item = fill_reference_price(row_to_item(row))
            if item.get("msrp_jpy", 0) <= 0:
                continue
            db.execute(
                "UPDATE watch_items SET msrp_jpy = ?, official_url = ?, updated_at = ? WHERE id = ? AND msrp_jpy <= 0",
                (item["msrp_jpy"], item["official_url"], now_iso(), item["id"]),
            )
            updated += 1
    return updated


def set_purchased(
    item_id: int,
    purchased: bool,
    purchase_price_cny: float | None = None,
) -> bool:
    with connect() as db:
        cursor = db.execute(
            """
            UPDATE watch_items
            SET active = ?, purchased_at = ?, purchase_price_cny = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                0 if purchased else 1,
                now_iso() if purchased else None,
                round(float(purchase_price_cny), 2)
                if purchased and purchase_price_cny is not None
                else None,
                now_iso(),
                item_id,
            ),
        )
        return cursor.rowcount > 0


def get_month_purchase_summary(month: str | None = None) -> dict[str, Any]:
    target_month = month or datetime.now().astimezone().strftime("%Y-%m")
    with connect() as db:
        row = db.execute(
            """
            SELECT
                COUNT(*) AS item_count,
                COALESCE(SUM(purchase_price_cny), 0) AS total_cny,
                SUM(CASE WHEN purchase_price_cny IS NULL THEN 1 ELSE 0 END) AS unknown_count
            FROM watch_items
            WHERE active = 0 AND substr(purchased_at, 1, 7) = ?
            """,
            (target_month,),
        ).fetchone()
    total = round(float(row["total_cny"] or 0), 2)
    return {
        "month": target_month,
        "item_count": int(row["item_count"] or 0),
        "unknown_count": int(row["unknown_count"] or 0),
        "total_cny": total,
        "anger_level": "rage" if total > 500 else "warning" if total > 300 else "calm",
    }


def get_settings() -> dict[str, str]:
    with connect() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_settings(values: dict[str, Any]) -> None:
    with connect() as db:
        for key, value in values.items():
            db.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, "" if value is None else str(value)),
            )


def delete_settings(keys: list[str]) -> None:
    if not keys:
        return
    placeholders = ",".join("?" for _ in keys)
    with connect() as db:
        db.execute(f"DELETE FROM settings WHERE key IN ({placeholders})", keys)


def create_run() -> int:
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO runs(started_at, status) VALUES (?, 'running')",
            (now_iso(),),
        )
        return int(cursor.lastrowid)


def finish_run(
    run_id: int,
    status: str,
    *,
    error: str | None = None,
    fx_rate: float | None = None,
    fx_date: str | None = None,
    fx_source: str | None = None,
) -> None:
    with connect() as db:
        db.execute(
            """
            UPDATE runs SET finished_at = ?, status = ?, error = ?,
                fx_rate = ?, fx_date = ?, fx_source = ?
            WHERE id = ?
            """,
            (now_iso(), status, error, fx_rate, fx_date, fx_source, run_id),
        )


def add_listings(run_id: int, watch_item_id: int, listings: list[dict[str, Any]]) -> None:
    with connect() as db:
        blocked = {(row['platform'], row['item_key']) for row in db.execute(
            'SELECT platform,item_key FROM blocked_listings'
        ).fetchall()}
        for listing in listings:
            if (listing.get('platform', 'xianyu'), listing['item_key']) in blocked:
                continue
            db.execute(
                """
                INSERT OR REPLACE INTO listings
                    (run_id, watch_item_id, item_key, title, item_price,
                     shipping_price, total_price, shipping_status, origin,
                     detail_url, image_url, seller, condition_status, eligible,
                     reject_reason, observed_at, rule_version, source_text, platform, price_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    watch_item_id,
                    listing["item_key"],
                    listing["title"],
                    listing["item_price"],
                    listing.get("shipping_price"),
                    listing.get("total_price"),
                    listing["shipping_status"],
                    listing.get("origin", ""),
                    listing["detail_url"],
                    listing.get("image_url", ""),
                    listing.get("seller", ""),
                    listing["condition_status"],
                    1 if listing.get("eligible") else 0,
                    listing.get("reject_reason", ""),
                    listing.get("observed_at", now_iso()),
                    int(listing.get("rule_version", 0)),
                    str(listing.get("source_text", ""))[:2000],
                    listing.get("platform", "xianyu"),
                    listing.get("price_note", ""),
                ),
            )


def get_latest_run() -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def list_runs(limit: int = 10) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_latest_successful_run_id() -> int | None:
    """最近一次**抓到过数据**的 run（success 或 partial）。

    UI 用它判断某张卡片是不是旧批次：卡片的 latest_run_id 小于它，
    就说明这一次侦察没覆盖到这个型号，报价必须标成过期。
    """
    with connect() as db:
        row = db.execute(
            """
            SELECT MAX(id) AS run_id FROM runs
            WHERE status IN ('success', 'partial')
            """
        ).fetchone()
    return row["run_id"] if row and row["run_id"] is not None else None


def _listing_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    result["eligible"] = bool(result["eligible"])
    return result


def block_listing(listing_id: int) -> dict[str, Any] | None:
    with connect() as db:
        listing = db.execute('SELECT * FROM listings WHERE id=?', (listing_id,)).fetchone()
        if listing is None:
            return None
        db.execute('''INSERT OR IGNORE INTO blocked_listings
            (platform,item_key,title,detail_url,blocked_at) VALUES (?,?,?,?,?)''',
            (listing['platform'], listing['item_key'], listing['title'], listing['detail_url'], now_iso()))
        row = db.execute('SELECT * FROM blocked_listings WHERE platform=? AND item_key=?',
                         (listing['platform'], listing['item_key'])).fetchone()
    return dict(row)


def list_blocked_listings() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute('SELECT * FROM blocked_listings ORDER BY blocked_at DESC,id DESC').fetchall()
    return [dict(row) for row in rows]


def get_blocked_item_keys(platform: str) -> set[str]:
    with connect() as db:
        return {row['item_key'] for row in db.execute(
            'SELECT item_key FROM blocked_listings WHERE platform=?', (platform,)).fetchall()}


def unblock_listing(block_id: int) -> bool:
    with connect() as db:
        cursor = db.execute('DELETE FROM blocked_listings WHERE id=?', (block_id,))
    return cursor.rowcount > 0


def get_item_summary(item_id: int) -> dict[str, Any]:
    with connect() as db:
        latest_rows = db.execute(
            """
            SELECT l.platform, MAX(l.run_id) AS run_id
            FROM listings l JOIN runs r ON r.id = l.run_id
            WHERE l.watch_item_id = ? AND r.status IN ('success', 'partial')
            GROUP BY l.platform
            """,
            (item_id,),
        ).fetchall()
        latest_by_platform = {row['platform']:row['run_id'] for row in latest_rows}
        latest_run = max(latest_by_platform.values(), default=None)
        scope = f'watch_item_id=? AND {UNBLOCKED_LISTING_SQL} AND (' + ' OR '.join('(platform=? AND run_id=?)' for _ in latest_rows) + ')'
        params = (item_id, *[value for pair in latest_by_platform.items() for value in pair])
        attempts = {row['platform']:dict(row) for row in db.execute('''
            SELECT m.*,r.started_at FROM marketplace_runs m JOIN runs r ON r.id=m.run_id
            WHERE m.run_id=(SELECT MAX(m2.run_id) FROM marketplace_runs m2 WHERE m2.platform=m.platform)
        ''').fetchall()}

        def view(row):
            result = _listing_dict(row)
            if result and result['platform'] in attempts:
                attempt = attempts[result['platform']]
                result['is_stale'] = (result['run_id'] != attempt['run_id']
                    or attempt['status'] not in {'success','partial'}
                    or result['observed_at'] < attempt['started_at'])
            return result

        current = None
        unknown_shipping = None
        candidates: list[dict[str, Any]] = []
        shipping_pending: list[dict[str, Any]] = []
        review_pending: list[dict[str, Any]] = []
        marketplace_lowest = []
        top_three = []
        if latest_run is not None:
            for platform in ('xianyu', 'pdd'):
                if platform not in latest_by_platform:
                    continue
                floor = db.execute(f'''SELECT * FROM listings WHERE watch_item_id=? AND run_id=? AND platform=?
                    AND {UNBLOCKED_LISTING_SQL}
                    AND eligible=1 AND total_price IS NOT NULL ORDER BY total_price,item_price LIMIT 1''',
                    (item_id, latest_by_platform[platform], platform)).fetchone()
                if floor:
                    marketplace_lowest.append(view(floor))
            current = db.execute(
                f"""
                SELECT * FROM listings
                WHERE {scope} AND eligible = 1
                  AND total_price IS NOT NULL
                ORDER BY total_price ASC, item_price ASC LIMIT 1
                """,
                params,
            ).fetchone()
            unknown_shipping = db.execute(
                f"""
                SELECT * FROM listings
                WHERE {scope} AND eligible = 1
                  AND total_price IS NULL
                ORDER BY item_price ASC LIMIT 1
                """,
                params,
            ).fetchone()
            shipping_pending = [view(row) for row in db.execute(
                f"""SELECT * FROM listings WHERE {scope}
                AND eligible = 1 AND total_price IS NULL ORDER BY item_price ASC LIMIT 3""",
                params,
            ).fetchall()]
            review_pending = [view(row) for row in db.execute(
                f"""SELECT * FROM listings WHERE {scope} AND eligible = 0
                AND condition_status IN ('成色待确认', '系列待确认', '版本待确认', '多件价格待确认', '规格价格待确认')
                ORDER BY item_price ASC LIMIT 3""", params,
            ).fetchall()]
            rows = db.execute(
                f"""
                SELECT * FROM listings
                WHERE {scope}
                ORDER BY eligible DESC, total_price IS NULL ASC,
                         COALESCE(total_price, item_price) ASC LIMIT 8
                """,
                params,
            ).fetchall()
            candidates = [view(row) for row in rows]
            top_three = [view(row) for row in db.execute(
                f'''SELECT * FROM listings WHERE {scope} AND eligible=1 AND total_price IS NOT NULL
                ORDER BY total_price,item_price LIMIT 3''', params,
            ).fetchall()]

        history_rows = db.execute(
            f"""
            SELECT * FROM listings
            WHERE watch_item_id = ? AND total_price IS NOT NULL
              AND {UNBLOCKED_LISTING_SQL}
              AND (eligible = 1 OR (platform = 'xianyu' AND source_text != ''))
            ORDER BY total_price ASC, observed_at ASC
            """,
            (item_id,),
        ).fetchall()
        watch_row = db.execute('SELECT * FROM watch_items WHERE id=?', (item_id,)).fetchone()
        history = None
        if watch_row:
            watch = row_to_item(watch_row)
            aliases, grade = watch_requirements(watch)
            for row in history_rows:
                if row['shipping_status'] == 'free_badge' and explicitly_paid_shipping(row['source_text'] or row['title']):
                    continue
                result = classify_listing(row['source_text'] or row['title'], aliases, title=row['title'],
                    expected_grade=grade, expected_edition=watch_edition(watch), retail=row['platform']=='pdd')
                if result.eligible:
                    history = _listing_dict(row)
                    history['rule_version'] = FILTER_RULE_VERSION
                    history['revalidated'] = True
                    break

    return {
        "current": view(current),
        "unknown_shipping": view(unknown_shipping),
        "shipping_pending": shipping_pending,
        "review_pending": review_pending,
        "marketplace_lowest": marketplace_lowest,
        "top_three": top_three,
        "history": history,
        "candidates": candidates,
        "latest_run_id": latest_run,
    }


def get_run_top_listings(
    item_id: int,
    run_id: int,
    limit: int = 3,
) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            f"""
            SELECT * FROM listings
            WHERE watch_item_id = ? AND run_id = ? AND eligible = 1 AND total_price IS NOT NULL
              AND {UNBLOCKED_LISTING_SQL}
            ORDER BY total_price IS NULL ASC,
                     COALESCE(total_price, item_price) ASC,
                     item_price ASC
            LIMIT ?
            """,
            (item_id, run_id, max(1, min(10, limit))),
        ).fetchall()
    return [_listing_dict(row) for row in rows]  # type: ignore[list-item]


def set_marketplace_status(run_id: int, platform: str, status: str, error: str = '') -> None:
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO marketplace_runs(run_id,platform,status,error) VALUES (?,?,?,?)',
                   (run_id, platform, status, error))


def get_marketplace_status() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute('''SELECT m.*, r.started_at FROM marketplace_runs m JOIN runs r ON r.id=m.run_id
            WHERE m.run_id=(SELECT MAX(m2.run_id) FROM marketplace_runs m2 WHERE m2.platform=m.platform)
            ORDER BY m.platform''').fetchall()
    return [dict(row) for row in rows]
