"""Verified tax-inclusive Bandai reference prices; never infer an unknown kit's price."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .catalog import INITIAL_WATCH_ITEMS
from .filtering import listing_grades


# Product identities and prices checked against these official pages on 2026-10-17.
# These are references, not new watch entries. In particular, the Dreissen is ZZ,
# and Leopard is the 2026 HG, not the old HG or Leopard Destroy/Da Vinci kits.
VERIFIED_PRICES = [
    {"names": ["德莱森", "ドライセン", "DREISSEN"], "grades": {"HG", "HGUC"},
     "edition": "ZZ", "msrp_jpy": 2640,
     "official_url": "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=4543112891792000&grp_id=5325"},
    {"names": ["主天使", "主天使高达", "ガンダムキュリオス", "KYRIOS"], "grades": {"MG"},
     "msrp_jpy": 5500,
     "official_url": "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=4573102595478000&grp_id=5325"},
    {"names": ["迅雷", "迅雷高达", "ブリッツガンダム", "BLITZ"], "grades": {"MG"},
     "msrp_jpy": 4400,
     "official_url": "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=4543112757029000&grp_id=5325"},
    {"names": ["斑豹", "斑豹高达", "ガンダムレオパルド", "GUNDAM LEOPARD"], "grades": {"HG", "HGAW"},
     "msrp_jpy": 2420,
     "official_url": "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=4573102720528000&grp_id=5325"},
    {"names": ["德南格", "德南盖", "デナン・ゲー", "DEN'AN GEI"], "grades": {"RE"},
     "msrp_jpy": 3300,
     "official_url": "https://p-bandai.jp/item/item-1000235132/"},
    {"names": ["赛克艾恩", "泽克艾恩", "ゼクアイン", "XEKU EINS"], "grades": {"HG", "HGUC"},
     "msrp_jpy": 1760,
     "official_url": "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=4543112052650000&grp_id=5325"},
    *[{"names": [item["name"], *item["aliases"]], "grades": {"HG", "HGUC"},
       "msrp_jpy": item["msrp_jpy"], "official_url": item["official_url"]}
      for item in INITIAL_WATCH_ITEMS],
]


def _identity(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).upper()
    text = re.sub(r"^(?:HGUC|HGAW|HG|MG|RE(?:/100)?)\s*(?:1/\d+)?\s*", "", text)
    return re.sub(r"[\s·・'’（）()/_-]", "", text)


def fill_reference_price(data: dict[str, Any]) -> dict[str, Any]:
    """Fill a missing price only when the name, grade and edition agree.

    Preserve manually supplied prices and URLs. Unknown/ambiguous versions stay
    unset so the UI can request a verified price rather than display zero.
    """
    result = dict(data)
    if int(result.get("msrp_jpy") or 0) > 0:
        return result
    text = " ".join(str(result.get(key) or "") for key in ("name", "model_code", "search_query"))
    if re.search(r"透明|电镀|電鍍|限定|特别|特別|旧|舊|老|毁灭|毀滅|达芬奇|達芬奇|クリア|CLEAR|DESTROY|DA\s*VINCI", text, re.I):
        return result
    grades = set(listing_grades(text)) or {"HG"}
    identity = _identity(str(result.get("name") or ""))
    for reference in VERIFIED_PRICES:
        if not grades.issubset(reference["grades"]):
            continue
        if reference.get("edition") == "ZZ":
            if not re.search(r"(?<![A-Z])ZZ(?![A-Z])", text, re.I) or re.search(r"(?<![A-Z])UC(?![A-Z])|袖付|袖付き", text, re.I):
                continue
        if identity not in {_identity(name) for name in reference["names"]}:
            continue
        result["msrp_jpy"] = reference["msrp_jpy"]
        if not str(result.get("official_url") or "").strip():
            result["official_url"] = reference["official_url"]
        break
    return result
