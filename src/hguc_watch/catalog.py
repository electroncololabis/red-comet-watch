from __future__ import annotations


# MSRP is the Japanese tax-inclusive reference price.  Older Bandai delivery
# sheets list pre-tax prices; these values consistently apply Japan's 10% tax so
# the dashboard compares against what a Japanese retail buyer actually pays.
INITIAL_WATCH_ITEMS = [
    {
        "name": "亚希玛",
        "model_code": "HGUC 054 / NRX-044",
        "search_query": "HGUC 亚希玛 万代",
        "aliases": ["亚希玛", "阿西玛", "アッシマー", "ASSHIMAR", "NRX-044", "HGUC 054", "HGUC 54"],
        "msrp_jpy": 2200,
        "official_url": "https://manual.bandai-hobby.net/menus/detail/892",
    },
    {
        "name": "卡普斯利",
        "model_code": "HGUC 058 / RX-110",
        "search_query": "HGUC 卡普斯利 加布斯雷 万代",
        "aliases": ["卡普斯利", "加布斯雷", "ガブスレイ", "GABTHLEY", "RX-110", "HGUC 058", "HGUC 58"],
        "msrp_jpy": 1760,
        "official_url": "https://manual.bandai-hobby.net/?page=55&sort=brand",
    },
    {
        "name": "迪杰",
        "model_code": "HGUC 219 / MSK-008",
        "search_query": "HGUC 迪杰 万代",
        "aliases": ["迪杰", "ディジェ", "DIJEH", "MSK-008", "HGUC 219"],
        "msrp_jpy": 2090,
        "official_url": "https://manual.bandai-hobby.net/menus/detail/485",
    },
    {
        "name": "帕拉斯雅典娜",
        "model_code": "HGUC 060 / PMX-001",
        "search_query": "HGUC 帕拉斯雅典娜 万代",
        "aliases": [
            "帕拉斯雅典娜",
            "帕拉斯·雅典娜",
            "パラス・アテネ",
            "PALACE ATHENE",
            "PALACE-ATHENE",
            "PMX-001",
            "HGUC 060",
            "HGUC 60",
        ],
        "msrp_jpy": 1980,
        "official_url": "https://manual.bandai-hobby.net/menus/detail/996",
    },
    {
        "name": "猎犬（巴乌德·多克）",
        "model_code": "HGUC 235 / NRX-055",
        "search_query": "HGUC 猎犬 巴乌德多克 万代",
        "aliases": [
            "猎犬",
            "巴乌德多克",
            "巴乌德·多克",
            "バウンド・ドック",
            "バウンドドック",
            "BAUND DOC",
            "BAUND-DOC",
            "BOUND DOC",
            "NRX-055",
            "HGUC 235",
        ],
        "msrp_jpy": 5500,
        "official_url": "https://manual.bandai-hobby.net/?categories%5B%5D=5&sort=new",
    },
    {
        "name": "梅萨拉",
        "model_code": "HGUC 157 / PMX-000",
        "search_query": "HGUC 梅萨拉 万代",
        "aliases": ["梅萨拉", "美萨拉", "メッサーラ", "MESSALA", "PMX-000", "HGUC 157"],
        "msrp_jpy": 3300,
        "official_url": "https://manual.bandai-hobby.net/menus/detail/379",
    },
    {
        "name": "加萨C 哈曼型",
        "model_code": "HGUC 062 / AMX-003",
        "search_query": "HGUC 加萨C 哈曼型 万代",
        "aliases": [
            "加萨C 哈曼",
            "加萨C哈曼",
            "加萨C 哈曼型",
            "加萨C 哈曼专用",
            "ガザC ハマーン",
            "GAZA C HAMAN",
            "AMX-003",
            "HGUC 062",
            "HGUC 62",
        ],
        "msrp_jpy": 1540,
        "official_url": "https://manual.bandai-hobby.net/menus/detail/897",
    },
]


DEFAULT_SETTINGS = {
    "scan_weekday": "MON",
    "scan_time": "03:00",
    "dashboard_time": "08:00",
    "destination_province": "",
    "destination_city": "",
    "max_results": "30",
    "detail_checks": "12",
    "headless": "false",
    "last_fx_rate": "",
    "last_fx_date": "",
    "last_fx_source": "",
}
