from __future__ import annotations

import asyncio
import hashlib
import random
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, parse_qs

from playwright.async_api import BrowserContext, Page, async_playwright

from .config import BROWSER_PROFILE_DIR, ensure_data_dirs
from .filtering import (
    FILTER_RULE_VERSION,
    classify_listing,
    normalize_text,
    parse_origin,
    parse_price,
    parse_shipping,
    watch_requirements,
    watch_edition,
    explicitly_paid_shipping,
)


class XianyuError(RuntimeError):
    """抓取失败的基类。

    `cause` 是**机器可读**的失败分类，用来区分
    「登录墙 / 风控页 / 真的没结果 / 解析器坏了」四种情况在运行记录里必须能区分，
    所以每一种都有自己的 cause 值，写进 runs.error 时带上，不要都塞进
    "页面结构可能已经变化" 这一句里。

    `evidence` 放判定依据（命中的关键词、cookie 名、选择器计数等），
    不含 cookie 值、token 等敏感内容。
    """

    cause = "unknown"

    def __init__(self, message: str, *, evidence: str = "") -> None:
        super().__init__(message)
        self.evidence = evidence

    def describe(self) -> str:
        label = CAUSE_LABELS.get(self.cause, self.cause)
        text = f"[{self.cause}/{label}] {self}"
        if self.evidence:
            text += f"（依据：{self.evidence}）"
        return text


class LoginRequiredError(XianyuError):
    """登录墙：页面明确要求登录 / 登录态已失效。"""

    cause = "login_required"


class VerificationRequiredError(XianyuError):
    """风控页：滑动验证 / 安全验证 / 访问验证。"""

    cause = "verification_required"


class IllegalAccessError(XianyuError):
    """闲鱼直接拒绝该客户端（"非法访问"）。"""

    cause = "illegal_access"


class EmptyResultError(XianyuError):
    """页面正常、也确实登录了，但**这个关键词真的没有结果**。"""

    cause = "empty_result"


class SearchParseError(XianyuError):
    """页面没有可识别的商品卡片，且不属于上面任何一类 —— 解析器或 DOM 的问题。"""

    cause = "parse_failed"


class NetworkError(XianyuError):
    """导航/网络层失败（超时、DNS、连接被重置）。"""

    cause = "network"


class PlatformBusyError(XianyuError):
    cause = "service_busy"


class ListingBatch(list):
    """Keep verified rows and report any incomplete searches separately."""
    def __init__(self, rows=(), errors=()):
        super().__init__(rows)
        self.errors = list(errors)


CAUSE_LABELS = {
    "login_required": "登录墙",
    "verification_required": "风控验证",
    "illegal_access": "非法访问",
    "empty_result": "真的没结果",
    "parse_failed": "解析器/DOM 异常",
    "network": "网络错误",
    "service_busy": "平台系统繁忙",
    "unknown": "未知",
}


# 各失败原因的判定词。都是**页面文案**，不是 cookie 值。
ILLEGAL_ACCESS_MARKERS = ("非法访问", "请使用正常浏览器访问闲鱼", "请使用正常浏览器")
VERIFICATION_MARKERS = (
    "访问验证",
    "滑动验证",
    "安全验证",
    "验证码",
    "请完成验证",
    "拖动滑块",
    "行为验证",
)
LOGIN_MARKERS = ("登录后可以更懂你", "立即登录", "扫码登录", "请先登录", "登录/注册")
EMPTY_RESULT_MARKERS = (
    "没有找到",
    "暂无相关",
    "未找到相关",
    "换个关键词",
    "换个词试试",
    "无搜索结果",
    "抱歉，没有找到",
)

AUTH_COOKIE_NAMES = {"cookie2", "tracknick", "unb"}


async def _has_authenticated_session(context: BrowserContext) -> bool:
    cookie_names = {cookie["name"] for cookie in await context.cookies()}
    return AUTH_COOKIE_NAMES.issubset(cookie_names)


def _first_marker(text: str, markers: tuple[str, ...]) -> str:
    lowered = text.lower()
    for marker in markers:
        if marker.lower() in lowered:
            return marker
    return ""


@dataclass(frozen=True)
class BrowserLaunch:
    executable_path: str | None
    channel: str | None


def _installed_browser() -> BrowserLaunch:
    import os
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    candidates = [
        program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
        program_files / "Microsoft/Edge/Application/msedge.exe",
        local_app_data / "Microsoft/Edge/Application/msedge.exe",
        program_files / "Google/Chrome/Application/chrome.exe",
        program_files_x86 / "Google/Chrome/Application/chrome.exe",
        local_app_data / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if path.exists():
            return BrowserLaunch(str(path), None)
    return BrowserLaunch(None, "msedge")


async def launch_context(headless: bool) -> tuple[Any, BrowserContext]:
    ensure_data_dirs()
    playwright = await async_playwright().start()
    browser = _installed_browser()
    kwargs: dict[str, Any] = {
        "user_data_dir": str(BROWSER_PROFILE_DIR),
        "headless": headless,
        "chromium_sandbox": True,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "viewport": {"width": 1440, "height": 960},
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if browser.executable_path:
        kwargs["executable_path"] = browser.executable_path
    elif browser.channel:
        kwargs["channel"] = browser.channel
    try:
        context = await playwright.chromium.launch_persistent_context(**kwargs)
    except Exception:
        await playwright.stop()
        raise
    return playwright, context


async def open_login_window(timeout_minutes: int = 10) -> None:
    playwright, context = await launch_context(headless=False)
    try:
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        target = search_url("HGUC 亚希玛 万代")
        login_url = f"https://www.goofish.com/login?redirectUrl={quote(target, safe='')}"
        await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
        deadline = asyncio.get_running_loop().time() + timeout_minutes * 60
        while asyncio.get_running_loop().time() < deadline:
            if page.is_closed():
                raise LoginRequiredError("登录窗口已关闭，但尚未检测到闲鱼登录成功")
            try:
                authenticated = await _has_authenticated_session(context)
                item_links = await page.locator('a[href*="/item?"]').count()
                if authenticated and item_links:
                    await page.wait_for_timeout(2_000)
                    return
            except LoginRequiredError:
                raise
            except Exception:
                pass
            await asyncio.sleep(2)
        raise LoginRequiredError("等待闲鱼登录超时，请重新打开登录窗口")
    finally:
        await context.close()
        await playwright.stop()


def search_url(query: str) -> str:
    return f"https://www.goofish.com/search?q={quote(query)}"


def _item_key(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for name in ("id", "itemId", "item_id"):
        if query.get(name):
            return str(query[name][0])
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


# 搜索卡片里"包邮"角标是一张 **图片**（见下面 _FREE_SHIPPING_BADGE_IMG_ID），
# 所以 body.innerText 里根本读不到它 —— 只能按图片 id 判断。
# 这一条是 2026-10-01 用 probe 的截图 + 图片通道核对确认的：
#   https://gw.alicdn.com/imgextra/i1/O1CN017BILHl1VNARooBnhe_!!6000000002640-2-tps-84-60.png
#   图像通道读出内容就是黑体"包邮"两个字。
# 30 张卡片里有 20 张带这张图，和页面上肉眼看到的包邮角标数量一致。
_FREE_SHIPPING_BADGE_IMG_ID = "O1CN017BILHl1VNARooBnhe"

# 卡片结构（2026-10-01 probe 的 page-search.html 实测）：
#   <div class="row1-wrap-title--xxx" title="完整标题">…</div>
#   <span class="cpv--xxx">全新未拼装</span>        <- 平台给的成色角标（文字）
#   <span class="cpv--xxx">Bandai/万代</span>
#   <div class="row3-wrap-price--xxx">
#     <span class="sign--xxx">¥</span>
#     <span class="number--xxx">179</span>
#     <span class="decimal--xxx">.89</span>          <- 整数和小数在**两个** span 里
#   </div>
# 类名后缀（--xxx）是会变的，所以全部用 [class*="前缀"] 匹配，不写死后缀。
_JS_EXTRACT_CARDS = """
(freeShippingBadgeId) => {
  const ITEM_RE = /\\/item|item\\?|item\\//i;
  const anchors = [...document.querySelectorAll('a[href]')]
    .filter(a => ITEM_RE.test(a.getAttribute('href') || ''));
  const seen = new Set();
  const rows = [];

  for (const anchor of anchors) {
    const href = anchor.href;
    if (!href || seen.has(href)) continue;
    seen.add(href);

    // 1) 找卡片容器（闲鱼卡片是 feeds-content--xxx）
    let card = null;
    for (let node = anchor; node; node = node.parentElement) {
      const cls = (node.className && node.className.toString()) || '';
      if (cls.indexOf('feeds-content') >= 0) { card = node; break; }
    }
    // 2) 找不到就退回旧逻辑：向上找带价格文本的最近祖先
    if (!card) {
      let node = anchor;
      let best = anchor;
      for (let i = 0; i < 6 && node.parentElement; i++) {
        node = node.parentElement;
        const t = (node.innerText || '').trim();
        if (t.length > (best.innerText || '').length && t.length < 900) best = node;
        if (/[¥￥]\\s*\\d/.test(t) && t.length > 25) break;
      }
      card = best;
    }

    // 标题：title 属性是**完整**标题，innerText 会被 UI 截断成 "..."
    let title = '';
    const titleNode = card.querySelector('[class*="row1-wrap-title"]');
    if (titleNode) title = titleNode.getAttribute('title') || '';
    if (!title || title.trim().length < 4) {
      const anyTitled = [...card.querySelectorAll('[title]')]
        .map(n => n.getAttribute('title') || '')
        .sort((a, b) => b.length - a.length);
      title = anyTitled.length ? anyTitled[0] : '';
    }
    if (!title || title.trim().length < 4) title = (anchor.innerText || '').trim();

    // 价格：整数和小数分在两个 span 里，必须拼起来，
    // 否则 "¥ 179 .89" 会被读成 179 而不是 179.89。
    let price = null;
    const numberNode = card.querySelector('[class*="price-wrap"] [class*="number"]')
      || card.querySelector('[class*="number--"]');
    if (numberNode) {
      const rawInt = (numberNode.innerText || '').replace(/[^0-9]/g, '');
      const wrap = numberNode.parentElement;
      const decimalNode = wrap ? wrap.querySelector('[class*="decimal"]') : null;
      let rawDec = decimalNode ? (decimalNode.innerText || '').replace(/[^0-9.]/g, '') : '';
      if (rawDec && rawDec.charAt(0) !== '.') rawDec = '.' + rawDec.replace(/\\./g, '');
      const value = parseFloat(rawInt + rawDec);
      if (isFinite(value) && value > 0) price = value;
    }

    // 包邮角标（图片）—— innerText 里没有，只能按图片 id 找
    let freeShipping = false;
    for (const img of card.querySelectorAll('img')) {
      const src = img.getAttribute('src') || img.currentSrc || '';
      if (src.indexOf(freeShippingBadgeId) >= 0) { freeShipping = true; break; }
    }

    // 平台成色角标（文字）：全新未拼装 / 已拼装 / 已涂装 / 1/144 …
    const chips = [];
    for (const node of card.querySelectorAll('[class*="cpv--"]')) {
      const t = (node.innerText || '').trim();
      if (t && t.length <= 20 && chips.indexOf(t) < 0) chips.push(t);
    }

    const productImg = card.querySelector('img[src*="bao/uploaded"]') || card.querySelector('img');
    rows.push({
      href,
      title: title.slice(0, 400),
      price,
      free_shipping: freeShipping,
      chips,
      text: (card.innerText || '').trim().slice(0, 1500),
      image: productImg ? (productImg.currentSrc || productImg.getAttribute('src') || '') : ''
    });
  }
  return rows;
}
"""


async def _extract_cards(page: Page) -> list[dict[str, Any]]:
    return await page.evaluate(_JS_EXTRACT_CARDS, _FREE_SHIPPING_BADGE_IMG_ID)


# 详情页里"猜你喜欢 / 推荐位"的容器特征。这些区域会把**别的商品**的成色、
# 包邮、型号文字混进来，必须先从文本里摘掉再参与判定。
_DETAIL_STRIP_SELECTORS = (
    '[class*="feeds"]',
    '[class*="Feeds"]',
    '[class*="recommend"]',
    '[class*="Recommend"]',
    '[class*="guess"]',
    '[class*="Guess"]',
    '[class*="similar"]',
    '[class*="Similar"]',
    '[class*="tuijian"]',
    '[class*="footer"]',
    '[class*="Footer"]',
    '[id*="recommend"]',
    '[id*="feeds"]',
)

_DETAIL_MAIN_SELECTORS = (
    '[class*="item-main-info"]',
    '[class*="item-main"]',
    '[class*="detail-main"]',
    '[class*="itemDetail"]',
    '[class*="detail-content"]',
    "main",
)

_JS_ITEM_DETAIL_TEXT = """
(args) => {
  const [stripSelectors, mainSelectors, limit] = args;
  // Read the live product area. Detached clone.innerText falls back to
  // textContent, including scripts, hidden UI and unrelated recommendations.
  let main = null;
  for (const selector of mainSelectors) {
    try { main = document.querySelector(selector); } catch (error) { main = null; }
    if (main && (main.innerText || '').trim().length > 20) break;
    main = null;
  }
  if (!main) return '';
  const excluded = [...stripSelectors, 'script', 'style', 'noscript', '[hidden]'].join(',');
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  const parts = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const parent = node.parentElement;
    if (!parent || parent.closest(excluded)) continue;
    if (!parent.getClientRects().length || getComputedStyle(parent).visibility === 'hidden') continue;
    const value = node.textContent.trim();
    if (value) parts.push(value);
  }
  return parts.join('\\n').slice(0, limit);
}
"""


async def _load_detail(context: BrowserContext, url: str) -> tuple[str, str, str]:
    """打开商品详情页，返回 (作用域内的正文, 最终 URL, 失败原因)。

    **不再** `body.inner_text()` 整页取文本：推荐位会污染成色/包邮/型号判定。
    改为在克隆体上先摘掉推荐/页脚容器，再取主区域文本。
    第三个返回值是失败原因（成功时为空串），供调用方写入证据。
    """
    page = await context.new_page()
    try:
        await page.bring_to_front()
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1800)
        try:
            text = await page.evaluate(
                _JS_ITEM_DETAIL_TEXT,
                [list(_DETAIL_STRIP_SELECTORS), list(_DETAIL_MAIN_SELECTORS), 4000],
            )
        except Exception as exc:  # noqa: BLE001
            return "", page.url, f"详情正文提取失败：{exc}"
        if not text:
            return "", url, "商品详情尚未加载或需要登录，保留搜索卡片证据"
        return text, page.url, ""
    except Exception as exc:  # noqa: BLE001
        return "", url, f"详情页打开失败：{exc}"
    finally:
        await page.close()


def _best_title(card: dict[str, Any]) -> str:
    title = (card.get("title") or "").strip()
    if len(title) >= 4:
        return title[:240]
    lines = [line.strip() for line in (card.get("text") or "").splitlines() if line.strip()]
    non_price = [line for line in lines if not re.fullmatch(r"[¥￥]?\s*\d+(?:\.\d+)?", line)]
    return (non_price[0] if non_price else (lines[0] if lines else "闲鱼商品"))[:240]


def _card_price(card: dict[str, Any]) -> float | None:
    """卡片价格：结构化字段优先，正则兜底。"""
    price = card.get("price")
    if isinstance(price, (int, float)) and price > 0:
        return float(price)
    return parse_price(card.get("text", ""))


def _strip_leading_title(card_text: str, title: str) -> str:
    """去掉卡片 innerText 的第一行 —— 那一行**就是标题**。

    闲鱼卡片的 innerText 第一行是标题（UI 会把它截断成 "…"）。
    如果不去掉，拼出来的 source_text 里标题会出现两次，于是：

      * 「标题整段重复 = 代购/占位模板」会把**每一条**商品都误判掉；
      * 「一条帖子里 3 个以上 HGUC = 多件混列」会把标题和正文各数一遍。

    两种误判都是 tests/test_card_parsing.py 里的真实卡片 fixture 抓出来的。
    只在第一行和标题确实共享足够长的前缀时才删，避免误删真的正文。
    """
    lines = card_text.splitlines()
    if len(lines) < 2:
        return card_text
    first = normalize_text(lines[0])
    if not first:
        return "\n".join(lines[1:])
    head = normalize_text(title)
    probe = min(14, len(first), len(head))
    if probe >= 8 and first[:probe] == head[:probe]:
        return "\n".join(lines[1:])
    return card_text


def build_listing(
    card: dict[str, Any],
    detail_text: str,
    aliases: list[str],
    resolved_url: str | None = None,
    *,
    expected_grade: str = "HGUC",
    expected_edition: str = "",
) -> dict[str, Any] | None:
    """把一张搜索卡片 + 该商品的详情正文，整理成一条 listing。

    各字段的取数口径（故意分开，避免互相污染）：

    * **价格**：只认搜索卡片上的价格。优先用页面结构里 number/decimal 两个 span
      拼出来的值；拿不到才回退到正则。详情页里有推荐位、套餐价、定金价，
      从详情页读价格会把别的商品的钱算到这一条上。
    * **运费**：卡片上的"包邮"是**平台角标**，而且是一张图片。
      有这张图 = 无条件包邮。没有才去看卡片文本、再看详情正文。
    * **成色 / 版本**：标题 + 平台成色角标（全新未拼装/已拼装…）+
      卡片文本 + **作用域内的**详情正文。
    * **source_text**：真正参与判定的那份文本，截断后存库，
      这样以后规则改了可以拿它重判，不用重新抓。

    `resolved_url` 必须是**跳转后**的最终 URL（详情页可能重定向）。
    """
    card_text = card.get("text", "") or ""
    title = _best_title(card)
    chips = card.get("chips") or []
    chip_text = " ".join(chips)
    # 卡片正文里第一行是标题，去掉，避免 source_text 里标题出现两次。
    card_body = _strip_leading_title(card_text, title)

    # 价格：结构化优先，正则兜底。
    item_price = card.get("price")
    if not isinstance(item_price, (int, float)) or item_price <= 0:
        item_price = parse_price(card_text)
    if item_price is None:
        return None
    item_price = float(item_price)

    detail_text = (detail_text or "").strip()
    source_text = "\n".join(
        part for part in (title, chip_text, card_body, detail_text) if part
    ).strip()

    classification = classify_listing(source_text, aliases, title=title, expected_grade=expected_grade, expected_edition=expected_edition)

    # 平台包邮角标（图片）是比卖家文案更硬的证据，优先采用。
    #
    # 真实案例（2026-10-01 核对过 HTML + 截图）：listing 246 的标题写
    # "粉丝快递包邮"（看着像条件包邮），但**闲鱼自己给它打了包邮角标**
    # （img id O1CN017BILHl1VNARooBnhe，已用图像通道确认图案就是"包邮"）。
    # 平台角标是闲鱼按商品设置算给买家的，比卖家的营销文案可信，
    # 所以这里按 0 元算，但状态单列成 free_badge，
    # 好让以后能区分"平台说包邮"和"卖家文案说包邮"。
    shipping_text = '\n'.join((title, card_body, detail_text))
    if explicitly_paid_shipping(shipping_text):
        shipping_price, shipping_status = parse_shipping(shipping_text)
    elif card.get("free_shipping"):
        shipping_price, shipping_status = 0.0, "free_badge"
    else:
        shipping_price, shipping_status = parse_shipping(title + "\n" + card_body)
        if shipping_status == "unknown" and detail_text:
            shipping_price, shipping_status = parse_shipping(detail_text)

    url = resolved_url or card.get("href", "")
    if url.startswith("/"):
        url = urljoin("https://www.goofish.com", url)
    total_price = round(item_price + shipping_price, 2) if shipping_price is not None else None
    return {
        "item_key": _item_key(url),
        "title": title,
        "item_price": item_price,
        "shipping_price": shipping_price,
        "total_price": total_price,
        "shipping_status": shipping_status,
        "origin": parse_origin(source_text),
        "detail_url": url,
        "image_url": card.get("image", ""),
        "seller": "",
        "condition_status": classification.condition_status,
        "eligible": classification.eligible,
        "reject_reason": classification.reason,
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule_version": FILTER_RULE_VERSION,
        "source_text": source_text[:1500],
        "platform_chips": chip_text,
    }


async def _read_search_diagnostics(page: Page) -> dict[str, Any]:
    """收集"这一页到底是什么"的只读证据。不打印 cookie 值。"""
    try:
        body = await page.locator("body").inner_text(timeout=15_000)
    except Exception as exc:  # noqa: BLE001
        body = ""
        body_error = str(exc)
    else:
        body_error = ""
    try:
        counts = await page.evaluate(
            """
            () => ({
              anchors: document.querySelectorAll('a[href]').length,
              itemLinks: [...document.querySelectorAll('a[href]')].filter(a =>
                /\\/item|item\\?|item\\//i.test(a.getAttribute('href') || '')).length,
              images: document.querySelectorAll('img').length,
              iframes: document.querySelectorAll('iframe').length,
            })
            """
        )
    except Exception as exc:  # noqa: BLE001
        counts = {"error": str(exc)}
    return {
        "url": page.url,
        "title": await page.title() if not page.is_closed() else "",
        "body": body,
        "body_error": body_error,
        "counts": counts,
    }


def _classify_search_failure(
    diagnostics: dict[str, Any], authenticated: bool, query: str
) -> XianyuError:
    """把一次"没有可用卡片"的搜索结果分成**可区分**的失败原因。

    分别标记登录、风控、没有结果和页面结构变化。
    """
    body = diagnostics.get("body", "") or ""
    counts = diagnostics.get("counts", {}) or {}
    evidence_bits = [
        f"url={diagnostics.get('url', '')[:120]}",
        f"title={diagnostics.get('title', '')[:60]}",
        f"cookie登录态={authenticated}",
        f"选择器计数={counts}",
        f"正文长度={len(body)}",
    ]
    evidence = "；".join(evidence_bits)

    marker = _first_marker(body, ILLEGAL_ACCESS_MARKERS)
    if marker:
        return IllegalAccessError(
            f"闲鱼拒绝了当前客户端（命中“{marker}”）。请关闭后台无头模式后重试。",
            evidence=evidence,
        )

    marker = _first_marker(body, VERIFICATION_MARKERS)
    if marker:
        return VerificationRequiredError(
            f"闲鱼要求人工验证（命中“{marker}”）。请打开登录窗口，由你本人完成验证。",
            evidence=evidence,
        )

    marker = _first_marker(body, LOGIN_MARKERS)
    if marker:
        return LoginRequiredError(
            f"闲鱼要求登录（命中“{marker}”）。请点击“打开闲鱼登录”扫码后重试。",
            evidence=evidence,
        )

    if not authenticated:
        return LoginRequiredError(
            "页面没有商品卡片，且登录态 cookie 不完整；最可能是登录已失效。"
            "请重新扫码登录后再试。",
            evidence=evidence,
        )

    marker = _first_marker(body, EMPTY_RESULT_MARKERS)
    if marker:
        return EmptyResultError(
            f"关键词“{query}”这次确实没有搜索结果（命中“{marker}”），不是登录或解析问题。",
            evidence=evidence,
        )

    return SearchParseError(
        "已登录、页面也有正常正文，但一条商品链接都没解析出来 —— "
        "这一类才是真正的 DOM/选择器问题，需要用 probe_search.py 的截图核对。",
        evidence=evidence,
    )


async def scrape_watch_item(
    context: BrowserContext,
    item: dict[str, Any],
    *,
    max_results: int = 30,
    detail_checks: int = 12,
    blocked_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    aliases, grade = watch_requirements(item)
    edition = watch_edition(item)
    queries = search_queries(item)
    cards: list[dict[str, Any]] = []
    search_errors = []
    for query in queries:
        try:
            cards.extend(await _search_cards(context, query, max_results))
        except EmptyResultError:
            continue
        except XianyuError as exc:
            search_errors.append(exc)
            if isinstance(exc, (LoginRequiredError, VerificationRequiredError, IllegalAccessError)):
                break
        if query != queries[-1]:
            await asyncio.sleep(2.5)
    if not cards:
        if search_errors:
            raise search_errors[0]
        raise EmptyResultError(f"“{item['name']}”的搜索词均未返回商品")

    unique: dict[str, dict[str, Any]] = {}
    for card in cards:
        if _card_price(card) is not None:
            unique.setdefault(_item_key(card['href']), card)
    if not unique:
        raise SearchParseError("找到了商品链接，但卡片上没有可解析的价格")
    candidates = sorted((card for key, card in unique.items() if key not in (blocked_keys or set())),
                        key=lambda c: _card_price(c) or 10**9)
    # Keep relevant kits before inexpensive spare parts when applying the cap.
    candidates.sort(key=lambda c: not needs_detail(build_listing(c, '', aliases, expected_grade=grade, expected_edition=edition)))
    candidates = candidates[:max_results]
    remaining = 0 if any(isinstance(e, (LoginRequiredError, VerificationRequiredError, IllegalAccessError)) for e in search_errors) else detail_checks
    results = []
    for card in candidates:
        listing = build_listing(card, '', aliases, expected_grade=grade, expected_edition=edition)
        if remaining and needs_detail(listing):
            detail, resolved_url, error = await _load_detail(context, card['href'])
            remaining -= 1
            if not error:
                listing = build_listing(card, detail, aliases, resolved_url, expected_grade=grade, expected_edition=edition)
            await asyncio.sleep(random.uniform(1.0, 2.2))
        if listing:
            results.append(listing)
    return ListingBatch(results, search_errors)


def needs_detail(listing: dict[str, Any] | None) -> bool:
    return bool(listing and (listing['eligible'] or listing['condition_status'] in {'成色待确认', '系列待确认', '版本待确认'}))


def search_queries(item: dict[str, Any]) -> list[str]:
    """At most three normal searches; aliases are alternatives, not AND terms."""
    aliases, grade = watch_requirements(item)
    queries = [item['search_query']]
    for alias in aliases:
        if re.search(r'[\u4e00-\u9fff]', alias) and len(alias) <= 12:
            candidate = f'{grade} {alias}'
            if normalize_text(candidate) not in {normalize_text(q) for q in queries}:
                queries.append(candidate)
        if len(queries) == 3:
            break
    return queries


async def _search_cards(context: BrowserContext, query: str, max_results: int) -> list[dict[str, Any]]:
    page = await context.new_page()
    try:
        await page.bring_to_front()
        try:
            await page.goto(
                search_url(query), wait_until="domcontentloaded", timeout=60_000
            )
        except Exception as exc:  # noqa: BLE001
            raise NetworkError(f"打开搜索页失败：{exc}", evidence=f"query={query}") from exc

        await page.wait_for_timeout(3500)
        for _ in range(3):
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(650)
        try:
            await page.wait_for_function(
                r"""
                () => [...document.querySelectorAll('a[href]')].some(a =>
                  /\/item|item\?|item\//i.test(a.getAttribute('href') || '') &&
                  /[¥￥]\s*\d/.test(a.innerText || '')
                )
                """,
                timeout=15_000,
            )
        except Exception:
            # 下面会给出更具体的失败原因（登录/验证/空结果/DOM）。
            pass

        authenticated = await _has_authenticated_session(context)

        # 先取卡片，再判失败。顺序很重要：只要页面上真有卡片，就不该报登录墙。
        cards = await _extract_cards(page)
        # Some search pages initially contain only a loading skeleton. Poll the
        # DOM without refreshing or repeating network requests.
        for _ in range(10):
            if any(_card_price(card) is not None for card in cards):
                break
            diagnostics = await _read_search_diagnostics(page)
            failure = _classify_search_failure(diagnostics, authenticated, query)
            if isinstance(failure, (IllegalAccessError, VerificationRequiredError)) or _first_marker(diagnostics['body'], LOGIN_MARKERS):
                raise failure
            await page.wait_for_timeout(1500)
            cards = await _extract_cards(page)
        if not cards:
            diagnostics = await _read_search_diagnostics(page)
            raise _classify_search_failure(diagnostics, authenticated, query)

        unique_cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            href = card.get("href", "")
            key = _item_key(href)
            if key in seen or _card_price(card) is None:
                continue
            seen.add(key)
            unique_cards.append(card)

        if not unique_cards:
            raise SearchParseError(
                "找到了商品链接，但卡片上没有可解析的价格（价格可能还没加载完）。"
            )

        return unique_cards
    finally:
        await page.close()
