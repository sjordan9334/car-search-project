 AutoTempest Deal Watcher

A Python program that monitors your saved AutoTempest search URLs, remembers seen listings, and sends alerts when it finds a strong deal.

## What it does

- Refreshes any number of saved search URLs on a schedule (daily by default).
- Extracts listing info (title, price, mileage, link, seller hints) from page JSON payloads.
- Scores listings based on configurable deal rules.
- Calculates a seller quality score from available metadata and text clues.
- Sends alerts to multiple recipients (e.g., you + your wife) via SMTP email.
- Stores previously seen listings in SQLite so you only get notified on new matches.

## Quick start

1. **Copy config and update your 3 search links**

   ```bash
   cp config.example.json config.json
   ```

2. **Run once (for testing)**

   ```bash
   python car_watcher.py --config config.json --run-once
   ```

3. **Run daily loop**

   ```bash
   python car_watcher.py --config config.json
   ```

## Deal detection

Each listing gets a `deal_score` based on:

- Price compared to your configured `max_price` or `target_price`.
- Mileage compared to optional `max_mileage`.
- Bonus for complete listing fields.

A listing triggers an alert when:

- `deal_score >= min_deal_score`, or
- price is at least `great_price_discount_pct` under your `target_price`.

## Seller quality estimation

Seller quality is a heuristic 0-100 score using signals such as:

- Positive terms (e.g., `maintenance records`, `single owner`, `clean title`).
- Negative terms (e.g., `salvage`, `rebuilt`, `as-is`, `no title`).
- Seller type hints (`dealer`, `private`).
- Presence of detailed listing descriptions.

## Scheduling options

- Built-in loop (default): sleeps `schedule_hours` between runs.
- Or run with cron/systemd daily and pass `--run-once`.

Example cron (daily 8:00 AM):

```cron
0 8 * * * /usr/bin/python /path/to/car_watcher.py --config /path/to/config.json --run-once
```
