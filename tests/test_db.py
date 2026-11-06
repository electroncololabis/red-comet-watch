from __future__ import annotations

from pathlib import Path

import hguc_watch.db as db_module


def test_seed_and_history_summary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()
    items = db_module.list_watch_items(include_purchased=False)
    assert len(items) == 7
    item = items[0]

    run1 = db_module.create_run()
    db_module.add_listings(
        run1,
        item["id"],
        [
            {
                "item_key": "first",
                "title": "全新未拼 亚希玛",
                "item_price": 100.0,
                "shipping_price": 10.0,
                "total_price": 110.0,
                "shipping_status": "platform",
                "detail_url": "https://www.goofish.com/item?id=first",
                "condition_status": "全新未拼",
                "eligible": True,
            }
        ],
    )
    db_module.finish_run(run1, "success", fx_rate=0.05)

    run2 = db_module.create_run()
    db_module.add_listings(
        run2,
        item["id"],
        [
            {
                "item_key": "second",
                "title": "全新未拼 亚希玛 包邮",
                "item_price": 120.0,
                "shipping_price": 0.0,
                "total_price": 120.0,
                "shipping_status": "free",
                "detail_url": "https://www.goofish.com/item?id=second",
                "condition_status": "全新未拼",
                "eligible": True,
            }
        ],
    )
    db_module.finish_run(run2, "success", fx_rate=0.051)

    summary = db_module.get_item_summary(item["id"])
    assert summary["current"]["total_price"] == 120.0
    assert summary["history"]["total_price"] == 110.0


def test_purchased_stops_tracking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()
    item = db_module.list_watch_items()[0]
    assert db_module.set_purchased(item["id"], True)
    assert all(row["id"] != item["id"] for row in db_module.list_watch_items(include_purchased=False))
    assert db_module.set_purchased(item["id"], False)
    assert any(row["id"] == item["id"] for row in db_module.list_watch_items(include_purchased=False))


def test_month_purchase_total_and_anger_levels(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()
    items = db_module.list_watch_items()

    assert db_module.set_purchased(items[0]["id"], True, 180.25)
    assert db_module.set_purchased(items[1]["id"], True, 150.00)
    summary = db_module.get_month_purchase_summary()
    assert summary["item_count"] == 2
    assert summary["total_cny"] == 330.25
    assert summary["anger_level"] == "warning"

    assert db_module.set_purchased(items[2]["id"], True, 175.00)
    summary = db_module.get_month_purchase_summary()
    assert summary["total_cny"] == 505.25
    assert summary["anger_level"] == "rage"

    assert db_module.set_purchased(items[2]["id"], False)
    summary = db_module.get_month_purchase_summary()
    assert summary["total_cny"] == 330.25
    assert summary["anger_level"] == "warning"


def test_two_marketplaces_rank_by_total_and_keep_pending_candidates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db_module, 'DB_PATH', tmp_path/'markets.db')
    db_module.init_db()
    item = db_module.list_watch_items()[0]
    run = db_module.create_run()
    common = {'title':'万代 HGUC 亚希玛 全新未拼', 'detail_url':'https://example.com/item',
              'shipping_status':'free', 'condition_status':'全新未拼', 'eligible':True}
    db_module.add_listings(run,item['id'],[
        {**common,'item_key':'a','platform':'xianyu','item_price':100,'shipping_price':20,'total_price':120},
        {**common,'item_key':'pdd:a','platform':'pdd','item_price':110,'shipping_price':0,'total_price':110},
        {**common,'item_key':'b','item_price':90,'shipping_price':None,'total_price':None},
        {**common,'item_key':'pdd:b','platform':'pdd','item_price':80,'total_price':80,'eligible':False,'condition_status':'规格价格待确认'},
    ])
    db_module.finish_run(run,'success')
    summary = db_module.get_item_summary(item['id'])
    assert summary['current']['platform'] == 'pdd'
    assert summary['shipping_pending'][0]['item_price'] == 90
    assert summary['review_pending'][0]['item_price'] == 80
    assert [r['total_price'] for r in db_module.get_run_top_listings(item['id'],run)] == [110,120]


def test_failed_market_refresh_keeps_previous_quote_with_stale_label(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db_module,'DB_PATH',tmp_path/'keep.db')
    db_module.init_db()
    item=db_module.list_watch_items()[0]
    common={'title':'万代 HGUC 亚希玛 全新未拼','detail_url':'https://example.com/item',
            'shipping_status':'free','condition_status':'全新未拼','eligible':True,'shipping_price':0}
    first=db_module.create_run()
    db_module.add_listings(first,item['id'],[
        {**common,'item_key':'x','item_price':120,'total_price':120,'platform':'xianyu'},
        {**common,'item_key':'pdd:p','item_price':110,'total_price':110,'platform':'pdd'},
    ])
    db_module.finish_run(first,'success')
    db_module.set_marketplace_status(first,'xianyu','success')
    db_module.set_marketplace_status(first,'pdd','success')
    second=db_module.create_run()
    db_module.add_listings(second,item['id'],[{**common,'item_key':'x','item_price':130,'total_price':130}])
    db_module.finish_run(second,'partial')
    db_module.set_marketplace_status(second,'xianyu','success')
    db_module.set_marketplace_status(second,'pdd','failed','系统繁忙')
    summary=db_module.get_item_summary(item['id'])
    floors={row['platform']:row for row in summary['marketplace_lowest']}
    assert floors['xianyu']['total_price']==130
    assert not floors['xianyu']['is_stale']
    assert floors['pdd']['total_price']==110
    assert floors['pdd']['is_stale']
    assert summary['current']['is_stale']
    assert [row['total_price'] for row in summary['top_three']]==[110,130]
