# KTMB Shuttle Tebrau — Automated Slot Checker

A guide to building a scheduled bot that checks [KTMB Shuttle Tebrau](https://shuttleonline.ktmb.com.my/Home/Shuttle) for available train slots and sends you a **Telegram notification** when seats open up.

Supports **up to 5 different searches per run** — different dates, directions, times, and pax counts — all configured in a single JSON file.

---

## Architecture Overview

### Recommended: browserless HTTP checker

The KTMB Shuttle page can be checked without Playwright for availability monitoring.

The practical flow is:

```
GitHub Actions / local cron
  └─> Reads searches.json
        └─> For each search:
              └─> requests.Session()
                    └─> GET /Home/Shuttle
                    └─> Extract hidden station data + CSRF token
                    └─> POST /ShuttleTrip
                    └─> Extract SearchData + FormValidationCode + new CSRF token
                    └─> POST /ShuttleTrip/Trip
                    └─> Parse returned trip table HTML
        └─> Sends ONE consolidated Telegram message with all findings
```

This is currently the preferred approach because the site returns the train list through a JSON endpoint after the initial form post:

```http
POST https://shuttleonline.ktmb.com.my/ShuttleTrip/Trip
```

The response JSON contains a `data` field with the trip table HTML. That table includes train service, departure, arrival, duration, available seats, fare, and login/select action text.

### Fallback: Playwright checker

Playwright is still useful if KTMB changes the flow, adds browser-only checks, or if you want a visible assisted booking flow later. For simple availability checks, start with the browserless HTTP flow.

```
GitHub Actions (cron schedule)
  └─> Reads searches.json (up to 5 search configs)
        └─> For each search:
              └─> Playwright (headless Chromium)
                    └─> Opens KTMB Shuttle page
                    └─> Fills in route, date, pax
                    └─> Reads available train slots
                    └─> Collects results
        └─> Sends ONE consolidated Telegram message with all findings
```

**Why this stack?**

| Concern | Choice | Reason |
|---|---|---|
| Availability checking | **requests + HTML parsing** | The search can be performed with HTTP calls and the result table is returned in JSON |
| Browser automation fallback | **Playwright (Python)** | Useful for debugging, future assisted booking, or if KTMB moves checks back into the browser |
| Scheduler | **GitHub Actions** | Free tier, runs in cloud, no server to maintain |
| Notification | **Telegram Bot** | Instant push notifications, trivial to set up |
| Configuration | **JSON file** | Easy to edit, version-controlled, no env var juggling |

---

## Prerequisites

- A **GitHub account** (free)
- A **Telegram account** (to receive alerts)
- **Python 3.10+** installed locally (for development/testing)
- Basic comfort with terminal commands

---

## Step 1 — Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts to name your bot
3. Copy the **Bot Token** (looks like `7123456789:AAF...`)
4. Send any message to your new bot, then visit:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
5. Find your **Chat ID** in the response JSON under `message.chat.id`

> Save both `BOT_TOKEN` and `CHAT_ID` — you'll add them as GitHub Secrets later.

---

## Step 2 — Project Setup

Create a new repo locally:

```bash
mkdir ktmb-checker && cd ktmb-checker
git init
```

### Directory structure

```
ktmb-checker/
├── .github/
│   └── workflows/
│       └── check-shuttle.yml    # GitHub Actions workflow
├── searches.json                # ⭐ Your search configurations (up to 5)
├── checker.py                   # Main checker script
├── requirements.txt             # Python dependencies
└── README.md
```

### requirements.txt

For the recommended browserless checker:

```txt
requests>=2.31.0
```

If you keep the Playwright fallback:

```txt
playwright==1.49.1
requests>=2.31.0
```

---

## Step 3 — Configure Your Searches

### searches.json

This is the file you edit whenever you want to change what to monitor. Each entry is one search — you can have up to 5.

```json
{
  "searches": [
    {
      "label": "Morning to Woodlands (Weekday)",
      "origin": "JB SENTRAL",
      "destination": "WOODLANDS CIQ",
      "date": "20/06/2026",
      "pax": 1,
      "preferred_times": ["06:00", "07:00", "08:00"],
      "enabled": true
    },
    {
      "label": "Evening return from Woodlands",
      "origin": "WOODLANDS CIQ",
      "destination": "JB SENTRAL",
      "date": "20/06/2026",
      "pax": 1,
      "preferred_times": ["17:00", "18:00", "19:00"],
      "enabled": true
    },
    {
      "label": "Weekend trip to JB",
      "origin": "WOODLANDS CIQ",
      "destination": "JB SENTRAL",
      "date": "21/06/2026",
      "pax": 2,
      "preferred_times": [],
      "enabled": true
    },
    {
      "label": "Weekend return from JB",
      "origin": "JB SENTRAL",
      "destination": "WOODLANDS CIQ",
      "date": "21/06/2026",
      "pax": 2,
      "preferred_times": ["18:00", "19:00", "20:00", "21:00"],
      "enabled": true
    },
    {
      "label": "Backup date",
      "origin": "JB SENTRAL",
      "destination": "WOODLANDS CIQ",
      "date": "27/06/2026",
      "pax": 1,
      "preferred_times": [],
      "enabled": false
    }
  ]
}
```

### Configuration fields

| Field | Required | Description |
|---|---|---|
| `label` | Yes | A friendly name for this search (shown in Telegram alerts) |
| `origin` | Yes | `"JB SENTRAL"` or `"WOODLANDS CIQ"` |
| `destination` | Yes | `"JB SENTRAL"` or `"WOODLANDS CIQ"` |
| `date` | Yes | Travel date in `DD/MM/YYYY` format |
| `pax` | Yes | Number of passengers, 1–6 |
| `preferred_times` | Yes | List of times like `["08:00", "17:00"]`. Empty list `[]` = any time |
| `enabled` | Yes | Set `false` to skip this search without deleting it |

> **To update your searches**: edit `searches.json`, commit, and push. The next scheduled run will use the new config.

---

## Step 4 — The Checker Script

### checker.py

> Note: the script below is the original Playwright-based version. Keep it as a fallback/reference, but the recommended next implementation is to replace the Playwright search execution with the browserless HTTP flow in Step 7.

```python
"""
KTMB Shuttle Tebrau — Multi-Search Availability Checker
Reads search configs from searches.json, checks each one via Playwright,
and sends a consolidated Telegram notification if any slots are found.
"""

import os
import sys
import json
import random
import requests as http_requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
KTMB_URL = "https://shuttleonline.ktmb.com.my/Home/Shuttle"
CONFIG_FILE = Path(__file__).parent / "searches.json"
MAX_SEARCHES = 5
SGT = timezone(timedelta(hours=8))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ──────────────────────────────────────────────
# Config Loader
# ──────────────────────────────────────────────
def load_searches() -> list[dict]:
    """Load and validate search configurations from searches.json."""
    if not CONFIG_FILE.exists():
        print(f"[ERROR] Config file not found: {CONFIG_FILE}")
        sys.exit(1)

    with open(CONFIG_FILE, "r") as f:
        data = json.load(f)

    searches = data.get("searches", [])

    # Filter to enabled only, skip past dates, cap at MAX_SEARCHES
    enabled = []
    today = datetime.now(SGT).date()
    for s in searches:
        if not s.get("enabled", True):
            continue
        try:
            search_date = datetime.strptime(s["date"], "%d/%m/%Y").date()
            if search_date < today:
                print(f"[SKIP] '{s.get('label', '?')}' — date {s['date']} has passed")
                continue
        except (ValueError, KeyError):
            pass
        enabled.append(s)

    if len(enabled) > MAX_SEARCHES:
        print(f"[WARN] {len(enabled)} searches enabled, capping at {MAX_SEARCHES}")
        enabled = enabled[:MAX_SEARCHES]

    # Validate required fields
    required_fields = ["label", "origin", "destination", "date", "pax"]
    for i, search in enumerate(enabled):
        for field in required_fields:
            if field not in search:
                print(f"[ERROR] Search #{i+1} missing required field: {field}")
                sys.exit(1)

    print(f"[INFO] Loaded {len(enabled)} search(es) from {CONFIG_FILE.name}")
    return enabled


# ──────────────────────────────────────────────
# Telegram Notification
# ──────────────────────────────────────────────
def send_telegram(message: str):
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram credentials not set. Printing to stdout instead.")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        resp = http_requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print("[OK] Telegram message sent.")
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")


# ──────────────────────────────────────────────
# Single Search Execution
# ──────────────────────────────────────────────
def run_single_search(page, search: dict, index: int) -> list[dict]:
    """
    Execute a single search on the KTMB Shuttle page.
    Returns a list of available slot dicts.
    """
    label = search["label"]
    origin = search["origin"]
    destination = search["destination"]
    date = search["date"]
    pax = str(search["pax"])
    preferred_times = search.get("preferred_times", [])

    print(f"\n{'='*60}")
    print(f"[SEARCH {index+1}] {label}")
    print(f"[INFO] {origin} → {destination} on {date}, {pax} pax")
    print(f"[INFO] Preferred times: {preferred_times or 'any'}")
    print(f"{'='*60}")

    try:
        # 1. Navigate to Shuttle page (fresh for each search)
        print("[INFO] Loading KTMB Shuttle page...")
        page.goto(KTMB_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(random.randint(1500, 3000))

        # 2. Select Origin
        #    ⚠️ SELECTOR TUNING REQUIRED — see Step 7
        print(f"[INFO] Selecting origin: {origin}")
        page.click("#FromStationInput", timeout=5000)
        page.wait_for_timeout(500)
        page.fill("#FromStationInput", origin)
        page.wait_for_timeout(500)
        page.locator(f"text={origin}").first.click(timeout=5000)

        # 3. Select Destination
        #    ⚠️ SELECTOR TUNING REQUIRED
        print(f"[INFO] Selecting destination: {destination}")
        page.click("#ToStationInput", timeout=5000)
        page.wait_for_timeout(500)
        page.fill("#ToStationInput", destination)
        page.wait_for_timeout(500)
        page.locator(f"text={destination}").first.click(timeout=5000)

        # 4. Set date
        #    ⚠️ SELECTOR TUNING REQUIRED
        print(f"[INFO] Setting date: {date}")
        date_input = page.locator("#DepartDate")
        date_input.click()
        date_input.fill(date)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        # 5. Set pax
        #    ⚠️ SELECTOR TUNING REQUIRED
        print(f"[INFO] Setting pax: {pax}")
        page.select_option("select#Rone", value=pax)

        # 6. Click Search
        print("[INFO] Clicking Search...")
        page.click("button:has-text('SEARCH')", timeout=5000)

        # 7. Wait for results
        print("[INFO] Waiting for results...")
        page.wait_for_timeout(random.randint(4000, 6000))

        # 8. Debug screenshot
        screenshot_name = f"search_{index+1}_{origin.replace(' ', '_')}_{date.replace('/', '-')}.png"
        page.screenshot(path=screenshot_name)
        print(f"[INFO] Screenshot saved: {screenshot_name}")

        # 9. Parse results
        results = parse_results(page)

        if not results:
            print("[INFO] No available slots found.")
            return []

        # 10. Filter by preferred times
        if preferred_times:
            results = [
                r for r in results
                if any(t in r.get("depart", "") for t in preferred_times)
            ]
            if not results:
                print("[INFO] Slots exist but none match preferred times.")
                return []

        print(f"[INFO] Found {len(results)} matching slot(s)!")

        # Tag results with search metadata
        for r in results:
            r["_label"] = label
            r["_origin"] = origin
            r["_destination"] = destination
            r["_date"] = date
            r["_pax"] = pax

        return results

    except PlaywrightTimeout as e:
        print(f"[ERROR] Timeout during search '{label}': {e}")
        page.screenshot(path=f"error_search_{index+1}.png")
        return []
    except Exception as e:
        print(f"[ERROR] Failed search '{label}': {e}")
        page.screenshot(path=f"error_search_{index+1}.png")
        return []


def parse_results(page) -> list[dict]:
    """
    Parse the search results page for available train slots.

    ⚠️ IMPORTANT: These selectors are STARTING POINTS. You must inspect
    the actual KTMB results page and adjust them. See Step 7.
    """
    available = []

    # Try multiple possible selectors for result rows
    rows = page.locator(
        ".trip-list .trip-item, "
        "table.schedule tbody tr, "
        ".shuttle-result-item, "
        ".result-row"
    ).all()

    if not rows:
        body_text = page.inner_text("body")
        if "no train" in body_text.lower() or "not available" in body_text.lower():
            return []
        print(f"[DEBUG] Could not find result rows. Page text excerpt:\n{body_text[:1000]}")
        return []

    for row in rows:
        try:
            text = row.inner_text()
            slot = {
                "raw_text": text.strip(),
                "depart": "",    # e.g. "08:00"  — fill in after tuning selectors
                "arrive": "",    # e.g. "08:30"
                "status": "",    # e.g. "Available"
                "price": "",     # e.g. "RM 5.00"
            }
            if "sold out" not in text.lower() and "not available" not in text.lower():
                available.append(slot)
        except Exception:
            continue

    return available


# ──────────────────────────────────────────────
# Message Formatting
# ──────────────────────────────────────────────
def format_telegram_message(all_results: list[dict]) -> str:
    """Format results from all searches into one Telegram message."""
    now_sgt = datetime.now(SGT).strftime("%Y-%m-%d %H:%M SGT")

    lines = [
        "🚂 *KTMB Shuttle — Slots Found!*",
        f"⏰ Checked at: {now_sgt}",
        "",
    ]

    # Group results by search label
    grouped = defaultdict(list)
    for r in all_results:
        grouped[r["_label"]].append(r)

    for label, slots in grouped.items():
        first = slots[0]
        lines.append(f"*{label}*")
        lines.append(f"📍 {first['_origin']} → {first['_destination']}")
        lines.append(f"📅 {first['_date']} | 👤 {first['_pax']} pax")
        for s in slots:
            if s.get("depart"):
                lines.append(f"  ✅ {s['depart']} → {s['arrive']}  {s['status']}  {s['price']}")
            else:
                lines.append(f"  ✅ {s['raw_text'][:80]}")
        lines.append("")

    lines.append(f"🔗 [Book now]({KTMB_URL})")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# Main Orchestrator
# ──────────────────────────────────────────────
def main():
    searches = load_searches()

    if not searches:
        print("[INFO] No enabled searches found. Exiting.")
        return

    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        for i, search in enumerate(searches):
            results = run_single_search(page, search, i)
            all_results.extend(results)

            # Polite delay between searches
            if i < len(searches) - 1:
                delay = random.randint(3000, 6000)
                print(f"[INFO] Waiting {delay}ms before next search...")
                page.wait_for_timeout(delay)

        browser.close()

    # Send consolidated notification
    print(f"\n{'='*60}")
    print(f"[SUMMARY] Total matching slots found: {len(all_results)}")
    print(f"{'='*60}")

    if all_results:
        msg = format_telegram_message(all_results)
        send_telegram(msg)
    else:
        print("[INFO] No available slots across all searches. No notification sent.")


# ──────────────────────────────────────────────
if __name__ == "__main__":
    main()
```

---

## Step 5 — GitHub Actions Workflow

### .github/workflows/check-shuttle.yml

```yaml
name: KTMB Shuttle Checker

on:
  schedule:
    # Runs every 15 minutes during waking hours (SGT = UTC+8)
    # SGT 06:00–23:59 = UTC 22:00–23:59 and 00:00–15:59
    - cron: "*/15 22-23 * * *"
    - cron: "*/15 0-15 * * *"

  workflow_dispatch:  # Allow manual trigger from GitHub UI

env:
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run checker
        run: python checker.py
```

If you use the Playwright fallback, add these install lines back:

```yaml
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install chromium
          playwright install-deps
```

---

## Step 6 — GitHub Secrets Setup

In your GitHub repo, go to **Settings → Secrets and variables → Actions** and add:

| Secret Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your personal chat ID |

---

## Step 7 — Browserless HTTP Flow

The checker can avoid Playwright by reproducing the site's normal request flow with `requests.Session`.

### Request sequence

1. `GET https://shuttleonline.ktmb.com.my/Home/Shuttle`
   - Save cookies in the session.
   - Extract:
     - `FromStationData`
     - `ToStationData`
     - `FromStationId`
     - `ToStationId`
     - `__RequestVerificationToken`

2. `POST https://shuttleonline.ktmb.com.my/ShuttleTrip`
   - Send the form fields as `application/x-www-form-urlencoded`.
   - Use date format like `20 Jun 2026`.
   - Include `PassengerCount`.
   - Keep the same cookies/session.

3. Parse the returned `/ShuttleTrip` page and extract:
   - `SearchData`
   - `FormValidationCode`
   - the new `__RequestVerificationToken`
   - the trip endpoint, currently `/ShuttleTrip/Trip`

4. `POST https://shuttleonline.ktmb.com.my/ShuttleTrip/Trip`
   - Send JSON:
     ```json
     {
       "SearchData": "<value from ShuttleTrip page>",
       "FormValidationCode": "<value from ShuttleTrip page>",
       "DepartDate": "2026-06-20",
       "IsReturn": false,
       "BookingTripSequenceNo": 1
     }
     ```
   - Add header:
     ```http
     RequestVerificationToken: <token from ShuttleTrip page>
     ```

5. Parse the JSON response:
   - `status == true` means the request succeeded.
   - `data` contains an HTML table of train rows.
   - Each row includes train service, departure time, arrival time, duration, available seats, fare, and a login/select action.

### Example parsed fields

```text
05:00 -> 05:05 | Shuttle Tebrau - 61 | seats=308 | MYR 5.00
05:30 -> 05:35 | Shuttle Tebrau - 63 | seats=301 | MYR 5.00
06:00 -> 06:05 | Shuttle Tebrau - 65 | seats=301 | MYR 5.00
07:00 -> 07:05 | Shuttle Tebrau - 69 | seats=295 | MYR 5.00
08:45 -> 08:50 | Shuttle Tebrau - 73 | seats=182 | MYR 5.00
```

### Notes for booking

Availability can be checked without login. Booking is different: unauthenticated results show `Login to view`, and the booking flow uses reserve/login endpoints plus recaptcha-related logic. Treat booking as a human-confirmed assisted flow for now:

- Send a Telegram alert when a matching slot appears.
- Include route, date, pax, times, available seats, and fare.
- Open the KTMB page for manual login/payment.
- Avoid silent auto-purchase.

### Playwright fallback debugging

If the browserless flow breaks, use Playwright only as a diagnostic fallback:

```bash
pip install -r requirements.txt
playwright install chromium
python checker.py
```

Useful Playwright debug helpers:

```python
page.pause()
page.screenshot(path="debug.png")
print(page.content())
print(page.inner_text("body"))
```

---

## Step 8 — Deploy

```bash
git add -A
git commit -m "Initial KTMB shuttle checker"
git remote add origin https://github.com/<you>/ktmb-checker.git
git push -u origin main
```

The workflow runs on the cron schedule automatically. Trigger it manually from **Actions → KTMB Shuttle Checker → Run workflow** to test.

---

## Development Workflow with Claude Code

The recommended way to develop and debug this project is with **Claude Code**:

### Initial setup

```bash
# 1. Create the project folder and files from this guide
mkdir -p ktmb-checker/.github/workflows
cd ktmb-checker

# 2. Create the files (copy from this guide):
#    - searches.json
#    - checker.py
#    - requirements.txt
#    - .github/workflows/check-shuttle.yml

# 3. Install dependencies
pip install requests

# 4. Open Claude Code in the project directory
claude
```

### Browserless checker development with Claude Code

This is where Claude Code really shines. The workflow:

1. **Run one search locally and save the raw responses**:
   ```
   > Help me convert checker.py to use requests.Session and save the ShuttleTrip response for debugging.
   ```

2. **Parse the returned trip table**:
   ```
   > Here's the /ShuttleTrip/Trip JSON response. Help me parse service, departure, arrival, seats, and fare.
   ```

3. **Iterate** — Claude Code can update the parser, re-run, and verify against saved HTML/JSON samples.

4. **Test the Telegram notification** — set env vars locally and run:
   ```bash
   export TELEGRAM_BOT_TOKEN="your_token_here"
   export TELEGRAM_CHAT_ID="your_chat_id_here"
   python checker.py
   ```

### What to ask Claude Code for help with

- "Convert checker.py from Playwright to requests using the browserless flow in Step 7"
- "Parse these /ShuttleTrip/Trip result rows and extract departure time, arrival time, seats, and fare"
- "Add state so I only get notified once per newly available slot"
- "Add error retry logic for flaky page loads"
- "Help me set up the GitHub Actions workflow and push to my repo"

---

## Everyday Usage

### Changing your searches

Edit `searches.json` and push:

```bash
git add searches.json
git commit -m "Update search dates"
git push
```

### Temporarily disabling a search

Set `"enabled": false` — no need to delete the entry.

### Expired dates

Searches with past dates are automatically skipped (logged as `[SKIP]`).

### Testing immediately

GitHub → **Actions** → **KTMB Shuttle Checker** → **Run workflow**.

### Example Telegram notification

```
🚂 KTMB Shuttle — Slots Found!
⏰ Checked at: 2026-06-15 08:30 SGT

Morning to Woodlands (Weekday)
📍 JB SENTRAL → WOODLANDS CIQ
📅 20/06/2026 | 👤 1 pax
  ✅ 07:00 → 07:30  Available  RM 5.00
  ✅ 08:00 → 08:30  Available  RM 5.00

Weekend trip to JB
📍 WOODLANDS CIQ → JB SENTRAL
📅 21/06/2026 | 👤 2 pax
  ✅ 10:00 → 10:30  Available  RM 5.00

🔗 Book now
```

---

## Resource Usage

The browserless checker should be much lighter than Playwright. A run with 5 searches will usually be closer to seconds than minutes, but keep polite delays between searches so the site is not hammered.

| Scenario | Runs/day | Minutes/day | Monthly usage |
|---|---|---|---|
| Every 15 min, 6am–11pm SGT | ~68 | Low with browserless checker | Usually fine |
| Every 30 min, 6am–11pm SGT | ~34 | Lower | Safer/politer |
| Every 15 min, peak hours only | ~24 | Lowest | Best free-tier margin |

GitHub Actions free tier = **2,000 min/month**. The browserless checker helps a lot, but GitHub scheduled workflows can still be delayed. To be polite and reliable, prefer fewer searches, a reasonable interval, and peak-hour windows.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Initial page loads but trip API fails | Check that cookies, `__RequestVerificationToken`, `SearchData`, and `FormValidationCode` are carried across requests |
| No rows parsed | Save the `data` HTML from `/ShuttleTrip/Trip` and inspect whether KTMB changed the trip table markup |
| Telegram not sending | Verify `BOT_TOKEN` and `CHAT_ID`; ensure you've messaged the bot first |
| GitHub Actions not running | Check cron syntax; GitHub may delay runs by a few minutes |
| CAPTCHA or login appears | Keep availability checking unauthenticated; use manual login for booking |
| Site structure changed | Re-inspect the returned HTML and update the parser |
| Exceeding free tier | Reduce frequency or restrict to peak hours |

---

## Important Notes

- **Terms of Service**: Automated access may violate KTMB's ToS. Use responsibly with reasonable polling intervals.
- **Fragility**: Web scraping is inherently brittle. Site updates can break hidden-field extraction or trip table parsing — plan for occasional maintenance.
- **No silent auto-booking**: This script should only *check* availability and help you act quickly. Login, passenger confirmation, and payment should remain manual unless you have confirmed the site's rules and reliability.
- **Config is version-controlled**: `searches.json` changes are tracked in git history.
