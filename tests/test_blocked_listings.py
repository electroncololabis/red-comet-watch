from pathlib import Path

import httpx
import pytest

from hguc_watch import db


@pytest.fixture
def watch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'blocked.db')
    db.init_db()
    return db.list_watch_items()[0]


def quote(key, price, *, platform='xianyu', eligible=True, **overrides):
    title = '万代 HGUC 亚希玛 全新未拼'
    return dict(item_key=key, platform=platform, title=title,
                source_text=title, item_price=price, shipping_price=0,
                total_price=price, shipping_status='free', eligible=eligible,
                condition_status='全新未拼', detail_url=f'https://example.com/item?id={key}',
                **overrides)


def listing_id(run, key, platform='xianyu'):
    with db.connect() as connection:
        return connection.execute('SELECT id FROM listings WHERE run_id=? AND item_key=? AND platform=?',
                                  (run, key, platform)).fetchone()['id']


def displayed(summary):
    singles = [summary[key] for key in ('current', 'unknown_shipping', 'history') if summary[key]]
    return singles + [row for key in ('top_three', 'marketplace_lowest', 'candidates', 'shipping_pending', 'review_pending')
                      for row in summary[key]]


def test_blocking_removes_all_occurrences_and_refills_rankings_before_limits(watch):
    old = db.create_run()
    db.add_listings(old, watch['id'], [quote('shared', 80, eligible=False)])
    db.finish_run(old, 'success')
    other_market = db.create_run()
    db.add_listings(other_market, watch['id'], [quote('shared', 150, platform='pdd')])
    db.finish_run(other_market, 'success')
    current = db.create_run()
    db.add_listings(current, watch['id'], [quote('shared', 100), quote('b', 110),
                                        quote('c', 120), quote('d', 130), quote('e', 140)])
    db.finish_run(current, 'success')
    assert db.get_item_summary(watch['id'])['history']['total_price'] == 80
    row_id = listing_id(current, 'shared')
    blocked = db.block_listing(row_id)
    assert db.block_listing(row_id) == blocked, 'Repeated clicks must not create duplicate rules.'
    summary = db.get_item_summary(watch['id'])
    assert summary['current']['total_price'] == 110
    assert summary['history']['total_price'] == 110, 'History revalidation must not resurrect a blocked record.'
    assert [row['total_price'] for row in summary['top_three']] == [110, 120, 130]
    assert [row['total_price'] for row in db.get_run_top_listings(watch['id'], current)] == [110, 120, 130]
    assert not any(row['platform'] == 'xianyu' and row['item_key'] == 'shared' for row in displayed(summary))
    assert any(row['platform'] == 'pdd' and row['item_key'] == 'shared' for row in summary['marketplace_lowest'])
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM listings').fetchone()[0] == 7
    assert db.get_watch_item(watch['id'])['active'], 'Removing a product must not remove its watched model.'


def test_blocks_survive_restart_and_changed_urls_in_future_scans_for_every_watch(watch):
    other = db.create_watch_item({**watch, 'name': '亚希玛备用观察'})
    first = db.create_run()
    db.add_listings(first, watch['id'], [quote('stable-id', 100)])
    db.add_listings(first, other['id'], [quote('stable-id', 100)])
    db.finish_run(first, 'success')
    block = db.block_listing(listing_id(first, 'stable-id'))
    db.init_db()
    assert db.list_blocked_listings() == [block]
    assert db.get_item_summary(other['id'])['current'] is None
    second = db.create_run()
    changed = quote('stable-id', 90)
    changed['detail_url'] = 'https://example.com/item?id=stable-id&tracking=new-link'
    db.add_listings(second, watch['id'], [changed, quote('next', 120)])
    db.add_listings(second, other['id'], [changed])
    db.finish_run(second, 'success')
    assert db.get_item_summary(watch['id'])['current']['item_key'] == 'next'
    assert db.get_item_summary(other['id'])['current'] is None
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM listings WHERE run_id=? AND item_key='stable-id'", (second,)).fetchone()[0] == 0
    assert db.unblock_listing(block['id'])
    assert db.list_blocked_listings() == []
    assert db.get_item_summary(other['id'])['current']['item_key'] == 'stable-id'
    third = db.create_run()
    db.add_listings(third, watch['id'], [changed])
    db.finish_run(third, 'success')
    assert db.get_item_summary(watch['id'])['current']['total_price'] == 90


def test_pending_shipping_and_specification_links_can_be_removed(watch):
    run = db.create_run()
    shipping = quote('shipping', 90)
    shipping.update(shipping_price=None, total_price=None, shipping_status='unknown')
    review = quote('review', 80, eligible=False, platform='pdd')
    review.update(title='万代 HGUC 亚希玛 成色待确认', source_text='', condition_status='规格价格待确认')
    db.add_listings(run, watch['id'], [shipping, review])
    db.finish_run(run, 'success')
    before = db.get_item_summary(watch['id'])
    assert before['unknown_shipping']
    assert before['review_pending']
    db.block_listing(listing_id(run, 'shipping'))
    db.block_listing(listing_id(run, 'review', 'pdd'))
    assert displayed(db.get_item_summary(watch['id'])) == []


def test_unknown_block_or_restore_does_not_change_any_records(watch):
    assert db.block_listing(999999) is None
    assert not db.unblock_listing(999999)
    assert db.list_blocked_listings() == []


async def test_block_and_restore_api_with_dashboard_exclusion(watch, monkeypatch):
    from hguc_watch import app as web_app

    monkeypatch.setattr(web_app, 'task_status', lambda: {})
    run = db.create_run()
    db.add_listings(run, watch['id'], [quote('one', 100), quote('two', 110)])
    db.finish_run(run, 'success')
    row_id = listing_id(run, 'one')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app.app), base_url='http://local.test') as client:
        missing = await client.post('/api/listings/999999/block')
        assert missing.status_code == 404
        blocked = await client.post(f'/api/listings/{row_id}/block')
        assert blocked.status_code == 200
        block_id = blocked.json()['blocked']['id']
        dashboard = (await client.get('/api/dashboard')).json()
        item = next(item for item in dashboard['items'] if item['id'] == watch['id'])
        assert item['summary']['current']['item_key'] == 'two'
        assert dashboard['blocked_listings'][0]['id'] == block_id
        assert len((await client.get('/api/blocked-listings')).json()['items']) == 1
        restored = await client.delete(f'/api/blocked-listings/{block_id}')
        assert restored.status_code == 200
        assert db.get_item_summary(watch['id'])['current']['item_key'] == 'one'
        assert (await client.delete(f'/api/blocked-listings/{block_id}')).status_code == 404
