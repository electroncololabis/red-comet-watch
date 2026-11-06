"""识别商品的型号、版本、成色和运费。

运费未知与条件包邮分别标记。筛选规则版本会随报价保存，方便区分历史数据。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# 每次改动判定规则就 +1：库里用这一列区分"新规则的数据"和"旧规则的历史数据"。
FILTER_RULE_VERSION = 6


# --------------------------------------------------------------------------
# 词表
# --------------------------------------------------------------------------

PREORDER_TERMS = (
    "预售",
    "預售",
    "预定",
    "預定",
    "预订",
    "預訂",
    "预约",
    "預約",
    "订金",
    "訂金",
    "定金",
    "补款",
    "補款",
    "尾款",
    "到货通知",
    "接单制作",
    "排单",
    "制作周期",
)

# 「明确不完整」的强信号：命中即判非完整模型，不需要再看别的。
# 注意这里**故意不放** 盒子 / 说明书 / 配件 / 零件 —— 它们单独出现时
# 常常是"配件齐全""盒子说明都好"这类完整的正面描述（真实误杀 #235 #244 #245）。
HARD_INCOMPLETE_TERMS = (
    "空盒",
    "只有包装盒",
    "只有盒子",
    "只有个盒子",
    "仅有包装盒",
    "仅有盒子",
    "仅包装盒",
    "仅盒子",
    "只有包装",
    "无模型",
    "没有模型",
    "不含模型",
    "非整盒",
    "非完整模型",
    "整盒勿拍",
    "补件",
    "補件",
    "散件",
    "拆卖",
    "拆賣",
    "拆出",
    "拆售",
    "分尸",
    "分屍",
    "改件",
    "翻模件",
    "树脂件",
    "板件散",
    "单出零件",
    "单出配件",
    "单卖零件",
    "单卖配件",
    "零件拆卖",
    "散货",
    "散貨",
    "缺件",
    "无主体",
    "武器包",
    "配件包",
    "装备包",
    "特效件",
)

# 弱信号：卖出水贴/贴纸本身，但完整套装也常"送水贴"。命中后还要看有没有
# 赠送类前缀（送/赠/含/带/附），有则不判非完整。
SOFT_INCOMPLETE_TERMS = ("水贴", "水貼", "贴纸", "貼紙", "合金骨架", "金属骨架", "LED灯组", "LED灯")
BONUS_PREFIXES = ("送", "赠", "贈", "赠品", "附赠", "另送", "加送", "含", "带", "帶", "有")

BUILT_TERMS = (
    "已拼",
    "素组",
    "素組",
    "拼装完成",
    "拼裝完成",
    "成品",
    "代工",
    "喷涂",
    "噴塗",
    "涂装",
    "塗裝",
    "改造",
    "尸体",
    "屍體",
    "补色",
    "補色",
    "渗线",
    "滲線",
    "消光",
    "笔涂",
    "筆塗",
    "喷漆",
)

NEW_UNBUILT_TERMS = (
    "全新",
    "未拼",
    "未组",
    "未組",
    "未拆",
    "未拆封",
    "未开封",
    "未開封",
    "未使用",
    "未把玩",
    "袋未开",
    "袋未開",
    "板未拆",
    "板件未拆",
    "塑封未拆",
    "未組裝",
    "未组装",
)

# 求购（买家收货帖）。故意不放单字"求""收"：真实文本里
# "要求高的自行补钱" / "签收" / "到手刀" 都会误命中。
WANTED_TERMS = (
    "求购",
    "求購",
    "收购",
    "收購",
    "回收",
    "诚收",
    "誠收",
    "高价收",
    "高價收",
    "长期收",
    "長期收",
    "无限收",
    "蹲一个",
    "蹲个",
    "求个",
    "求一",
    "慢收",
    "收HGUC",
    "收万代",
    "收一台",
    "收一个",
)

# 变体 / 限定：普通版比价必须把它们排除，否则 PB/NT/彩透 会顶掉普通版最低价。
VARIANT_TERMS = (
    "GK样式",
    "GK改造",
    "PB限定",
    "PB限定版",
    "网限",
    "網限",
    "网络限定",
    "網絡限定",
    "限定版",
    "会场限定",
    "會場限定",
    "基地限定",
    "限定",
    "NT版",
    "NT.ver",
    "NT ver",
    "NARRATIVE",
    "ナラティブ",
    "彩透",
    "彩色透明",
    "透明",
    "电镀",
    "電鍍",
    "メッキ",
    "PLATED",
    "钛合金",
    "チタニウム",
    "TITANIUM",
    "クリア",
    "CLEAR",
)

# 变体词里需要单独加边界的：NT 必须整词，避免命中 "NTT"/"NT-1" 之外的无关串；
# 透明后面如果是"袋"，说的是包装袋是透明的（AGENTS 里点名过的误判）。
VARIANT_PATTERNS = (
    (r"(?<![A-Z0-9])NT(?![A-Z0-9])", "NT"),
    (r"透明(?!袋|膠袋|胶袋|自封|包装袋|OPP)", "透明"),
    (r"(?<![A-Z])CLEAR(?![A-Z])", "CLEAR"),
    (r"限定", "限定"),
)

# 不是"HGUC 拼装整盒"的品类 / 产品线。真实误判来源：listing #24/#153/#326
# 标题是「FIX GFFN 0041 高达ZII Z2 Katoki 非ROBOT魂 Ka魂 MR魂 HG HGUC MG RG …」
# —— 一条关键词堆砌的**成品**帖，标价 ¥695，却因为在标题里写了「HGUC」而被选进比价。
NOT_A_KIT_TERMS = (
    "扭蛋",
    "食玩",
    "盒蛋",
    "景品",
    "手办",
    "雕像",
    "胸像",
    "拼图",
    "完成品",
    "已涂装",
    "GFFN",
    "GFF",
    "FIX FIGURATION",
    "ROBOT魂",
    "METAL ROBOT",
    "超合金",
    "フィギュア",
)

# 数字比例：在**去空白**的文本上匹配，因为闲鱼标题常写成 "1/14 4"、"1/14\n4"。
OTHER_SCALE_NUMERIC_PATTERNS = (
    (r"1\s*/\s*100", "1/100"),
    (r"1\s*/\s*60", "1/60"),
    (r"1\s*/\s*48", "1/48"),
    (r"1\s*/\s*550", "1/550"),
    (r"1\s*/\s*2200", "1/2200"),
    (r"RE\s*/\s*100", "RE/100"),
)

# 系列缩写：必须按**词**匹配，所以在保留空白的文本上跑。
# 这里踩过一个坑：在去空白的文本上跑 (?<![A-Z])MG(?![A-Z])，
# "…HGUC MG RG…" 会变成 "HGUCMGRG"，MG 后面跟的是 R，前瞻失败 → 漏判。
OTHER_SCALE_TOKEN_PATTERNS = (
    (r"(?<![A-Z0-9])MG(?![A-Z0-9])", "MG"),
    (r"(?<![A-Z0-9])RG(?![A-Z0-9])", "RG"),
    (r"(?<![A-Z0-9])PG(?![A-Z0-9])", "PG"),
    (r"(?<![A-Z0-9])SD(?![A-Z0-9])", "SD"),
    (r"HI-?RES", "HI-RES"),
    (r"FULL\s*MECHANICS", "FULL MECHANICS"),
)

# 第三方品牌即使使用相同型号名称，也不参与万代模型比价。
NON_BANDAI_BRAND_TERMS = (
    "XW模型",
    "XW",
    "大班",
    "龙桃子",
    "龍桃子",
    "桃子",
    "高高",
    "慧眼",
    "精乐",
    "模匠魂",
    "无限新星",
    "摩动核",
    "星影",
    "DABAN",
    "SUPERNOVA",
    "超新星",
)

BANDAI_TERMS = ("万代", "萬代", "BANDAI", "バンダイ")

# 标题里有一整段（>= 14 字）原样重复 —— 闲鱼上典型的**代购/占位模板**帖，
# 标价往往是定金或占位价，不是现货总价。
# 真实来源：#81/#254「【内袋未开封】1/144 HGUC PMX-0 日本代购 【内袋未开封】1/144 HGUC PMX-001 …」¥59、
#           #303「全新未拆封 HG 1/144 梅萨拉 & MS 日本代购 全新未拆封 HG 1/144 梅萨拉 & MS-05 扎克I 万雷版双套装」¥70。
DUPLICATE_BLOCK_MIN = 14


def has_duplicated_block(compact: str, min_len: int = DUPLICATE_BLOCK_MIN) -> str:
    """标题里是否有一段 >= min_len 的字原样出现了两次。返回那段文字，没有则返回 ""。"""
    seen: set[str] = set()
    for index in range(len(compact) - min_len + 1):
        block = compact[index : index + min_len]
        if block in seen:
            return block
        seen.add(block)
    return ""

# 条件包邮：这些情况下的"包邮"不是无条件 0 元运费。
CONDITIONAL_FREE_PATTERNS = (
    (r"粉丝.{0,4}包邮", "粉丝包邮"),
    (r"關注.{0,4}包邮", "关注包邮"),
    (r"关注.{0,4}包邮", "关注包邮"),
    (r"满[0-9一二三四五六七八九十百千两]+(?:件|款|个|元|块|商品|起)", "满件/满额包邮"),
    (r"任意[0-9两]+[件款个]", "任意 N 件包邮"),
    (r"[0-9两]+[件款个].{0,6}(?:包邮|免邮|优惠)", "满件包邮"),
    (r"同时买.{0,16}包邮", "同时买 N 件包邮"),
    (r"主页.{0,20}包邮", "主页满件包邮"),
    (r"淘宝.{0,6}下单.{0,6}包邮", "淘宝下单包邮"),
    (r"直播间.{0,6}包邮", "直播间包邮"),
    (r"打包.{0,4}优惠", "打包优惠"),
    (r"多件.{0,6}包邮", "多件包邮"),
    (r"凑单.{0,6}包邮", "凑单包邮"),
)

# 明确"不包邮/运费自理"：直接按未知运费处理，不能算 0。
NOT_FREE_PATTERNS = (
    (r"不包邮", "不包邮"),
    (r"不包郵", "不包邮"),
    (r"不含运费", "不含运费"),
    (r"不含郵費", "不含运费"),
    (r"运费到付", "运费到付"),
    (r"運費到付", "运费到付"),
    (r"邮费到付", "邮费到付"),
    (r"郵費到付", "邮费到付"),
    (r"运费实收", "运费实收"),
    (r"运费实付", "运费实付"),
    (r"運費實收", "运费实收"),
    (r"邮费实收", "邮费实收"),
    (r"邮费自理", "邮费自理"),
    (r"运费自理", "运费自理"),
    (r"运费另计", "运费另计"),
    (r"邮费另计", "邮费另计"),
    (r"顺丰到付", "顺丰到付"),
    (r"快递到付", "快递到付"),
)

FREE_PATTERNS = (
    (r"非偏远.{0,8}包邮", "non_remote_free"),
    (r"非偏遠.{0,8}包郵", "non_remote_free"),
    (r"包邮", "free"),
    (r"包郵", "free"),
    # "包普邮 / 包普快 / 包快递" 都是无条件包邮，字面上不含"包邮"两个字，
    # 2026-10-01 真实卡片 15 就是这么写的（「包普邮，主页商品两个及以上包京东」）。
    (r"包普邮", "free"),
    (r"包普快", "free"),
    (r"包快递", "free"),
    (r"免运费", "free"),
    (r"免運費", "free"),
    (r"运费0(?:\.0+)?元", "free"),
    (r"運費0(?:\.0+)?元", "free"),
)

# 一条帖子里挂多个不同商品（"杂物贴"）：HGUC 出现 3 次以上基本可以判定。
MULTI_ITEM_MIN_HGUC = 3

_NEGATION_SUFFIXES = ("并非", "並非", "不是", "没有", "沒有", "不会", "不會", "不属于")
_NEGATION_CHARS = "非不无無没沒"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def normalize_text(value: str) -> str:
    """NFKC + 去掉所有空白 + 转大写。用于中文子串匹配。"""
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", "", value).upper()


def spaced_text(value: str) -> str:
    """NFKC + 把连续空白压成一个空格 + 转大写。

    用于**按词**匹配的拉丁缩写（MG / RG / PG / SD）。
    不能先去掉空白：闲鱼标题常写 "HGUC MG RG"，去空白后变成 "HGUCMGRG"，
    缩写之间会互相吃掉边界，前瞻判断失效。
    """
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", value).strip().upper()


def _is_negated(compact: str, index: int) -> bool:
    """判断 compact[index:] 处命中的词是不是被前面的否定词否掉了。

    "非全新" -> 全新被否定；"全新未拆" -> 未拆没有被否定；
    "非偏远包邮" -> 包邮前面的字是"远"，不算否定。
    """
    prefix = compact[max(0, index - 3) : index]
    if not prefix:
        return False
    for suffix in _NEGATION_SUFFIXES:
        if prefix.endswith(suffix):
            return True
    return prefix[-1] in _NEGATION_CHARS


def _find_term(compact: str, terms: tuple[str, ...]) -> str | None:
    """返回第一个**未被否定**的命中词，找不到返回 None。"""
    for term in terms:
        needle = normalize_text(term)
        start = 0
        while True:
            index = compact.find(needle, start)
            if index < 0:
                break
            if not _is_negated(compact, index):
                return term
            start = index + 1
    return None


def _find_pattern(compact: str, patterns) -> tuple[str, str] | None:
    """返回第一个未被否定的 (匹配文本, 标签)。patterns 是 (regex, label) 序列。"""
    for pattern, label in patterns:
        for match in re.finditer(pattern, compact, re.IGNORECASE):
            if not _is_negated(compact, match.start()):
                return match.group(0), label
    return None


def _has_bonus_prefix(compact: str, index: int) -> bool:
    """水贴/贴纸前面 1-2 个字是不是"送/含/带/附"这类赠送语。"""
    prefix = compact[max(0, index - 2) : index]
    return any(prefix.endswith(bonus) for bonus in BONUS_PREFIXES)


# --------------------------------------------------------------------------
# 判定
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    eligible: bool
    condition_status: str
    reason: str = ""
    # "plain" = 普通版；"variant" = 限定/变体；"unknown" = 还没判到这一步
    version: str = "unknown"
    version_evidence: str = ""


def _detect_version(compact: str) -> tuple[str, str]:
    """先看正则词（有边界要求），再看普通词表。返回 (匹配文本, 标签) 或 ("", "")。"""
    hit = _find_pattern(compact, VARIANT_PATTERNS)
    if hit:
        return hit[1], hit[0]
    term = _find_term(compact, VARIANT_TERMS)
    if term:
        return normalize_text(term), term
    return "", ""


_GRADE_PATTERN = re.compile(
    r"(?<![A-Z])(?:HGUC|HGBF|HGBD|HGCE|HGAC|MGEX|MGSD|MG|RG|PG|RE(?:/100)?|HG|FG|TV|SD)(?![A-Z])"
)


def listing_grades(text: str) -> list[str]:
    text = spaced_text(text)
    return [m.group().replace('/100', '') for m in _GRADE_PATTERN.finditer(text)
            if not _is_negated(text, m.start())]


def watch_requirements(item: dict) -> tuple[list[str], str]:
    """Follow the user's watch entry; a newly added MG/RE is not an HGUC."""
    aliases = item.get('aliases') or [item['name']]
    if not item.get('aliases') and item['name'].endswith('高达') and len(item['name']) > 3:
        aliases = [*aliases, item['name'][:-2]]
    grades = listing_grades(item.get('model_code', '')) or listing_grades(item.get('search_query', ''))
    return aliases, grades[0] if grades else 'HG'


def watch_edition(item: dict) -> str:
    return 'ZZ' if re.search(r'(?<![A-Z])ZZ(?![A-Z])', spaced_text(item.get('model_code', ''))) else ''


def classify_listing(
    text: str,
    aliases: list[str],
    *,
    title: str | None = None,
    expected_grade: str = "HGUC",
    expected_edition: str = "",
    retail: bool = False,
) -> Classification:
    """判定一条闲鱼条目是否属于"全新未拼的普通版整盒"。

    `text` 应当只是**这一件商品**的标题/描述，不要把整页详情
    （含"猜你喜欢"推荐位）拼进来 —— 推荐位会污染型号、成色和包邮判定。

    `title` 只在需要判断"标题是不是整段重复"时用（代购/占位模板帖）。

    ⚠ 为什么重复检测只看 title：
        生产环境里 `text` 是「完整标题 + 卡片正文 + 详情正文」拼起来的，
        而**卡片正文本身就以标题开头**（闲鱼卡片的 innerText 第一行就是标题）。
        如果拿整段 text 去找重复块，每一条商品都会被判成"标题重复"。
        这个坑是 tests/test_card_parsing.py 里的真实卡片 fixture 抓出来的。
        不传 `title` 时退回用 `text`（适合直接拿标题当 text 的重判场景）。
    """
    compact = normalize_text(text)
    spaced = spaced_text(text)
    old_hg = _find_pattern(compact, (
        (r"(?:旧|舊|老)(?:版|款|模)?HG(?:UC|CE|AC|AW|BF|BD)?(?![A-Z])", "旧 HG"),
        (r"HG(?:旧|舊|老)(?:版|款|模)", "旧 HG"),
    ))
    if old_hg:
        return Classification(False, "旧HG已屏蔽", f"命中屏蔽规则：{old_hg[0]}")
    normalized_aliases = [normalize_text(alias) for alias in aliases if alias.strip()]

    if normalized_aliases and not any(alias.replace('·', '').replace('・', '') in compact.replace('·', '').replace('・', '') for alias in normalized_aliases):
        return Classification(False, "型号不符", "标题和描述未命中型号别名")

    # The base Leopard and Leopard Destroy are separate kits. A shortened
    # fallback alias must not admit Destroy just because it contains Leopard.
    if any('斑豹' in alias for alias in normalized_aliases) and not any(
        any(term in alias for term in ('毁灭', '毀滅', 'DESTROY')) for alias in normalized_aliases
    ):
        if _find_term(normalize_text(title or text), ('斑豹毁灭者', '斑豹毀滅者')):
            return Classification(False, "型号不符", "关注的是斑豹高达，商品标题明确为斑豹毁灭者")

    if re.search(r'^(?:【|\[|已售\s*)?(?:已出|已售|售罄|已卖出)', normalize_text(title or text)):
        return Classification(False, "已售出", "卖家标题明确标注已售出")
    if re.search(r'出[两二2]盒.{0,20}一盒.{0,20}一盒', normalize_text(title or text)):
        return Classification(False, "多件价格待确认", "不同成色的多盒商品共用标价，需确认具体单盒价格")

    if _find_term(compact, WANTED_TERMS) or _find_pattern(
        compact, ((r"(?<![到款货集藏纳入起费据])收(?:HGUC|万代|一[台个盒]|[0-9]+)", "求购"),)
    ):
        return Classification(False, "求购帖", "这是买家求购帖，不是在售商品")

    preorder = _find_term(compact, PREORDER_TERMS)
    if preorder:
        return Classification(False, "预售/预定", f"包含排除词：{preorder}")

    # "发现断件或缺件联系我有售后" describes a hypothetical after-sales
    # policy, not the current condition of the kit.
    incomplete_text = re.sub(r"(?:发现|發現|如有|若有|如果).{0,12}(?:缺件|断件).{0,12}(?:联系|聯繫|售后|售後|补发)", "", compact)
    incomplete = _find_term(incomplete_text, HARD_INCOMPLETE_TERMS)
    if incomplete:
        return Classification(False, "非完整模型", f"包含明确不完整语义：{incomplete}")

    for term in SOFT_INCOMPLETE_TERMS:
        needle = normalize_text(term)
        start = 0
        while True:
            index = compact.find(needle, start)
            if index < 0:
                break
            if not _is_negated(compact, index) and not _has_bonus_prefix(compact, index):
                return Classification(False, "非完整模型", f"单独在卖{term}，不是整盒模型")
            start = index + 1

    built = _find_term(compact, BUILT_TERMS)
    if built:
        return Classification(False, "已拼或加工", f"包含排除词：{built}")

    # The title, description and platform's "series: HGUC" repeat the SAME kit.
    # Only distinct product identifiers establish a multi-item listing.
    identifiers = set(re.findall(
        r"HGUC\s*([0-9]{1,3}(?![0-9])|[A-Z]{1,4}-?[0-9]{2,4})", spaced
    ))
    if len(identifiers) >= MULTI_ITEM_MIN_HGUC:
        return Classification(
            False, "多件混列", "一条帖子包含多个不同 HGUC 型号，单件价格不明确"
        )

    duplicate = has_duplicated_block(normalize_text(title) if title is not None else compact)
    if duplicate and _find_term(compact, ("日本代购", "占位", "占坑")):
        return Classification(
            False, "代购/占位模板", f"标题有一整段重复（{duplicate}），是代购/占位模板帖"
        )

    not_a_kit = _find_term(compact, NOT_A_KIT_TERMS)
    if not_a_kit:
        return Classification(False, "型号不符", f"不是 HGUC 拼装整盒：{not_a_kit}")

    # Prefer the seller's product title over potentially mislabelled platform chips.
    grades = listing_grades(title or text) or listing_grades(text)
    compatible = {'HG', 'HGUC', 'HGBF', 'HGBD', 'HGCE', 'HGAC'} if expected_grade in {'HG', 'HGUC'} else {expected_grade}
    if grades and grades[0] not in compatible:
        return Classification(False, "型号不符", f"关注的是 {expected_grade}，商品标注为 {grades[0]}")
    if not grades and expected_grade not in {'HG', 'HGUC'}:
        return Classification(False, "系列待确认", f"未找到 {expected_grade} 系列的明确描述")
    if expected_edition == 'ZZ':
        edition_text = normalize_text(title or text)
        if 'ZZ' not in edition_text:
            if '独角兽' in edition_text or 'UNICORN' in edition_text or 'UC版' in edition_text:
                return Classification(False, "版本不符", "关注的是 ZZ 版，商品标注为独角兽版")
            return Classification(False, "版本待确认", "关注的是 ZZ 版，标题未明确标注版本")

    # Legacy HG entries still exclude 1/100; MG and RE explicitly permit it.
    if expected_grade in {'HG', 'HGUC'} and "1/144" not in compact:
        other = _find_pattern(compact, OTHER_SCALE_NUMERIC_PATTERNS)
        if not other:
            other = _find_pattern(spaced, OTHER_SCALE_TOKEN_PATTERNS)
        if other:
            return Classification(False, "型号不符", f"不是 1/144 HGUC：{other[1]}")

    # 提到别的牌子、又完全没提万代 —— 判非万代（用户只要万代）。
    if re.search(r"(?:非|不是|并非|非正版)万代|国产.{0,8}(?:模型|拼装)", compact):
        return Classification(False, "非万代品牌", "商品明确写明国产或非万代")
    seller_brand = _find_term(normalize_text(title or text), NON_BANDAI_BRAND_TERMS)
    if seller_brand or not _find_term(compact, BANDAI_TERMS):
        other_brand = _find_term(compact, NON_BANDAI_BRAND_TERMS) or _find_pattern(
            spaced, ((r"(?<![A-Z0-9])XW(?![A-Z0-9])", "XW"),)
        )
        if other_brand:
            return Classification(False, "非万代品牌", "没有提到万代，且出现其他品牌")

    version_text = compact
    # This exact base kit is itself a Premium Bandai release, not a special
    # colour variant: https://p-bandai.jp/hobby/special-1000018431/
    if expected_grade == 'RE' and any('德南格' in alias.replace('·','') for alias in normalized_aliases):
        version_text = re.sub(r'PB限定(?:版)?|网络限定|網絡限定|网限|網限|限定版|限定', '', version_text)
    version, evidence = _detect_version(version_text)
    if version:
        return Classification(
            False,
            "限定/变体",
            f"限定或变体版本（不参与普通版比价）：{evidence}",
            version="variant",
            version_evidence=evidence,
        )

    new_term = _find_term(compact, NEW_UNBUILT_TERMS)
    if new_term:
        return Classification(True, "全新未拼", "", version="plain", version_evidence=new_term)
    if retail and _find_term(compact, BANDAI_TERMS) and ("拼装" in compact or "模型" in compact):
        return Classification(True, "商城新品", "商城在售拼装模型", version="plain")

    return Classification(
        False, "成色待确认", "未发现“全新/未拼/未拆”等明确描述", version="plain"
    )


# --------------------------------------------------------------------------
# 价格 / 运费 / 发货地
# --------------------------------------------------------------------------


def parse_price(text: str) -> float | None:
    """从文本里取价格。

    闲鱼搜索卡片的结构是 `¥` / `179` / `.89` **三个行**（三个独立 span），
    所以 innerText 长这样：

        ¥
        179
        .89
        1人想要

    不先把「整数行 + 小数行」接起来，就会把 ¥179.89 读成 179。
    但也**不能**把整段空白全删掉：`¥` `10` `1人想要` 会变成 "¥101人想要"，
    于是 ¥10 被读成 101。所以只在这种形态下合并：
    数字后面跟换行、再跟一个 `.dd` 结尾的小数。
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(
        r"(?<=[0-9])[ \t]*\r?\n[ \t]*(?=\.\s*[0-9]{1,2}(?![0-9]))",
        "",
        normalized,
    )

    patterns = (
        r"[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            price = float(match.group(1).replace(",", ""))
            if 0 < price < 100000:
                return price
    return None


def parse_shipping(text: str) -> tuple[float | None, str]:
    """返回 (运费, 状态)。

    状态取值：
      free             无条件包邮 -> 0.0
      non_remote_free  按非偏远地区包邮估算 -> 0.0
      conditional      粉丝包邮 / 满件满额包邮 / 淘宝下单包邮 等**有条件**包邮 -> None
      platform         页面/描述里给了明确运费数字 -> 该数字
      unknown          没说清 -> None
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    compact = re.sub(r"\s+", "", normalized)
    # This is a return-policy badge, not outbound free shipping.
    compact = compact.replace("描述不符包邮退", "").replace("包邮退", "")
    if re.search(r"(?:运费|運費|邮费|郵費|快递费|快遞費)[：:]?[¥￥]?[0-9]+(?:\.[0-9]+)?元?(?:起|左右|不等)", compact):
        return None, "unknown"

    # A seller can explicitly quote paid shipping, e.g. "不包邮，运费10元".
    amount = re.search(r"(?:运费|運費|邮费|郵費|快递费|快遞費)[：:]?[¥￥]?([0-9]+(?:\.[0-9]{1,2})?)(?:元)?(?![0-9.元]|起|左右|不等)", compact)
    if amount:
        return float(amount.group(1)), "platform"

    for pattern, _label in NOT_FREE_PATTERNS:
        if re.search(pattern, compact, re.IGNORECASE):
            return None, "unknown"

    # 条件包邮必须排在无条件包邮**前面**，否则"粉丝快递包邮"会被当成 0 元。
    conditional = _find_pattern(compact, CONDITIONAL_FREE_PATTERNS)
    if conditional:
        return None, "conditional"

    for pattern, status in FREE_PATTERNS:
        if re.search(pattern, compact, re.IGNORECASE):
            return 0.0, status

    patterns = (
        r"(?:运费|運費|邮费|郵費|快递费|快遞費)[：:]?[¥￥]?([0-9]+(?:\.[0-9]{1,2})?)元?",
        r"[¥￥]([0-9]+(?:\.[0-9]{1,2})?)(?:运费|運費|邮费|郵費)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            return float(match.group(1)), "platform"
    return None, "unknown"


def explicitly_paid_shipping(text: str) -> bool:
    compact = normalize_text(text)
    return any(re.search(pattern, compact, re.IGNORECASE) for pattern, _ in NOT_FREE_PATTERNS)


def parse_origin(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    patterns = (
        r"(?:发货地|發貨地|所在地)[：:]?\s*([^\s|·]{2,12})",
        r"(?:来自|來自)[：:]?\s*([^\s|·]{2,12})",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(1)
    return ""
