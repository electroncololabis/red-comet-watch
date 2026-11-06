"""搜索卡片解析的回归测试。

全部输入都来自 **2026-10-01 真实抓到的页面**
（_diag/probe_20261001-210236/body-search.txt 与 page-search.html），
不是编的例子。

这一天最重要的发现：**搜索本来就是好的**。30 张卡片、价格、平台成色角标
全都在，选择器没坏 —— 之前的失败是登录/风控，不是 DOM 改版。
（依据：probe 截图 + 图片通道核对 + 保存的 HTML。）
"""

from __future__ import annotations

from hguc_watch.filtering import parse_price, parse_shipping
from hguc_watch.xianyu import build_listing

DIJEH = ["迪杰", "ディジェ", "DIJEH", "MSK-008", "HGUC 219"]


# ---------------------------------------------------------------------------
# 价格：整数和小数在**两个** span / 两行里
# ---------------------------------------------------------------------------


def test_split_decimal_price_is_joined() -> None:
    """真实卡片：<span>¥</span><span>179</span><span>.89</span>
    对应 innerText 是 "¥\\n179\\n.89\\n1人想要"。"""
    assert parse_price("¥\n179\n.89\n1人想要") == 179.89


def test_split_decimal_price_with_other_lines() -> None:
    assert parse_price("全新未拼装\nBandai/万代\n¥\n207\n.86\n2人想要\n广东") == 207.86


def test_integer_price_is_not_merged_with_the_next_line() -> None:
    """★ 反例：不能把整段空白删掉，否则 ¥10 + "1人想要" 会被读成 101。"""
    assert parse_price("¥\n10\n1人想要\n重庆") == 10.0
    assert parse_price("¥\n5\n2人想要\n广东") == 5.0
    assert parse_price("¥\n168\n1人想要\n北京") == 168.0


def test_price_with_commas() -> None:
    assert parse_price("¥1,299") == 1299.0


def test_price_in_yuan_suffix_still_works() -> None:
    assert parse_price("运费：¥12.5") == 12.5


# ---------------------------------------------------------------------------
# 平台角标：包邮是**图片**，image id 已用图像通道核对
# ---------------------------------------------------------------------------

# 2026-10-01 实测：30 张卡片里 20 张带这张图
# https://gw.alicdn.com/imgextra/i1/O1CN017BILHl1VNARooBnhe_!!6000000002640-2-tps-84-60.png
# 图像通道读出内容 = 黑体"包邮"。
FREE_SHIPPING_CARD = {
    "href": "https://www.goofish.com/item?id=1",
    "title": "万代 HGUC 1/144 迪杰 MSK-008 全新未拼 太仓可自提",
    "price": 139.0,
    "free_shipping": True,
    "chips": ["全新未拼装", "Bandai/万代", "1/144"],
    "text": "万代 HGUC 1/144 迪杰 MSK-008 全新未拼 太仓可自提\n全新未拼装\nBandai/万代\n1/144\n¥\n139",
    "image": "",
}

# 同一批里没有包邮角标、且写着"邮费自理"的那张
NOT_FREE_SHIPPING_CARD = {
    "href": "https://www.goofish.com/item?id=2",
    "title": "万代 HGUC 1/144 迪杰 MSK-008 全新未拼 邮费自理 太仓可自提",
    "price": 139.0,
    "free_shipping": False,
    "chips": ["全新未拼装", "Bandai/万代", "1/144"],
    "text": "万代 HGUC 1/144 迪杰 MSK-008 全新未拼 邮费自理 太仓可自提\n全新未拼装\n¥\n139",
    "image": "",
}


def test_free_shipping_badge_wins() -> None:
    listing = build_listing(FREE_SHIPPING_CARD, "", DIJEH)
    assert listing is not None
    # 状态单列成 free_badge：能区分"平台角标说包邮"和"卖家文案说包邮"
    assert listing["shipping_status"] == "free_badge"
    assert listing["shipping_price"] == 0.0
    assert listing["total_price"] == 139.0


def test_text_only_free_shipping_stays_plain_free() -> None:
    card = dict(FREE_SHIPPING_CARD)
    card["free_shipping"] = False
    card["text"] = "万代 HGUC 219 迪杰 全新未拼装 申通包邮\n全新未拼装\n¥\n168"
    listing = build_listing(card, "", DIJEH)
    assert listing is not None
    assert listing["shipping_status"] == "free"


def test_platform_badge_overrides_conditional_seller_wording() -> None:
    """包邮角标示例：卖家写"粉丝快递包邮"，但平台打了包邮角标。

    已用 HTML + 截图核对过：这张卡片上确实有包邮角标图
    （img id O1CN017BILHl1VNARooBnhe，图像通道读出图案是"包邮"）。
    平台角标按商品设置算给买家，比卖家营销文案可信 —— 按 0 元算，
    但状态记成 free_badge 以便审计。
    """
    card = {
        "href": "https://www.goofish.com/item?id=42",
        "title": (
            "万代HGUC 219高达Z HG迪杰高达阿姆罗机再版蓝标拼装 模型 全新未拆 八角尖尖 好盒发出！ "
            "粉丝快递包邮！现货可批发！ 打包有泡沫和硬纸箱！ 不包运输盒损！发货不退不换 ！"
        ),
        "price": 220.0,
        "free_shipping": True,
        "chips": ["全新未拼装", "Bandai/万代"],
        "text": (
            "万代HGUC 219高达Z HG迪杰高达阿姆罗机再版蓝标拼装 模型 全新未拆 八角尖尖 好盒发出！ "
            "粉丝快递包邮！现货可批发！\n全新未拼装\nBandai/万代\n¥\n220"
        ),
        "image": "",
    }
    listing = build_listing(card, "", DIJEH)
    assert listing is not None
    assert listing["shipping_status"] == "free_badge"
    assert listing["total_price"] == 220.0
    # 而**只看文案**时它是条件包邮，绝不能算 0
    assert parse_shipping(card["title"]) == (None, "conditional")


def test_without_badge_the_text_decides() -> None:
    listing = build_listing(NOT_FREE_SHIPPING_CARD, "", DIJEH)
    assert listing is not None
    assert listing["shipping_status"] == "unknown"
    assert listing["shipping_price"] is None
    assert listing["total_price"] is None


def test_seller_explicit_paid_shipping_prevents_false_free_total():
    card = dict(NOT_FREE_SHIPPING_CARD, free_shipping=True)
    listing = build_listing(card,'',DIJEH)
    assert listing['total_price'] is None
    card['title'] = '万代 HGUC 迪杰 全新未拼 不包邮 运费10元'
    card['text'] = card['title']
    assert build_listing(card,'',DIJEH)['total_price'] == 149


# ---------------------------------------------------------------------------
# 用结构化价格字段，而不是正则碰运气
# ---------------------------------------------------------------------------


def test_structured_price_field_is_used() -> None:
    card = dict(FREE_SHIPPING_CARD)
    card["text"] = "万代 HGUC 219 迪杰 全新未拼装 包邮 ¥ 999"  # 文本里的价格是干扰项
    card["price"] = 179.89
    listing = build_listing(card, "", DIJEH)
    assert listing is not None
    assert listing["item_price"] == 179.89


def test_price_falls_back_to_regex_when_structure_missing() -> None:
    card = dict(FREE_SHIPPING_CARD)
    card["price"] = None
    card["text"] = "万代 HGUC 219 迪杰 全新未拼装\n¥\n179\n.89\n1人想要"
    listing = build_listing(card, "", DIJEH)
    assert listing is not None
    assert listing["item_price"] == 179.89


# ---------------------------------------------------------------------------
# 平台成色角标（文字）参与判定
# ---------------------------------------------------------------------------


def test_platform_chip_已拼装_is_rejected() -> None:
    """真实卡片 4：<span class="cpv--…">已拼装</span>。"""
    card = {
        "href": "https://www.goofish.com/item?id=3",
        "title": "【清柜回血】万代HGUC 1/144 迪杰 Z高达版！已拼装 补色渗线消光",
        "price": 108.0,
        "free_shipping": True,
        "chips": ["已拼装", "Bandai/万代", "1/144"],
        "text": "【清柜回血】万代HGUC 1/144 迪杰 Z高达版！已拼装\n已拼装\n¥\n108",
        "image": "",
    }
    listing = build_listing(card, "", DIJEH)
    assert listing is not None
    assert listing["eligible"] is False
    assert listing["condition_status"] == "已拼或加工"


def test_platform_chip_全新未拼装_is_accepted() -> None:
    """真实卡片：全新未拼装 角标 + 平台包邮角标 -> 入选。"""
    listing = build_listing(FREE_SHIPPING_CARD, "", DIJEH)
    assert listing is not None
    assert listing["eligible"] is True
    assert listing["condition_status"] == "全新未拼"


def test_platform_chip_1_100_scale_is_rejected() -> None:
    """真实卡片 20：cpv 角标里是 1/100，标题写 捷达 阿姆罗专用型。"""
    card = {
        "href": "https://www.goofish.com/item?id=4",
        "title": "包邮 胶佬星人 捷达 阿姆罗专用型 1/100拼装模型 全新未拆封，带原包装盒",
        "price": 138.0,
        "free_shipping": True,
        "chips": ["全新未拼装", "全新无瑕疵", "1/100"],
        "text": "包邮 胶佬星人 捷达 阿姆罗专用型 1/100拼装模型 全新未拆封，带原包装盒",
        "image": "",
    }
    listing = build_listing(card, "", DIJEH)
    assert listing is not None
    assert listing["eligible"] is False


# ---------------------------------------------------------------------------
# 证据留存
# ---------------------------------------------------------------------------


def test_source_text_and_rule_version_are_recorded() -> None:
    listing = build_listing(FREE_SHIPPING_CARD, "详情页正文", DIJEH)
    assert listing is not None
    assert listing["rule_version"] >= 2
    assert "全新未拼装" in listing["source_text"]
    assert "详情页正文" in listing["source_text"]


def test_card_without_price_returns_none() -> None:
    card = dict(FREE_SHIPPING_CARD)
    card["price"] = None
    card["text"] = "万代 HGUC 219 迪杰 全新未拼装 价格面议"
    assert build_listing(card, "", DIJEH) is None


def test_source_text_contains_the_title_only_once() -> None:
    """source_text 里标题只能出现一次 —— 这是上面两条规则的共同前提。"""
    from hguc_watch.filtering import normalize_text

    listing = build_listing(FREE_SHIPPING_CARD, "", DIJEH)
    assert listing is not None
    compact = normalize_text(listing["source_text"])
    title = normalize_text(FREE_SHIPPING_CARD["title"])
    assert compact.count(title) == 1
    # 标题后面的平台角标和正文要保留下来
    assert "全新未拼装" in listing["source_text"]
    assert "1/144" in listing["source_text"]


def test_multi_item_count_does_not_double_count_the_title() -> None:
    """标题里有 2 个 HGUC 的真实卡片刻意不会因为标题重复被数成 4 个。"""
    card = {
        "href": "https://www.goofish.com/item?id=6",
        "title": "万代 HGUC 219 迪杰 MSK-008 全新未拼装 HGUC 迪杰 包邮",
        "price": 168.0,
        "free_shipping": True,
        "chips": ["全新未拼装", "Bandai/万代", "1/144"],
        "text": "万代 HGUC 219 迪杰 MSK-008 全新未拼装 HGUC 迪杰 包邮\n全新未拼装\nBandai/万代\n1/144\n¥\n168",
        "image": "",
    }
    listing = build_listing(card, "", DIJEH)
    assert listing is not None
    assert listing["condition_status"] != "多件混列"


def test_repeated_title_in_card_body_does_not_look_like_a_daigou_template() -> None:
    """★ 回归：闲鱼卡片的 innerText 第一行**就是标题**。

    所以 source_text = 完整标题 + 卡片正文 + 详情正文 时，标题天然出现两次。
    "标题整段重复"这条规则如果拿整段 text 去判，会把**每一条**商品都误杀成
    代购/占位模板。规则必须只看 title 本身。
    """
    listing = build_listing(FREE_SHIPPING_CARD, "详情页正文", DIJEH)
    assert listing is not None
    assert listing["eligible"] is True
    assert listing["condition_status"] != "代购/占位模板"


def test_real_daigou_title_is_still_rejected() -> None:
    """反例：真正的代购模板帖，标题本身整段重复，必须照样判掉。

    真实 listing 81 / 254。
    """
    daigou_title = (
        "【内袋未开封】1/144 HGUC PMX-0 日本代购 【内袋未开封】1/144 HGUC PMX-001 "
        "帕拉斯·雅典娜《机动战士Z高达》[0141425] Gunpla（高达模型）"
    )
    card = {
        "href": "https://www.goofish.com/item?id=5",
        "title": daigou_title,
        "price": 59.0,
        "free_shipping": False,
        "chips": [],
        "text": daigou_title + "\n全新未拼装\n¥\n59",
        "image": "",
    }
    listing = build_listing(card, "", ["帕拉斯雅典娜", "PMX-001", "HGUC 060"])
    assert listing is not None
    assert listing["eligible"] is False
    assert listing["condition_status"] == "代购/占位模板"


def test_resolved_url_is_preferred_over_card_href() -> None:
    listing = build_listing(
        FREE_SHIPPING_CARD,
        "",
        DIJEH,
        resolved_url="https://www.goofish.com/item?id=999&categoryId=1",
    )
    assert listing is not None
    assert listing["detail_url"].endswith("id=999&categoryId=1")
    assert listing["item_key"] == "999"


# ---------------------------------------------------------------------------
# 真实页面里"看起来像包邮其实不是"的写法
# ---------------------------------------------------------------------------


def test_real_card_15_says_baopuyou_not_baoyou() -> None:
    """真实卡片 15（listing 241）：「包普邮，主页商品两个及以上包京东」。

    "包普邮"是**无条件**的普通快递包邮，按 0 元算；
    "主页两个及以上包京东"是**另外一档**的条件优惠，不影响普邮那一档。
    """
    text = (
        "万代 PB限定 HGUC NT迪杰 MSK-008 迪杰(Narrative Ver.) 全新未拆，默认盒损，情况如图。"
        "包普邮，主页商品两个及以上包京东，商品售出不退不换"
    )
    assert parse_shipping(text) == (0.0, "free")


def test_real_card_246_fans_free_shipping_is_conditional() -> None:
    """真实卡片 29（listing 246）：「粉丝快递包邮」不是无条件包邮。"""
    text = "万代HGUC 219高达Z HG迪杰高达阿姆罗机再版蓝标拼装 模型 全新未拆 粉丝快递包邮！现货可批发！"
    assert parse_shipping(text) == (None, "conditional")


def test_real_card_says_postage_self_pay() -> None:
    """真实卡片 5：「邮费自理」。"""
    assert parse_shipping("万代 HGUC 1/144 迪杰 MSK-008 全新未拼 邮费自理 太仓可自提") == (
        None,
        "unknown",
    )
