"""抓取失败分类测试。

    登录墙 / 风控页 / 真的没结果 / 解析器坏了，**四种情况在运行记录里必须能区分**。

以前无论哪种都落到「闲鱼搜索页没有可识别的商品链接，页面结构可能已经变化」，
所以下一位 Agent 无法判断到底该修登录、修风控，还是真改选择器。
这个文件把四种（外加网络错误）钉死在测试里。

这里测的是 `_classify_search_failure` —— 一个纯函数，
输入是"页面诊断字典 + 登录态"，不需要真的开浏览器。
"""

from __future__ import annotations

import pytest

from hguc_watch.xianyu import (
    CAUSE_LABELS,
    EmptyResultError,
    IllegalAccessError,
    LoginRequiredError,
    NetworkError,
    SearchParseError,
    VerificationRequiredError,
    XianyuError,
    _classify_search_failure,
)


def diagnostics(body: str = "", url: str = "https://www.goofish.com/search?q=x") -> dict:
    return {
        "url": url,
        "title": "闲鱼",
        "body": body,
        "body_error": "",
        "counts": {"anchors": 12, "itemLinks": 0, "images": 3, "iframes": 0},
    }


def test_illegal_access_is_its_own_cause() -> None:
    error = _classify_search_failure(diagnostics("非法访问，请使用正常浏览器访问闲鱼"), True, "HGUC 迪杰 万代")
    assert isinstance(error, IllegalAccessError)
    assert error.cause == "illegal_access"


def test_verification_page_is_its_own_cause() -> None:
    error = _classify_search_failure(diagnostics("请完成安全验证后继续访问"), True, "HGUC 迪杰 万代")
    assert isinstance(error, VerificationRequiredError)
    assert error.cause == "verification_required"


def test_login_wall_is_its_own_cause() -> None:
    error = _classify_search_failure(diagnostics("登录后可以更懂你"), False, "HGUC 迪杰 万代")
    assert isinstance(error, LoginRequiredError)
    assert error.cause == "login_required"


def test_missing_session_without_login_marker_is_still_login() -> None:
    """页面没有明显的登录文案，但 cookie 登录态不完整 -> 也是登录问题。"""
    error = _classify_search_failure(diagnostics("加载中……"), False, "HGUC 迪杰 万代")
    assert isinstance(error, LoginRequiredError)
    assert error.cause == "login_required"


def test_genuine_empty_result_is_its_own_cause() -> None:
    error = _classify_search_failure(
        diagnostics("抱歉，没有找到与“HGUC 迪杰 万代”相关的宝贝"), True, "HGUC 迪杰 万代"
    )
    assert isinstance(error, EmptyResultError)
    assert error.cause == "empty_result"


def test_parse_failure_is_its_own_cause() -> None:
    """登录了、页面正文也正常，就是没有卡片 —— 这一类才是真的 DOM/选择器问题。"""
    error = _classify_search_failure(
        diagnostics("闲鱼 首页 推荐 猜你喜欢 " + "正常正文" * 50), True, "HGUC 迪杰 万代"
    )
    assert isinstance(error, SearchParseError)
    assert error.cause == "parse_failed"


def test_every_cause_is_distinct_and_labelled() -> None:
    causes = {
        _classify_search_failure(diagnostics("非法访问"), True, "q").cause,
        _classify_search_failure(diagnostics("滑动验证"), True, "q").cause,
        _classify_search_failure(diagnostics("立即登录"), False, "q").cause,
        _classify_search_failure(diagnostics("没有找到"), True, "q").cause,
        _classify_search_failure(diagnostics("正常正文"), True, "q").cause,
    }
    assert len(causes) == 5, f"失败原因没有区分开：{causes}"
    for cause in causes:
        assert cause in CAUSE_LABELS


def test_illegal_access_wins_over_login_marker() -> None:
    """页面同时出现"非法访问"和"立即登录"时，先报非法访问（更具体）。"""
    error = _classify_search_failure(diagnostics("非法访问 立即登录"), True, "q")
    assert error.cause == "illegal_access"


def test_verification_wins_over_login_marker() -> None:
    error = _classify_search_failure(diagnostics("访问验证 立即登录"), True, "q")
    assert error.cause == "verification_required"


def test_describe_carries_machine_readable_cause() -> None:
    """"运行记录里必须能区分" —— 写进 runs.error 的文本要带 cause。"""
    error = _classify_search_failure(diagnostics("登录后可以更懂你"), False, "q")
    text = error.describe()
    assert "login_required" in text
    assert "登录墙" in text


def test_evidence_never_leaks_cookie_values() -> None:
    """证据里只能有选择器计数和 URL，不能有 cookie 值/token。"""
    info = diagnostics("登录后可以更懂你")
    info["counts"] = {"anchors": 3, "itemLinks": 0, "images": 0, "iframes": 1}
    error = _classify_search_failure(info, False, "q")
    assert "cookie" not in error.evidence.lower().replace("cookie登录态", "")
    assert "选择器计数" in error.evidence


def test_network_error_is_a_xianyu_error() -> None:
    error = NetworkError("打开搜索页失败：Timeout 60000ms exceeded")
    assert isinstance(error, XianyuError)
    assert error.cause == "network"


@pytest.mark.parametrize(
    "body,authenticated,expected",
    [
        ("请使用正常浏览器访问闲鱼", False, "illegal_access"),
        ("拖动滑块完成拼图", True, "verification_required"),
        ("扫码登录", False, "login_required"),
        ("暂无相关宝贝，换个关键词试试", True, "empty_result"),
        ("这里什么提示都没有", True, "parse_failed"),
    ],
)
def test_cause_matrix(body: str, authenticated: bool, expected: str) -> None:
    error = _classify_search_failure(diagnostics(body), authenticated, "q")
    assert error.cause == expected
