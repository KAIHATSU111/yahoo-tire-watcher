import json
import re

from .http import RequestFailed


def money(value: int) -> str:
    return f"{value:,}円"


def notification(listing, watch, shipping, current, buy_now) -> dict:
    lines = [f"サイズ {watch.size} ／ {watch.count}本",
             f"送料（{watch.prefecture}） {money(shipping)}"]
    if current is not None:
        total = current + shipping
        label = "上限内" if total <= watch.max_total_yen else "上限超過"
        lines.extend([f"現在価格 {money(current)} ／ 送料込み {money(total)}（{label}）",
                      "現在価格は入札により変動します。"])
    if buy_now is not None:
        total = buy_now + shipping
        label = "上限内" if total <= watch.max_total_yen else "上限超過"
        lines.append(f"即決価格 {money(buy_now)} ／ 送料込み {money(total)}（{label}）")
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{"title": listing.title[:125], "url": listing.url,
                    "description": "\n".join(lines) + f"\n{listing.url}"}],
    }


class Discord:
    def __init__(self, http, url: str):
        pattern = r"https://(?:discord\.com|discordapp\.com)/api/(?:v[0-9]+/)?webhooks/[0-9]+/[\w-]+"
        if re.fullmatch(pattern, url, re.ASCII) is None:
            raise ValueError("DISCORD_WEBHOOK_URL に Discord の Webhook URL を設定してください")
        self.http = http
        self.url = url + "?wait=true"

    def send(self, payload: dict):
        raw = self.http.request("Discord", self.url, method="POST", payload=payload)
        try:
            if not re.fullmatch(r"[0-9]+", json.loads(raw)["id"]):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise RequestFailed("Discord") from None
