import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from watcher.__main__ import run
from watcher.discord import Discord
from watcher.history import GitHubHistory, History, LocalHistory
from watcher.http import RequestFailed
from watcher.rules import Config, Watch, load_config, tire_count, tire_sizes
from watcher.shipping import ShippingQuote
from watcher.yahoo import (
    Listing, TIRE_CATEGORY, Yahoo, confirmed_shipping, detail_page, matches_detail, prices, search_page,
)


NOW = datetime(2026, 11, 20, 3, tzinfo=timezone.utc)
WATCH = Watch("225/45R17", 4, 24000, "used", "埼玉県", "225-45r17")
LISTING = Listing("k1250718376", "中古タイヤ 225/45R17 4本セット", NOW - timedelta(hours=2),
                  NOW + timedelta(hours=2), True, False)
CATEGORY_PATH = [{"id": TIRE_CATEGORY, "name": "タイヤ"}]


def item_data():
    return {
        "auctionId": LISTING.id, "title": LISTING.title, "status": "open",
        "category": {"path": CATEGORY_PATH}, "conditionName": "やや傷や汚れあり",
        "quantity": 1, "isFleaMarket": False, "seller": {"isStore": True},
        "price": 21800, "bidorbuy": 21801, "taxinPrice": 23980, "taxinBidorbuy": 23981,
        "chargeForShipping": "seller", "descriptionHtml": "<p>送料無料。沖縄・離島を除く。</p>",
        "itemSpec": {"tireWidth": 225, "tireFlatness": 45, "tireRimDiameter": "17.0"},
    }


def next_html(page, query, state):
    data = {"page": page, "query": query, "props": {"pageProps": {"initialState": state}}}
    return f'<html><script type="application/json" id="__NEXT_DATA__">{json.dumps(data)}</script></html>'.encode()


def search_html(offset=1, *, total=75):
    row = {
        "auctionId": LISTING.id, "title": LISTING.title, "categoryPath": CATEGORY_PATH,
        "startTime": LISTING.start.isoformat(), "endTime": LISTING.end.isoformat(),
        "isFreeShipping": True, "isFixedPrice": False, "isFleamarketItem": False, "isShoppingItem": False,
    }
    data = {"items": [row] if total else [], "totalResultsAvailable": total,
            "metadata": {"limit": 50, "searchEngineStatus": "200"}}
    query = {"p": WATCH.query, "auccat": TIRE_CATEGORY, "b": str(offset)}
    return next_html("/search/search/[[...params]]", query, {"search": {"items": {"listing": data}}})


class MatchingTests(TestCase):
    def test_load_sample_config(self):
        self.assertEqual([w.size for w in load_config(Path("config.toml")).watches],
                         ["225/45R17", "205/55R16"])

    def test_size_variants(self):
        for form in ("225/45R17", "225/45 R17", "225 45 17", "225-45-17", "225/45ZR17", "２２５／４５Ｒ１７"):
            self.assertEqual(tire_sizes(form), {"225/45R17"})
            self.assertTrue(WATCH.matches_title(f"中古タイヤ {form} 91W ４本セット"))

    def test_no_wrong_sizes_or_wheels(self):
        for title in ("中古 225/50R17 4本", "ホイール単体 225/45R17 適合 4本",
                      "225/45R17 4本タイヤ・ホイールセット",
                      "225/45R17 225/50R17 各2本 4本", "225/45R170 中古4本"):
            self.assertFalse(WATCH.matches_title(title), title)

    def test_ambiguous_count(self):
        for title in ("225/45R17 中古", "225/45R17 4本 1本価格", "225/45R17 2本～4本",
                      "225/45R17 4本中2本", "225/45R17 4本以上"):
            self.assertIsNone(tire_count(title), title)

    def test_detail_validates_spec_condition_inventory_and_category(self):
        self.assertTrue(matches_detail(item_data(), WATCH))
        for fields in ({"conditionName": "未使用"}, {"quantity": 4}, {"status": "closed"},
                       {"itemSpec": {"tireFlatness": 50}}, {"category": {"path": []}}):
            self.assertFalse(matches_detail(item_data() | fields, WATCH), fields)


class ShippingTests(TestCase):
    def test_shipping_calculation_uses_confirmed_unit(self):
        for unit, parcels, expected in (("lot", None, 1400), ("tire", None, 5600),
                                        ("parcel", 2, 2800), ("parcel", None, None)):
            quote = ShippingQuote("埼玉県", 1400, unit, parcels)
            self.assertEqual(quote.total("埼玉県", 4), expected)
            self.assertIsNone(quote.total("東京都", 4))

    def test_free_shipping_and_restrictions(self):
        item = item_data()
        self.assertEqual(confirmed_shipping(item, LISTING, "埼玉県").total("埼玉県", 4), 0)
        self.assertIsNone(confirmed_shipping(item, LISTING, "東京都"))
        self.assertIsNone(confirmed_shipping(item, LISTING, "沖縄県"))
        self.assertIsNone(confirmed_shipping(item | {"descriptionHtml": "送料無料は会社宛のみ"}, LISTING, "埼玉県"))
        self.assertIsNone(confirmed_shipping(item, replace(LISTING, free_shipping=False), "埼玉県"))

    def test_buyer_paid_rate_without_confirmed_scope_is_unknown(self):
        item = item_data() | {"chargeForShipping": "winner", "shippingInput": "ARRIVAL",
                              "descriptionHtml": "埼玉県 2,200円。梱包数は落札後にご案内します。"}
        self.assertIsNone(confirmed_shipping(item, LISTING, "埼玉県"))

    def test_current_and_buy_now_use_tax_inclusive_prices(self):
        self.assertEqual(prices(item_data(), False), (23980, 23981))
        self.assertEqual(prices(item_data() | {"taxinBidorbuy": 23980}, True), (None, 23980))
        self.assertEqual(prices(item_data() | {"taxinPrice": None}, False), (None, 23981))


class YahooTests(TestCase):
    def test_search_and_pagination(self):
        http = Mock()
        http.request.side_effect = [search_html(), search_html(51)]
        self.assertEqual(Yahoo(http).search(WATCH.query, 2), [LISTING, LISTING])
        self.assertTrue(http.request.call_args.args[1].endswith("?b=51"))

    def test_empty_and_unrecognized_search_are_distinct(self):
        self.assertEqual(search_page(search_html(total=0), WATCH.query, 1)[0], [])
        for raw in (b"<html>denied</html>", search_html(51)):
            with self.assertRaises(ValueError):
                search_page(raw, WATCH.query, 1)

    def test_detail_id_is_checked(self):
        page = next_html("/jp/auction/[aid]", {"aid": LISTING.id}, {"item": {"detail": {"item": item_data()}}})
        self.assertEqual(detail_page(page, LISTING.id), item_data())
        with self.assertRaises(ValueError):
            detail_page(page, "x1234567")


class HistoryTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = LocalHistory(Path(temp.name) / "state.json")
        self.yahoo = Mock()
        self.yahoo.search.return_value = [LISTING, LISTING]
        self.yahoo.detail.return_value = item_data()
        self.send = Mock()
        self.config = Config("notify", 1, 12, (WATCH,))

    def test_success_is_saved_and_rerun_is_deduplicated(self):
        run(self.config, self.yahoo, self.store, self.send, now=NOW)
        self.assertEqual(self.store.load().sent, {LISTING.id})
        run(self.config, self.yahoo, self.store, self.send, now=NOW)
        self.send.assert_called_once()
        description = self.send.call_args.args[0]["embeds"][0]["description"]
        for term in ("4本", "23,980円", "23,981円", "0円", "変動", LISTING.url):
            self.assertIn(term, description)

    def test_failed_delivery_is_retried_next_run(self):
        self.send.side_effect = RequestFailed("Discord", 429)
        with self.assertRaises(RequestFailed):
            run(self.config, self.yahoo, self.store, self.send, now=NOW)
        self.assertEqual(self.store.load(), History())
        self.send.side_effect = None
        run(self.config, self.yahoo, self.store, self.send, now=NOW)
        self.assertIn(LISTING.id, self.store.load().sent)

    def test_baseline_uses_listing_time_not_search_rank(self):
        config = replace(self.config, first_run="skip")
        self.yahoo.search.return_value = []
        run(config, self.yahoo, self.store, self.send, now=NOW)
        self.yahoo.search.return_value = [LISTING, replace(LISTING, id="b1234567", start=NOW + timedelta(minutes=1))]
        run(config, self.yahoo, self.store, self.send, now=NOW + timedelta(minutes=30))
        self.send.assert_called_once()
        self.assertEqual(self.store.load().sent, {"b1234567"})
        self.assertEqual(self.store.load().skip_before, NOW)

    def test_dry_run_does_not_write_and_failed_search_does_not_initialize(self):
        run(self.config, self.yahoo, self.store, now=NOW)
        self.assertEqual(self.store.load(), History())
        self.yahoo.search.side_effect = RequestFailed("Yahoo", 403)
        with self.assertRaises(RequestFailed):
            run(replace(self.config, first_run="skip"), self.yahoo, self.store, self.send, now=NOW)
        self.assertEqual(self.store.load(), History())

    def test_github_sends_blob_sha_and_recovers_lost_confirmation(self):
        def response(history, sha):
            return json.dumps({"type": "file", "encoding": "base64", "sha": sha,
                               "content": base64.b64encode(history.encode()).decode()}).encode()

        new = History(initialized=True).record(LISTING.id)
        http = Mock()
        http.request.side_effect = [response(History(), "a" * 40), RequestFailed("GitHub"),
                                    response(new, "b" * 40)]
        store = GitHubHistory(http, "owner/repo", "main", "test-token")
        self.assertEqual(store.load(), History())
        store.save(new)
        self.assertEqual(store.sha, "b" * 40)
        self.assertEqual(http.request.call_args_list[1].kwargs["payload"]["sha"], "a" * 40)

    def test_discord_waits_for_a_message_id(self):
        http = Mock()
        sender = Discord(http, "https://discord.com/api/webhooks/123/test")
        http.request.return_value = b'{"id":"1234"}'
        sender.send({})
        self.assertTrue(http.request.call_args.args[1].endswith("?wait=true"))
        http.request.return_value = b""
        with self.assertRaises(RequestFailed):
            sender.send({})
