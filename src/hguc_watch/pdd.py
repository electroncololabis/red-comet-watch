from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from urllib.parse import quote, urlsplit, parse_qs

from playwright.async_api import async_playwright
from playwright.async_api import Error as PlaywrightError

from .config import LOGIN_LOCK_PATHS, PDD_BROWSER_PROFILE_DIR
from .xianyu import _installed_browser
from .xianyu import LoginRequiredError, VerificationRequiredError, SearchParseError, EmptyResultError, NetworkError
from .xianyu import ListingBatch, XianyuError, PlatformBusyError
from .filtering import FILTER_RULE_VERSION, classify_listing, watch_requirements, watch_edition, parse_price, parse_shipping, normalize_text


CARD_SELECTOR = '._3glhOBhU'
TITLE_SELECTOR = '._3ANzdjkc'


def _clean(text: str) -> str:
    return re.sub(r'[\u200b-\u200f\ufeff\ue000-\uf8ff]', '', text or '')


async def _wait_page(page, *, search: bool):
    for _ in range(20):
        text = _clean(await page.locator('body').inner_text())
        if 'login' in urlsplit(page.url).path:
            raise LoginRequiredError('拼多多需要登录，请点击“拼多多登录”后扫码')
        if any(word in text for word in ('系统繁忙', '操作过于频繁', '访问过于频繁')):
            raise PlatformBusyError('拼多多提示系统繁忙，请稍后再试；已停止继续访问')
        if any(word in text for word in ('安全验证', '滑动验证', '请完成验证', '访问验证')):
            raise VerificationRequiredError('拼多多需要本人完成安全验证')
        if search and await page.locator(CARD_SELECTOR).count():
            return
        if not search and await page.locator('.tLYIg_Ju').count() and '¥' in text:
            return
        if any(word in text for word in ('没有找到相关', '暂无相关商品')):
            raise EmptyResultError('拼多多这个关键词没有结果')
        await page.wait_for_timeout(750)
    raise SearchParseError('拼多多页面未加载出可识别的商品；本次不写入报价')


async def _cards(page):
    rows = await page.locator(CARD_SELECTOR).evaluate_all('''nodes => nodes.map((node,index) => ({
        index, title:(node.querySelector('._3ANzdjkc')?.textContent || node.innerText?.split('\\n')[0] || '').trim(),
        price_text:(node.querySelector('._3_U04GgA')?.innerText || node.innerText || '').trim(),
        text:node.innerText,
        image:node.querySelector('img')?.src || ''
    }))''')
    for row in rows:
        row['title'] = _clean(row['title'])
    return rows


async def _detail(page, card):
    match = None
    for attempt in range(4):
        cards = await _cards(page)
        same_title = [row for row in cards if normalize_text(row['title']) == normalize_text(card['title'])]
        if len(same_title) == 1:
            match = same_title[0]
        else:
            image_matches = [row for row in same_title if urlsplit(row['image']).path == urlsplit(card['image']).path]
            if len(image_matches) == 1:
                match = image_matches[0]
            else:
                sales = re.search(r'本店已拼[^\n]*',card['text'])
                matches = [row for row in same_title if parse_price(row['price_text']) == parse_price(card['price_text'])
                           and (not sales or sales.group() in row['text'])]
                if len(matches) == 1:
                    match = matches[0]
        if match:
            break
        await page.mouse.wheel(0, 900)
        await page.wait_for_timeout(750)
    if not match:
        raise SearchParseError('返回搜索页后未能定位原商品，跳过该条以避免错配')
    card.update(match)
    target = page.locator(CARD_SELECTOR).nth(match['index'])
    title_node = target.locator(TITLE_SELECTOR)
    if await title_node.count():
        await title_node.click(position={'x':4,'y':4}, timeout=10000)
    else:
        await target.click(position={'x':4,'y':4}, timeout=10000)
    try:
        await _wait_page(page, search=False)
        for _ in range(8):
            if await page.locator('.sku-plus1 .sku-specs-key').count():
                break
            await page.wait_for_timeout(500)
        goods_id = parse_qs(urlsplit(page.url).query).get('goods_id', [''])[0]
        if not goods_id.isdigit():
            raise SearchParseError('拼多多商品页没有正规商品ID')
        data = await page.evaluate('''() => {
            const text = sel => document.querySelector(sel)?.innerText?.trim() || '';
            const sku = document.querySelector('.sku-plus1');
            return {
                title:text('.tLYIg_Ju'), price_text:text('.JYElDCH4'),
                description:text('.GhxhoVMH'),
                sku_price:sku?.querySelector('.ujEqGzEB')?.textContent || '',
                selected:sku?.querySelector('.Mbx2m60G')?.textContent || '',
                groups:sku ? [...sku.querySelectorAll('.bIhLWVqm')].map(group=>({
                    name:group.querySelector('.sku-specs-key')?.textContent || '',
                    options:[...group.querySelectorAll('[role=button][aria-label]')].map(n=>n.getAttribute('aria-label'))
                })) : [],
                shipping:[...document.querySelectorAll('div,span')].filter(n=>n.children.length === 0 && /^(全场包邮|包邮|免运费|运费[：:¥￥0-9])/.test(n.innerText||'')).map(n=>n.innerText).join('\\n')
            };
        }''')
        data['goods_id'] = goods_id
        return data
    finally:
        await page.go_back(wait_until='domcontentloaded', timeout=45000)
        await _wait_page(page, search=True)


def build_listing(card: dict, detail: dict, item: dict) -> dict | None:
    aliases, grade = watch_requirements(item)
    title = _clean(detail.get('title') or card['title'])
    description = _clean(detail.get('description', ''))
    groups = detail.get('groups') or []
    selected = _clean(detail.get('selected', ''))
    evidence = '\n'.join((title, description, selected))
    result = classify_listing(evidence, aliases, title=title, expected_grade=grade,
                              expected_edition=watch_edition(item), retail=True)
    item_price = parse_price(detail.get('price_text', ''))
    if item_price is None:
        return None
    shipping_price, shipping_status = parse_shipping(detail.get('shipping', ''))
    eligible = result.eligible
    condition, reason = result.condition_status, result.reason
    note = '拼单价'
    # A range or multiple variants has not established the target SKU's price.
    single_sku = bool(groups) and all(len(group['options']) == 1 for group in groups)
    sku_price = parse_price(detail.get('sku_price', ''))
    price_range = re.search(r'\d\s*(?:[-~～—]|起)', detail.get('sku_price', '') + ' ' + detail.get('price_text', ''))
    if eligible and (not single_sku or price_range or sku_price is None or abs(sku_price-item_price) > 0.01):
        eligible, condition, reason = False, '规格价格待确认', '存在多个规格或规格价格未确认，需核对对应型号的成交价'
    if eligible and '券后' in card.get('price_text', ''):
        eligible, condition, reason = False, '规格价格待确认', '券后价的领取条件尚未确认'
        note = '券后价待核实'
    goods_id = detail['goods_id']
    return {
        'item_key':'pdd:' + goods_id, 'platform':'pdd', 'title':title, 'item_price':item_price,
        'shipping_price':shipping_price, 'shipping_status':shipping_status,
        'total_price':round(item_price+shipping_price,2) if shipping_price is not None else None,
        'origin':'', 'detail_url':'https://mobile.yangkeduo.com/goods.html?goods_id=' + goods_id,
        'image_url':card.get('image',''), 'seller':'', 'condition_status':condition,
        'eligible':eligible, 'reject_reason':reason, 'price_note':note,
        'observed_at':datetime.now().astimezone().isoformat(timespec='seconds'),
        'rule_version':FILTER_RULE_VERSION,
        'source_text':(evidence + '\n规格显示价：' + detail.get('sku_price','')
                       + '\n规格选项：' + json.dumps(groups,ensure_ascii=False)
                       + '\n运费说明：' + detail.get('shipping',''))[:2000],
    }


async def scrape_watch_item(context, item: dict, *, max_results: int = 30, detail_checks: int = 12, blocked_keys: set[str] | None = None):
    aliases, grade = watch_requirements(item)
    names = [alias for alias in aliases if re.search(r'[\u4e00-\u9fff]', alias) and len(alias) <= 12][:2] or [item['name']]
    queries = [f'万代 {grade} {name} {watch_edition(item)}'.strip() for name in names]
    results, errors, seen = [], [], set()
    for query in queries:
        try:
            batch = await _scrape_query(context, item, query, max_results=max_results, detail_checks=detail_checks,
                                        blocked_keys=blocked_keys)
            for row in batch:
                if row['item_key'] not in seen:
                    results.append(row)
                    seen.add(row['item_key'])
            errors.extend(batch.errors)
        except EmptyResultError:
            continue
        except XianyuError as exc:
            errors.append(exc)
        if any(isinstance(exc, (LoginRequiredError, VerificationRequiredError, PlatformBusyError)) for exc in errors):
            break
        if len([row for row in results if row['eligible'] and row['total_price'] is not None]) >= 3:
            break
        await asyncio.sleep(2.5)
    if not results and errors:
        raise errors[0]
    return ListingBatch(results, errors)


async def _scrape_query(context, item: dict, query: str, *, max_results: int, detail_checks: int, blocked_keys: set[str] | None = None):
    aliases, grade = watch_requirements(item)
    page = await context.new_page()
    try:
        await page.bring_to_front()
        try:
            await page.goto(search_url(query), wait_until='domcontentloaded', timeout=60000)
        except Exception as exc:
            raise NetworkError(f'拼多多搜索页打开失败：{type(exc).__name__}') from exc
        await _wait_page(page, search=True)
        for _ in range(2):
            await page.mouse.wheel(0,700)
            await page.wait_for_timeout(750)
        cards = await _cards(page)
        if not any(card['title'] and parse_price(card['price_text']) is not None for card in cards):
            raise SearchParseError('拼多多商品卡片存在，但标题或价格未解析出来')
        usable = []
        for card in cards:
            card['title'] = _clean(card['title'])
            classification = classify_listing(card['title'], aliases, title=card['title'],
                                              expected_grade=grade, expected_edition=watch_edition(item), retail=True)
            if classification.eligible or classification.condition_status in {'成色待确认','版本待确认','系列待确认'}:
                if parse_price(card['price_text']) is not None:
                    usable.append(card)
        usable.sort(key=lambda c:parse_price(c['price_text']) or 10**9)
        results, seen, errors = [], set(), []
        for card in usable[:min(max_results,detail_checks)]:
            verified = sorted(r['total_price'] for r in results if r['eligible'] and r['total_price'] is not None)
            if len(verified) >= 3 and (parse_price(card['price_text']) or 0) >= verified[2]:
                break
            try:
                detail = await _detail(page,card)
                listing = build_listing(card,detail,item)
            except (LoginRequiredError, VerificationRequiredError, PlatformBusyError) as exc:
                errors.append(exc)
                break
            except XianyuError as exc:
                errors.append(exc)
                continue
            if listing and listing['item_key'] not in seen:
                seen.add(listing['item_key'])
                if listing['item_key'] not in (blocked_keys or set()):
                    results.append(listing)
            await asyncio.sleep(1.5)
        return ListingBatch(results, errors)
    finally:
        await page.close()


def search_url(query: str) -> str:
    return 'https://mobile.yangkeduo.com/search_result.html?search_key=' + quote(query)


async def launch_context(headless: bool = False):
    driver = await async_playwright().start()
    browser = _installed_browser()
    kwargs = {'headless': headless, 'chromium_sandbox': True, 'locale': 'zh-CN', 'timezone_id': 'Asia/Shanghai',
              'viewport': {'width': 480, 'height': 960}}
    if browser.executable_path:
        kwargs['executable_path'] = browser.executable_path
    else:
        kwargs['channel'] = browser.channel
    try:
        context = await driver.chromium.launch_persistent_context(str(PDD_BROWSER_PROFILE_DIR), **kwargs)
    except Exception:
        await driver.stop()
        raise
    return driver, context


async def open_login_window(timeout_minutes: int = 10):
    driver, context = await launch_context()
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(search_url('万代 MG 主天使'), wait_until='domcontentloaded', timeout=60000)
        for _ in range(15):
            if 'login' in urlsplit(page.url).path:
                break
            await page.wait_for_timeout(1000)
        qr_tab = page.get_by_text('扫码登录', exact=True)
        if await qr_tab.count():
            await qr_tab.click()
        print('拼多多窗口已打开，等待本人完成登录。', flush=True)
        reported = False
        for _ in range(timeout_minutes * 30):
            if page.is_closed():
                return
            if 'login' not in urlsplit(page.url).path:
                text = await page.locator('body').inner_text()
                if ('搜索' in text or '商品' in text) and not reported:
                    print('已进入拼多多页面。窗口保持打开，请确认登录后关闭窗口。', flush=True)
                    reported = True
            try:
                await page.wait_for_timeout(2000)
            except PlaywrightError:
                if page.is_closed():
                    return
                raise
        print('登录等待结束，可稍后从面板重新打开。', flush=True)
    finally:
        await context.close()
        await driver.stop()


if __name__ == '__main__':
    import asyncio
    from filelock import FileLock

    with FileLock(str(LOGIN_LOCK_PATHS[1]), timeout=0):
        asyncio.run(open_login_window())
