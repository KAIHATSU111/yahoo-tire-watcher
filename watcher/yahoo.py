from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote

from .rules import Watch, normalize, tire_sizes
from .shipping import ShippingQuote, yen


BASE = "https://auctions.yahoo.co.jp"
TIRE_CATEGORY = "2084005141"
AUCTION_ID = re.compile(r"[a-z]?[0-9]{6,16}")
SHIPPING_RESTRICTION = re.compile(
    r"個人宅|法人(?:様)?(?:宛|限定|のみ|専用)|会社(?:様)?(?:宛|限定|のみ)|営業所(?:止|留)"
    r"|手渡しのみ|引[き]?取[り]?(?:のみ|限定)|着払い|代引(?:き)?のみ|本州|関東"
    r"|(?:九州|四国|北海道|東北|中部|近畿|中国地方).{0,12}(?:別途|除く|のみ|限定)"
    r"|(?:送料無料|送料込み).{0,12}(?:条件|場合のみ)|配送不可|発送不可|お届け不可"
    r"|別途送料|送料(?:が|は)?(?:別途|かかる|掛かる)"
)
LANDLOCKED = frozenset({"栃木県", "群馬県", "埼玉県", "山梨県", "長野県", "岐阜県", "滋賀県", "奈良県"})


class NextData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.data: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.data.append(data)


def page_state(html: bytes, page: str) -> tuple[dict, dict]:
    try:
        parser = NextData()
        parser.feed(html.decode("utf-8-sig"))
        parser.close()
        data = json.loads("".join(parser.data))
        if data["page"] != page:
            raise ValueError
        return data["query"], data["props"]["pageProps"]["initialState"]
    except (ValueError, KeyError, TypeError):
        raise ValueError("Yahoo: 公開HTMLの埋め込みデータを確認できません") from None


def timestamp(value: str) -> datetime:
    date = datetime.fromisoformat(value)
    if date.tzinfo is None:
        raise ValueError("タイムゾーン付きの日時が必要です")
    return date


def in_tire_category(path: list) -> bool:
    return any(str(category["id"]) == TIRE_CATEGORY for category in path)


@dataclass(frozen=True)
class Listing:
    id: str
    title: str
    start: datetime
    end: datetime
    free_shipping: bool
    fixed_price: bool

    @property
    def url(self) -> str:
        return f"{BASE}/jp/auction/{self.id}"


def search_page(html: bytes, keyword: str, offset: int) -> tuple[list[Listing], int, int]:
    query, state = page_state(html, "/search/search/[[...params]]")
    try:
        if (query["p"] != keyword or str(query["auccat"]) != TIRE_CATEGORY
                or int(query.get("b", 1)) != offset):
            raise ValueError
        data = state["search"]["items"]["listing"]
        metadata = data["metadata"]
        rows = data["items"]
        total, page_size = data["totalResultsAvailable"], metadata["limit"]
        if (str(metadata["searchEngineStatus"]) != "200" or type(total) is not int or total < 0
                or type(page_size) is not int or not 1 <= page_size <= 100
                or not isinstance(rows, list) or (not rows and offset <= total)):
            raise ValueError
        listings = []
        for row in rows:
            if (row.get("isFleamarketItem") is not False or row.get("isShoppingItem") is not False
                    or not in_tire_category(row["categoryPath"])):
                continue
            id_, title = row["auctionId"], row["title"]
            if not AUCTION_ID.fullmatch(id_) or not isinstance(title, str) or not title.strip():
                raise ValueError
            listings.append(Listing(id_, title, timestamp(row["startTime"]), timestamp(row["endTime"]),
                                    row.get("isFreeShipping") is True, row.get("isFixedPrice") is True))
        return listings, total, page_size
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("Yahoo: 検索結果の形式またはページ番号を確認できません") from None


def detail_page(html: bytes, id_: str) -> dict:
    query, state = page_state(html, "/jp/auction/[aid]")
    try:
        item = state["item"]["detail"]["item"]
        if query["aid"] != id_ or item["auctionId"] != id_ or not isinstance(item["status"], str):
            raise ValueError
        return item
    except (ValueError, KeyError, TypeError):
        raise ValueError("Yahoo: 商品詳細の形式または商品IDを確認できません") from None


def matches_detail(item: dict, watch: Watch) -> bool:
    if (item.get("status") != "open" or item.get("isFleaMarket") is not False
            or type(item.get("quantity")) is not int or item["quantity"] != 1
            or not in_tire_category(item.get("category", {}).get("path", []))
            or not watch.matches_title(item["title"])
            or not watch.matches_condition(item.get("conditionName"))):
        return False

    spec = item.get("itemSpec") or {}
    fields = ("tireWidth", "tireFlatness", "tireRimDiameter")
    # Compare any individual size component published on the detail page.
    width, aspect, rim = re.split(r"[/R]", watch.size)
    for field, wanted in zip(fields, (width, aspect, rim), strict=True):
        if spec.get(field) is not None:
            try:
                if float(spec[field]) != int(wanted):
                    return False
            except (TypeError, ValueError):
                return False
    return True


def confirmed_shipping(item: dict, listing: Listing, prefecture: str) -> ShippingQuote | None:
    # The public HTML verified so far has no buyer-paid rate bound to a destination
    # and a billing unit. Search result shipping amounts do not establish either.
    if item.get("chargeForShipping") != "seller" or not listing.free_shipping:
        return None
    description = item.get("descriptionHtml")
    if not isinstance(description, str) or not description.strip():
        return None
    text = unescape(re.sub(r"<[^>]*>", " ", description))
    text = re.sub(r"\s+", "", normalize(item["title"] + text))
    # Even a seller-paid flag can coexist with exceptions in the item description.
    if SHIPPING_RESTRICTION.search(text) or prefecture.removesuffix("県") in text:
        return None
    if "離島" in text and prefecture not in LANDLOCKED:
        return None
    return ShippingQuote(prefecture, 0, "lot")


def prices(item: dict, fixed: bool) -> tuple[int | None, int | None]:
    seller = item.get("seller", {})
    if seller.get("isStore") is True or "taxRate" in item or "taxinPrice" in item:
        current, buy_now = yen(item.get("taxinPrice")), yen(item.get("taxinBidorbuy"))
    elif seller.get("isStore") is False:
        current, buy_now = yen(item.get("price")), yen(item.get("bidorbuy"))
    else:
        return None, None
    if fixed:
        # A fixed-price listing must not be sent as a bid opportunity.
        return None, buy_now if buy_now == current else None
    return current, buy_now


class Yahoo:
    def __init__(self, http):
        self.http = http

    def search(self, keyword: str, pages: int) -> list[Listing]:
        base_url = f"{BASE}/search/search/{quote(keyword, safe='')}/{TIRE_CATEGORY}"
        listings = []
        offset = 1
        for _ in range(pages):
            url = base_url if offset == 1 else f"{base_url}?b={offset}"
            html = self.http.request("Yahoo", url, retry=True)
            rows, total, page_size = search_page(html, keyword, offset)
            listings.extend(rows)
            offset += page_size
            if offset > total:
                break
        return listings

    def detail(self, listing: Listing) -> dict:
        html = self.http.request("Yahoo", listing.url, retry=True)
        return detail_page(html, listing.id)
