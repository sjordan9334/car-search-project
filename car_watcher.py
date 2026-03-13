from playwright.sync_api import sync_playwright
#!/usr/bin/env python3
import argparse
import json
import logging
import re
import sqlite3
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import smtplib


@dataclass
class Listing:
    search_name: str
    listing_id: str
    title: str
    price: int | None
    mileage: int | None
    url: str
    seller_type: str | None
    seller_quality_score: int
    seller_quality_reason: str
    deal_score: int


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_listings (
            listing_id TEXT PRIMARY KEY,
            search_name TEXT NOT NULL,
            title TEXT,
            price INTEGER,
            mileage INTEGER,
            url TEXT,
            seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn


def fetch_html(url: str, user_agent: str) -> str:

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1280, "height": 900}
        )

        page = context.new_page()

        page.goto(url, timeout=60000)

        # allow dynamic listings to load
        page.wait_for_timeout(5000)

        html = page.content()

        browser.close()

        return html


def _normalize_price(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        nums = re.sub(r"[^0-9]", "", value)
        return int(nums) if nums else None
    return None


def _normalize_mileage(value: Any) -> int | None:
    return _normalize_price(value)


def _collect_text(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return " ".join(_collect_text(v) for v in node.values())
    if isinstance(node, list):
        return " ".join(_collect_text(x) for x in node)
    return ""


def _extract_script_blocks(html: str, script_id: str | None = None, script_type: str | None = None) -> list[str]:
    attrs = []
    if script_id:
        attrs.append(rf"id=[\"']{re.escape(script_id)}[\"']")
    if script_type:
        attrs.append(rf"type=[\"']{re.escape(script_type)}[\"']")

    attr_pattern = "(?=.*" + ")(?=.*".join(attrs) + ")" if attrs else ""
    pattern = rf"<script{attr_pattern}[^>]*>(.*?)</script>"
    return re.findall(pattern, html, flags=re.DOTALL | re.IGNORECASE)


def extract_listings(html: str, base_url: str, search_name: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for raw_json in _extract_script_blocks(html, script_type="application/ld+json"):
        text = raw_json.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue

        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") in {"Product", "Vehicle"}:
                offer = node.get("offers", {}) if isinstance(node.get("offers"), dict) else {}
                url = node.get("url")
                if url:
                    url = urljoin(base_url, url)
                candidates.append(
                    {
                        "id": node.get("sku") or node.get("mpn") or url,
                        "title": node.get("name") or "Untitled",
                        "price": _normalize_price(offer.get("price") or node.get("price")),
                        "mileage": _normalize_mileage(node.get("mileageFromOdometer")),
                        "url": url,
                        "seller_type": None,
                        "description": _collect_text(node.get("description", "")),
                    }
                )

    for block in _extract_script_blocks(html, script_id="__NEXT_DATA__"):
        try:
            parsed = json.loads(block)
            blob = _collect_text(parsed)
            listing_urls = set(re.findall(r"https?://[^\s\"']+", blob))
            for link in listing_urls:
                if any(domain in link for domain in ["cars.com", "ebay", "autotrader", "craigslist", "carvana", "autotempest"]):
                    candidates.append(
                        {
                            "id": link,
                            "title": f"Listing from {search_name}",
                            "price": None,
                            "mileage": None,
                            "url": link,
                            "seller_type": None,
                            "description": "",
                        }
                    )
        except json.JSONDecodeError:
            continue

    deduped: dict[str, dict[str, Any]] = {}
    for c in candidates:
        cid = c.get("id") or c.get("url")
        if not cid:
            continue
        c["id"] = str(cid)
        c["url"] = c.get("url") or base_url
        deduped[c["id"]] = c

    return list(deduped.values())


def compute_deal_score(listing: dict[str, Any], search_cfg: dict[str, Any]) -> int:
    score = 0
    price = listing.get("price")
    mileage = listing.get("mileage")
    target_price = search_cfg.get("target_price")
    max_price = search_cfg.get("max_price")
    max_mileage = search_cfg.get("max_mileage")

    if price is not None:
        score += 20
        if target_price:
            pct_below = (target_price - price) / target_price * 100
            if pct_below >= 15:
                score += 40
            elif pct_below >= 8:
                score += 28
            elif pct_below >= 3:
                score += 18
            elif pct_below >= 0:
                score += 10
            else:
                score += max(-10, int(pct_below))
        elif max_price:
            if price <= max_price:
                score += 25
            else:
                over = (price - max_price) / max_price * 100
                score -= int(over)
    else:
        score -= 5

    if mileage is not None:
        score += 15
        if max_mileage:
            if mileage <= max_mileage:
                score += 15
            else:
                over = (mileage - max_mileage) / max_mileage * 100
                score -= min(20, int(over))

    title = (listing.get("title") or "").strip()
    if len(title) > 8:
        score += 5

    return max(0, min(100, score))


def compute_seller_quality(listing: dict[str, Any]) -> tuple[int, str]:
    text = " ".join(
        [
            listing.get("title") or "",
            listing.get("description") or "",
            listing.get("seller_type") or "",
        ]
    ).lower()

    positive = ["maintenance records", "clean title", "single owner", "one owner", "service history", "non smoker", "garage kept"]
    negative = ["salvage", "rebuilt", "as-is", "no title", "flood", "frame damage", "mechanic special"]

    score = 50
    pos_hits = [p for p in positive if p in text]
    neg_hits = [n for n in negative if n in text]

    score += len(pos_hits) * 8
    score -= len(neg_hits) * 14

    seller_type = (listing.get("seller_type") or "").lower()
    if "dealer" in seller_type:
        score += 4
    if "private" in seller_type:
        score += 6

    if listing.get("description") and len(listing["description"]) > 100:
        score += 6

    score = max(0, min(100, score))
    reason = f"+{len(pos_hits)} positive signals, -{len(neg_hits)} risk signals"
    return score, reason


def is_great_deal(listing: Listing, search_cfg: dict[str, Any]) -> bool:

    min_deal_score = search_cfg.get("min_deal_score", 70)
    target_price = search_cfg.get("target_price")

    if listing.deal_score >= min_deal_score:
        return True

    if target_price and listing.price is not None:

        pct_below = (target_price - listing.price) / target_price * 100

        # steal deal
        if pct_below >= search_cfg.get("steal_price_discount_pct", 20):
            return True

        # normal good deal
        if pct_below >= search_cfg.get("great_price_discount_pct", 10):
            return True

    return False    min_deal_score = search_cfg.get("min_deal_score", 70)
    if listing.deal_score >= min_deal_score:
        return True

    target_price = search_cfg.get("target_price")
    discount_pct = search_cfg.get("great_price_discount_pct", 12)
    if target_price and listing.price is not None:
        pct_below = (target_price - listing.price) / target_price * 100
        return pct_below >= discount_pct
    return False


def listing_already_seen(conn: sqlite3.Connection, listing_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_listings WHERE listing_id = ? LIMIT 1", (listing_id,)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, listing: Listing) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO seen_listings
        (listing_id, search_name, title, price, mileage, url)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (listing.listing_id, listing.search_name, listing.title, listing.price, listing.mileage, listing.url),
    )
    conn.commit()


def send_email_alert(smtp_cfg: dict[str, Any], subject: str, body: str) -> None:
    if not smtp_cfg:
        logging.warning("No SMTP configuration found; skipping notification")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_cfg["from_email"]
    msg["To"] = ", ".join(smtp_cfg["to"])
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg.get("port", 587), timeout=30) as server:
        server.starttls(context=context)
        server.login(smtp_cfg["username"], smtp_cfg["password"])
        server.send_message(msg)


def format_alert(listing: Listing) -> str:
    return (
        f"Search: {listing.search_name}\n"
        f"Title: {listing.title}\n"
        f"Price: {listing.price if listing.price is not None else 'unknown'}\n"
        f"Mileage: {listing.mileage if listing.mileage is not None else 'unknown'}\n"
        f"Deal score: {listing.deal_score}/100\n"
        f"Seller quality: {listing.seller_quality_score}/100 ({listing.seller_quality_reason})\n"
        f"URL: {listing.url}\n"
    )


def run_monitor(config: dict[str, Any]) -> int:
    conn = init_db(config.get("state_db", "./watcher_state.db"))
    user_agent = config.get("user_agent", "Mozilla/5.0")
    smtp_cfg = (config.get("notifications") or {}).get("smtp")

    total_alerts = 0

    for search in config.get("searches", []):
        name = search["name"]
        url = search["url"]
        logging.info("Checking %s", name)

        try:
            html = fetch_html(url, user_agent)
            raw_listings = extract_listings(html, url, name)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed to fetch/parse search %s: %s", name, exc)
            continue

        logging.info("Found %d candidate listings for %s", len(raw_listings), name)

        for raw in raw_listings:
            listing_id = raw.get("id")
            if not listing_id or listing_already_seen(conn, listing_id):
                continue

            deal_score = compute_deal_score(raw, search)
            seller_score, seller_reason = compute_seller_quality(raw)

            listing = Listing(
                search_name=name,
                listing_id=listing_id,
                title=raw.get("title") or "Untitled",
                price=raw.get("price"),
                mileage=raw.get("mileage"),
                url=raw.get("url") or url,
                seller_type=raw.get("seller_type"),
                seller_quality_score=seller_score,
                seller_quality_reason=seller_reason,
                deal_score=deal_score,
            )

            mark_seen(conn, listing)

            if is_great_deal(listing, search):
                try:
                    subject = f"🚗 ${listing.price} {listing.title}"
                    send_email_alert(smtp_cfg, subject, format_alert(listing))     
                    total_alerts += 1
                except Exception as exc:  # noqa: BLE001
                    logging.exception("Failed to send alert for %s: %s", listing.listing_id, exc)

    return total_alerts


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoTempest saved-search monitor")
    parser.add_argument("--config", default="config.json", help="Path to JSON config")
    parser.add_argument("--run-once", action="store_true", help="Run once then exit")
    parser.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"Config file not found: {config_path}")

    config = load_config(str(config_path))
    schedule_hours = int(config.get("schedule_hours", 24))

    if args.run_once:
        alerts = run_monitor(config)
        logging.info("Run complete. Alerts sent: %d", alerts)
        return

    while True:
        alerts = run_monitor(config)
        logging.info("Cycle complete. Alerts sent this cycle: %d. Sleeping %d hour(s).", alerts, schedule_hours)
        time.sleep(schedule_hours * 3600)


if __name__ == "__main__":
    main()
