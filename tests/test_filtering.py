"""商品筛选规则的回归测试。"""

from hguc_watch.filtering import (
    FILTER_RULE_VERSION,
    classify_listing,
    has_duplicated_block,
    normalize_text,
    parse_origin,
    parse_price,
    parse_shipping,
)

DIJEH = ["迪杰", "ディジェ", "DIJEH", "MSK-008", "HGUC 219"]
ASSHIMAR = ["亚希玛", "阿西玛", "アッシマー", "ASSHIMAR", "NRX-044", "HGUC 054", "HGUC 54"]
MESSALA = ["梅萨拉", "美萨拉", "メッサーラ", "MESSALA", "PMX-000", "HGUC 157"]
PALACE = ["帕拉斯雅典娜", "パラス・アテネ", "PMX-001", "HGUC 060"]


# ---------------------------------------------------------------------------
# 商品标题及详情示例
# ---------------------------------------------------------------------------

# listing 235 / 61 —— 普通版 HGUC 219，v1 因为裸子串"配件"被误杀
T_DIJEH_235 = (
    "【出】万代 HGUC 219 1/144 迪杰 阿姆罗专用迪 殊 MSK-008 拼装高达模型"
    "全新未拼装，配件齐全默认盒损，盒控慎拍申通包邮闲置物品售出不退不换"
)
# listing 244 / 245 / 72 —— 普通版，v1 因为裸子串"盒子"被误杀
T_DIJEH_244 = (
    "全新 万代 HGUC 1/144 迪杰 阿姆罗专用型 MSK -008 高达拼装模型 "
    "本品为全新万代正品板件塑封包装未拆盒子说明书都好，"
    "主页内同时买任意2款满百商品可包邮非偏远地区，发货时会拍板件塑封照片和打包视频"
    "确保塑封完好及盒况正常，一经发货不退不换无售后！本品只保全新未拆不保各种出厂问题及运损情况"
)
# listing 236 —— PB 限定 NT 版，v1 混进了普通版最低价
T_DIJEH_236 = (
    "《包邮非偏远》出全新未拆封 万代原装 PB限定 HGUC 迪 杰(NT版) 默认盒损 "
    "天津可自提 不讲价  备注： 1、物品发货前会给买家拍照（物品图和打包图）确认无误后第二天发货 "
    "2、商品售出后不退货无售后"
)
# listing 239 —— 彩透 会场限定
T_DIJEH_239 = (
    "万代 BANDAI HGUC 迪杰 阿姆罗·雷机 彩透 会场 限定  高达Z UC0087 全新未拆封 "
    "非偏远包邮 不出盒控 盒控勿扰 请出门淘宝 不保盒 运输盒损 请找快递"
)
# listing 243 —— 网限 Narrative Ver.
T_DIJEH_243 = (
    "全新现货 万代网限 HGUC 1/144 迪杰(Narrat ive Ver.) 只保证内部物品全新 默认盒损 "
    "中奖自负无售后 标价为单价 包邮 如有问题可以开盒检查拍照视频验货"
)
# listing 232 —— PB限定 NT.Ver，运费实收
T_DIJEH_232 = (
    "万代 PB限定 HGUC 1/144 MSK-008 迪杰 高达 NT.Ver NT版 现货。全新未拼，盒损，盒控勿扰。"
    "运费实收，不刀不包。售出不退。"
)
# listing 246 —— 普通版，但"粉丝快递包邮"是条件包邮
T_DIJEH_246 = (
    "万代HGUC 219高达Z HG迪杰高达阿姆罗机再版蓝标拼装 模型 全新未拆 八角尖尖 好盒发出！ "
    "粉丝快递包邮！现货可批发！ 打包有泡沫和硬纸箱！ 不包运输盒损！发货不退不换 ！"
)
# listing 224 —— 只在卖包装盒
T_DIJEH_224 = (
    "万代HGUC迪杰包装盒，是包装盒！只有包装盒！没有模型，适合 收藏用，"
    "成色如图二手物品售出不退不换，看清再拍"
)
# listing 225 —— 补件
T_DIJEH_225 = (
    "【HG补件】现货 万代 HGUC 迪杰 阿姆罗 补件 散件 拍下默认为封面款补件，"
    "默认每个零件号为单个零件。 1.零件价格:价格视具体而定， 单零件为13元~18元包邮不等"
)
# listing 177 / 4 —— 第三方 XW 模型，¥28 顶掉了亚希玛普通版最低价
T_ASSHIMAR_XW = (
    "【全新 转手】XW模型 HG 1/144 南瓜饼亚希玛 AR -01 01号机 可变形 模型  "
    "带双枪特典，全新未拆封 E.F.ARMY 模仿.scheme 一体变形，拼装模型。"
)
# listing 197 / 24 / 153 / 326 —— 关键词堆砌的成品帖（GFFN / MG / RG）
T_KEYWORD_STUFFED = (
    "FIX GFFN 0041 高达ZII Z2 Katoki 非ROBOT魂 Ka魂 MR魂 HG HGUC MG RG Zeta高达 "
    "Z高达 Z3 灵格斯 梅塔斯 亚希玛 梅萨拉 PALACE ATHENE"
)
# listing 254 / 81 —— 代购模板帖，标题整段重复，¥59
T_PALACE_DAIGOU = (
    "【内袋未开封】1/144 HGUC PMX-0 日本代购 【内袋未开封】1/144 HGUC PMX-001 "
    "帕拉斯·雅典娜《机动战士Z高达》[0141425] Gunpla（高达模型）"
)
# listing 70 —— 一条帖子里挂了 5 个 HGUC
T_MULTI_ITEM = (
    "HGUC RX124 TR6 伍德沃特  透明   280 HGUC RX-160 拜亚兰 透明  360 "
    "HGUC RMS-117 加里波第β 透明 200 HGUC RX-178 MK2高达 新生 透明 190 "
    "HGUC MSK-008 迪杰 透明 260"
)
# listing 58 / 231 —— 其实是 1/100 的其他牌子
T_WRONG_SCALE = (
    "包邮 胶佬星人  捷达 阿姆罗专用型 1/100拼装模型 全新未拆封，带原包装盒 "
    "非偏远包邮 默认盒损，不包运损 售出不退不换 盒控勿扰"
)


# ---------------------------------------------------------------------------
# 1. 验收标准：普通版入选、变体不得混入普通版比价
# ---------------------------------------------------------------------------


def test_plain_dijeh_235_is_eligible() -> None:
    """普通版商品「配件齐全」的普通版必须入选。"""
    result = classify_listing(T_DIJEH_235, DIJEH)
    assert result.eligible is True
    assert result.condition_status == "全新未拼"
    assert result.version == "plain"


def test_plain_dijeh_244_with_box_and_manual_is_eligible() -> None:
    """普通版商品「盒子说明书都好」是误杀，必须入选。"""
    result = classify_listing(T_DIJEH_244, DIJEH)
    assert result.eligible is True
    assert result.version == "plain"


def test_plain_dijeh_246_is_eligible_but_shipping_is_conditional() -> None:
    result = classify_listing(T_DIJEH_246, DIJEH)
    assert result.eligible is True
    assert result.version == "plain"
    # "粉丝快递包邮" 不是无条件包邮
    assert parse_shipping(T_DIJEH_246) == (None, "conditional")


def test_variants_never_mix_into_plain_comparison() -> None:
    """PB限定 / NT版 / 彩透会场限定 / 网限 Narrative 都必须被判成变体。"""
    for title in (T_DIJEH_236, T_DIJEH_239, T_DIJEH_243, T_DIJEH_232):
        result = classify_listing(title, DIJEH)
        assert result.eligible is False, title
        assert result.version == "variant", title
        assert result.condition_status == "限定/变体", title


def test_variant_records_which_marker_matched() -> None:
    result = classify_listing(T_DIJEH_236, DIJEH)
    assert result.version_evidence  # 例如 "NT"

    result = classify_listing(T_DIJEH_239, DIJEH)
    assert result.version_evidence  # 例如 "限定"


# ---------------------------------------------------------------------------
# 2. 完整的正面描述不能被当成"非完整模型"
# ---------------------------------------------------------------------------


def test_completeness_words_that_are_not_exclusions() -> None:
    """「配件齐全」「盒子完好」「带说明书」都是**完整**的描述，不能排除。"""
    for title in (
        "万代 HGUC 219 迪杰 全新未拼 配件齐全 包邮",
        "万代 HGUC 219 迪杰 全新未拆 盒子完好，带说明书",
        "万代 HGUC 157 梅萨拉 全新未拆封 盒子完好 杭州可自提",
        "万代 HGUC 157 梅萨拉 全新未拆封，红标。盒子如图，配件齐全，带说明书。1/144比例",
    ):
        assert classify_listing(title, DIJEH + MESSALA).eligible is True, title


def test_explicit_incomplete_semantics_are_rejected() -> None:
    assert classify_listing(T_DIJEH_224, DIJEH).condition_status == "非完整模型"
    assert classify_listing(T_DIJEH_225, DIJEH).condition_status == "非完整模型"
    for title in (
        "万代 HGUC 219 迪杰 全新 只有包装盒 无模型",
        "万代 HGUC 219 迪杰 全新未拼 拆卖 只出主体",
        "万代 HGUC 219 迪杰 全新未拼 单卖配件 请私聊",
        "万代 HGUC 219 迪杰 GK改件 树脂件 全新",
    ):
        result = classify_listing(title, DIJEH)
        assert result.eligible is False, title
        assert result.condition_status == "非完整模型", title


def test_hard_incomplete_is_cancelled_by_negation() -> None:
    """"整盒不拆卖"说的是**不**拆卖，不能当排除词。"""
    result = classify_listing("万代 HGUC 219 迪杰 全新未拆 整盒出 不拆卖 包邮", DIJEH)
    assert result.eligible is True


def test_decal_is_a_bonus_but_a_decal_only_listing_is_not() -> None:
    assert classify_listing("万代 HGUC 219 迪杰 全新未拆 送水贴", DIJEH).eligible is True
    assert classify_listing("万代 HGUC 219 迪杰 全新水贴", DIJEH).condition_status == "非完整模型"


# ---------------------------------------------------------------------------
# 3. 求购 / 否定语义 / 预售
# ---------------------------------------------------------------------------


def test_wanted_posts_are_rejected() -> None:
    for title in (
        "收 HGUC 迪杰 全新未拆",
        "高价收 万代 HGUC 219 迪杰",
        "求购 HGUC 迪杰 全新未拆封",
        "长期收购 万代 HGUC 梅萨拉",
    ):
        result = classify_listing(title, DIJEH + MESSALA)
        assert result.eligible is False, title
        assert result.condition_status == "求购帖", title


def test_wanted_detection_does_not_fire_on_要求() -> None:
    """真实文本「要求高的自行补钱发顺丰」不能当成求购。"""
    title = "万代 HGUC 1/144 亚希玛 全新未拆封 包邮 要求高的自行补钱发顺丰"
    assert classify_listing(title, ASSHIMAR).eligible is True


def test_negated_condition_words_are_not_accepted() -> None:
    """「非全新 非未拼」含"全新""未拼"子串，但都是被否定的，不能算全新。"""
    result = classify_listing("万代 HGUC 219 迪杰 非全新 非未拼 盒况见图", DIJEH)
    assert result.eligible is False
    assert result.condition_status == "成色待确认"


def test_negated_preorder_word_is_not_a_preorder() -> None:
    """"不是预售 现货" 不该被判成预售。"""
    result = classify_listing("万代 HGUC 219 迪杰 不是预售 现货 没写新旧", DIJEH)
    assert result.condition_status != "预售/预定"


def test_preorder_variants_are_rejected() -> None:
    assert classify_listing("HGUC 梅萨拉 全新预售，先付定金", MESSALA).condition_status == "预售/预定"
    assert (
        classify_listing("万代 HGUC 219 迪杰 全新 预约 到货后发货", DIJEH).condition_status
        == "预售/预定"
    )


def test_unconfirmed_condition_is_not_eligible() -> None:
    # listing 275 / 102 ——「老货堆积所以出了」没有成色证据
    result = classify_listing("HGUC 060 PMX-001 帕拉斯雅典娜 老货堆积所以出了", PALACE)
    assert result.eligible is False
    assert result.condition_status == "成色待确认"


# ---------------------------------------------------------------------------
# 4. 运费：条件包邮 / 到付 / 非偏远
# ---------------------------------------------------------------------------


def test_conditional_free_shipping_is_not_zero() -> None:
    cases = {
        "全新未拆 粉丝快递包邮": "conditional",  # listing 246
        "全新未拆 主页内同时买任意2款满百商品可包邮非偏远地区": "conditional",  # 244/245
        "全新未拆 淘宝下单全场包邮": "conditional",  # listing 196 的写法
        "全新未拆 满2件包邮": "conditional",
        "全新未拆 全国普邮包邮 关注店铺包邮": "conditional",
    }
    for text, expected in cases.items():
        assert parse_shipping(text) == (None, expected), text


def test_unconditional_free_shipping_is_zero() -> None:
    assert parse_shipping("全新未拆 申通包邮") == (0.0, "free")  # listing 235
    assert parse_shipping("全新未拆 中通包邮") == (0.0, "free")
    assert parse_shipping("非偏远地区包邮") == (0.0, "non_remote_free")


def test_pay_on_delivery_is_unknown() -> None:
    for text in (
        "全新未拆 顺丰到付",
        "全新未拆 只发顺丰到付",
        "全新未拼 运费实收，不刀不包",  # listing 232
        "全新未拆 邮费自理",
        "全新未拆 不包邮",
    ):
        price, status = parse_shipping(text)
        assert price is None, text
        assert status == "unknown", text


# ---------------------------------------------------------------------------
# 5. 其他品牌 / 其他比例 / 多件混列 / 代购模板
# ---------------------------------------------------------------------------


def test_third_party_brand_is_rejected() -> None:
    """listing 4/177：XW模型 HG 1/144 亚希玛 ¥28，不是万代。"""
    result = classify_listing(T_ASSHIMAR_XW, ASSHIMAR)
    assert result.eligible is False
    assert result.condition_status == "非万代品牌"


def test_bandai_listing_is_not_rejected_by_the_brand_rule() -> None:
    assert classify_listing(T_DIJEH_235, DIJEH).eligible is True


def test_keyword_stuffed_finished_product_is_rejected() -> None:
    """listing 24/197/153/326：¥695 的 GFFN 成品帖，标题里塞了 HGUC。"""
    result = classify_listing(T_KEYWORD_STUFFED, ASSHIMAR)
    assert result.eligible is False
    assert result.condition_status == "型号不符"


def test_multi_item_lot_is_rejected() -> None:
    result = classify_listing(T_MULTI_ITEM, DIJEH)
    assert result.eligible is False
    assert result.condition_status == "多件混列"


def test_daigou_template_listing_is_rejected() -> None:
    """listing 81/254：标题整段重复的代购模板，¥59 是占位价。"""
    result = classify_listing(T_PALACE_DAIGOU, PALACE)
    assert result.eligible is False
    assert result.condition_status == "代购/占位模板"


def test_duplicate_block_helper() -> None:
    assert has_duplicated_block(normalize_text(T_PALACE_DAIGOU)) != ""
    assert has_duplicated_block(normalize_text(T_DIJEH_235)) == ""


def test_wrong_scale_and_brand_is_rejected() -> None:
    result = classify_listing(T_WRONG_SCALE, DIJEH)
    assert result.eligible is False


def test_mg_rg_are_detected_even_when_written_adjacent() -> None:
    """"HGUC MG RG" 去空白后是 "HGUCMGRG"，缩写边界不能失效。"""
    title = "万代 HGUC 迪杰 全新未拆 MG RG 完成品"
    assert classify_listing(title, DIJEH).eligible is False


def test_one_forty_four_is_not_rejected_by_the_scale_rule() -> None:
    assert classify_listing("万代 HGUC 219 迪杰 1/144 全新未拆 包邮", DIJEH).eligible is True


# ---------------------------------------------------------------------------
# 6. 规则版本
# ---------------------------------------------------------------------------


def test_rule_version_is_exported() -> None:
    # 每次改判定规则都要 +1；库里用它区分新旧规则的数据。
    assert isinstance(FILTER_RULE_VERSION, int)
    assert FILTER_RULE_VERSION >= 2


# ---------------------------------------------------------------------------
# 7. 原有基础测试（语义未变）
# ---------------------------------------------------------------------------


def test_accepts_new_unbuilt_matching_model() -> None:
    result = classify_listing("万代 HGUC 054 亚希玛 全新未拼，盒况好", ["亚希玛", "ASSHIMAR"])
    assert result.eligible is True
    assert result.condition_status == "全新未拼"


def test_rejects_preorder_even_if_new() -> None:
    result = classify_listing("HGUC 梅萨拉 全新预售，先付定金", ["梅萨拉"])
    assert result.eligible is False
    assert result.condition_status == "预售/预定"


def test_rejects_built_and_parts() -> None:
    built = classify_listing("HGUC 迪杰 已拼素组", ["迪杰"])
    parts = classify_listing("HGUC 迪杰 全新水贴", ["迪杰"])
    assert built.eligible is False
    assert parts.eligible is False


def test_rejects_unconfirmed_condition() -> None:
    result = classify_listing("万代 HGUC 157 梅萨拉 盒况见图", ["梅萨拉", "HGUC 157"])
    assert result.eligible is False
    assert result.condition_status == "成色待确认"


def test_shipping_rules_for_zhuhai() -> None:
    assert parse_shipping("非偏远地区包邮") == (0.0, "non_remote_free")
    assert parse_shipping("广东省内包邮") == (0.0, "free")
    assert parse_shipping("运费：¥12.5") == (12.5, "platform")
    assert parse_shipping("不包邮，运费到付") == (None, "unknown")


def test_price_and_origin_parsing() -> None:
    assert parse_price("¥ 168.00  9人想要") == 168.0
    assert parse_origin("发货地：上海市 闵行区") == "上海市"
