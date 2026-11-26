from dataclasses import dataclass
import re
import tomllib
import unicodedata


SIZE = re.compile(
    r"(?<![0-9.])([1-3][0-9]{2})(?:\s*[/\-]\s*|\s+)([2-9][0-9])"
    r"(?:\s*Z?R\s*|\s*[/\-]\s*|\s+)([1-3][0-9])(?![0-9.]|[A-Z](?![A-Z]))"
)
COUNT = re.compile(r"(?<![0-9.,])([1-9][0-9]?)\s*本")
AMBIGUOUS_LOT = re.compile(
    r"バラ売|ばら売|単品販売|(?:1|一)本\s*(?:当たり|あたり|単価|価格|の価格|分の価格|から|[～~])"
    r"|[0-9]+\s*本\s*(?:以上|から|[～~])|[0-9]+\s*[～~+＋,、・]\s*[0-9]+\s*本"
    r"|(?:個数|数量)\s*[1-9]|選べる"
)
NOT_TIRES_ONLY = re.compile(
    r"ホイ[ーｰ]?ル\s*(?:単体|のみ|付き|付|セット)|タイヤ\s*(?:なし|無し|ホイ[ーｰ]?ル)"
    r"|ホイ[ーｰ]?ル.{0,24}タイヤ\s*セット|PCD\s*\d|\d(?:\.\d)?J\b"
    r"|タイヤチェ[ーｰ]ン|タイヤカバ[ーｰ]|タイヤラック"
)
USED = frozenset({"未使用に近い", "目立った傷や汚れなし", "やや傷や汚れあり",
                  "傷や汚れあり", "全体的に状態が悪い"})
PREFECTURES = frozenset((
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県"
    " 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県"
    " 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県"
    " 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県"
    " 鹿児島県 沖縄県"
).split())


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).upper()


def tire_sizes(value: str) -> set[str]:
    return {f"{width}/{aspect}R{rim}" for width, aspect, rim in SIZE.findall(normalize(value))}


def tire_count(title: str) -> int | None:
    title = normalize(title)
    if AMBIGUOUS_LOT.search(title):
        return None
    counts = {int(value) for value in COUNT.findall(title)}
    return next(iter(counts)) if len(counts) == 1 else None


@dataclass(frozen=True)
class Watch:
    size: str
    count: int
    max_total_yen: int
    condition: str
    prefecture: str
    query: str

    def matches_title(self, title: str) -> bool:
        return (not NOT_TIRES_ONLY.search(normalize(title))
                and tire_sizes(title) == {self.size}
                and tire_count(title) == self.count)

    def matches_condition(self, condition: str) -> bool:
        return (
            (self.condition in {"used", "any"} and condition in USED)
            or (self.condition in {"new", "any"} and condition == "未使用")
        )


@dataclass(frozen=True)
class Config:
    first_run: str
    search_pages: int
    max_details: int
    watches: tuple[Watch, ...]


def bounded_integer(value: object, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} は {minimum}〜{maximum} の整数にしてください")
    return value


def load_config(path) -> Config:
    with path.open("rb") as file:
        raw = tomllib.load(file)
    first_run = raw.get("first_run", "skip")
    if first_run not in ("skip", "notify"):
        raise ValueError('first_run は "skip" または "notify" にしてください')
    pages = bounded_integer(raw.get("search_pages", 2), 1, 3, "search_pages")
    limit = bounded_integer(raw.get("max_details", 12), 1, 24, "max_details")
    watches = []
    for row in raw.get("watch", []):
        size = normalize(row["size"]).strip()
        if SIZE.fullmatch(size) is None:
            raise ValueError("watch.size にタイヤサイズを1種類指定してください")
        size = next(iter(tire_sizes(size)))
        count = bounded_integer(row["count"], 1, 20, "watch.count")
        max_total = bounded_integer(row["max_total_yen"], 1, 10_000_000, "watch.max_total_yen")
        condition = row["condition"]
        if condition not in ("used", "new", "any"):
            raise ValueError('watch.condition は "used"、"new" または "any" にしてください')
        prefecture = row["prefecture"]
        if prefecture not in PREFECTURES:
            raise ValueError("watch.prefecture に都道府県の正式名を指定してください")
        query = row.get("query", size.lower().replace("/", "-")).strip()
        if not query or len(query) > 100 or query in {".", ".."} or re.search(r"[/\\?#\x00-\x1f]", query):
            raise ValueError("watch.query は100文字以内の検索語にしてください（/、\\、?、# は不可）")
        watches.append(Watch(size, count, max_total, condition, prefecture, query))
    if not watches or len({watch.query for watch in watches}) > 6:
        raise ValueError("監視条件を1件以上、異なる検索語は6件以内で指定してください")
    return Config(first_run, pages, limit, tuple(watches))
