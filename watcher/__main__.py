import argparse
from dataclasses import replace
from datetime import datetime, timezone
import logging
import os
from pathlib import Path

from .discord import Discord, notification
from .history import GitHubHistory, LocalHistory
from .http import HTTP, RequestFailed
from .rules import load_config
from .yahoo import Yahoo, confirmed_shipping, matches_detail, prices


log = logging.getLogger("watcher")


def run(config, yahoo, history_store, send=None, *, now=None):
    now = now or datetime.now(timezone.utc)
    history = history_store.load()
    listings = {}
    for query in dict.fromkeys(watch.query for watch in config.watches):
        results = yahoo.search(query, config.search_pages)
        log.info("検索 %s: タイヤカテゴリ内 %s 件", query, len(results))
        for listing in results:
            listings.setdefault(listing.id, listing)

    if send is not None and not history.initialized and config.first_run == "skip":
        next_history = replace(history, initialized=True, skip_before=now)
        history_store.save(next_history)
        history = next_history
        log.info("初回の既存出品を除外します（出品時刻の基準 %s）", now.isoformat())
        # Listings started during the scan are still eligible below.

    candidates = []
    for listing in listings.values():
        if listing.end <= now or history.excludes(listing.id, listing.start):
            continue
        watches = [watch for watch in config.watches if watch.matches_title(listing.title)]
        if watches:
            candidates.append((listing, watches))

    # Unknown paid rates cannot block known free-shipping candidates.
    candidates.sort(key=lambda entry: (entry[0].free_shipping, entry[0].start), reverse=True)
    failures = matches = 0
    for listing, watches in candidates[:config.max_details]:
        try:
            item = yahoo.detail(listing)
        except RequestFailed as error:
            if error.status in (403, 429):
                raise
            if error.status == 404:
                log.info("%s: 商品ページなし", listing.id)
                continue
            log.warning("%s: %s", listing.id, error)
            failures += 1
            continue
        except ValueError as error:
            log.warning("%s: %s", listing.id, error)
            failures += 1
            continue
        for watch in watches:
            if not matches_detail(item, watch):
                continue
            quote = confirmed_shipping(item, listing, watch.prefecture)
            shipping = quote.total(watch.prefecture, watch.count) if quote else None
            if shipping is None:
                log.info("%s: %s向け送料は不明", listing.id, watch.prefecture)
                continue
            current, buy_now = prices(item, listing.fixed_price)
            if not any(value is not None and value + shipping <= watch.max_total_yen
                       for value in (current, buy_now)):
                continue
            matches += 1
            if send is not None:
                try:
                    send(notification(listing, watch, shipping, current, buy_now))
                except RequestFailed:
                    log.error("%s: Discord の配信を確認できません。履歴に記録しません", listing.id)
                    raise
                next_history = replace(history.record(listing.id), initialized=True)
                history_store.save(next_history)
                history = next_history
            log.info("%s: %s", listing.id, "通知済み" if send is not None else "条件に一致（試行）")
            break
    if send is not None and not history.initialized and not failures:
        history_store.save(replace(history, initialized=True))
    log.info("集計 検索ID=%s 詳細候補=%s 詳細対象=%s 一致=%s 詳細エラー=%s",
             len(listings), len(candidates), min(len(candidates), config.max_details), matches, failures)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Yahoo!オークション中古タイヤ監視")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--dry-run", action="store_true", help="Discordへの送信と履歴の書き込みを省略")
    parser.add_argument("--github-state", action="store_true", help="GitHubの既定ブランチの履歴を使う")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        http = HTTP()
        history = (GitHubHistory(http, os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REF_NAME"],
                                os.environ["GITHUB_TOKEN"]) if args.github_state
                   else LocalHistory(Path("state.local.json")))
        discord = None if args.dry_run else Discord(http, os.environ.get("DISCORD_WEBHOOK_URL", ""))
        return run(config, Yahoo(http), history, discord.send if discord else None)
    except (RuntimeError, ValueError, KeyError, OSError, TypeError) as error:
        log.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
