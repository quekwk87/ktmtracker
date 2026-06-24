# KTMB Shuttle Tebrau — Automated Slot Checker

Checks [KTMB Shuttle Tebrau](https://shuttleonline.ktmb.com.my/Home/Shuttle) for available train slots and sends a Telegram notification when seats open up.

## Configure Searches

You only need to provide:

- `origin`: `WOODLANDS CIQ` or `JB SENTRAL`
- `date`: travel date in `DD/MM/YYYY`
- `pax`: passenger count, 1-6
- `preferred_times`: departure times like `["17:30", "18:45"]`; use `[]` for any time
- `enabled`: `true` or `false`

`destination` is derived automatically from `origin`, and `label` is generated automatically.

Example:

```json
{
  "origin": "WOODLANDS CIQ",
  "date": "21/06/2026",
  "pax": 2,
  "preferred_times": ["17:30", "18:45"],
  "enabled": true
}
```

## Run Locally

```bash
cd "/Users/weekiatquek/Documents - Weekiat's MacBook Air/ktmreader"
python3 -m pip install -r requirements.txt
python3 checker.py
```

## Run Manually From GitHub Without Editing Code

Go to your repo on GitHub:

```text
Actions -> KTMB Shuttle Checker -> Run workflow
```

Fill in:

- `origin`: choose `WOODLANDS CIQ` or `JB SENTRAL`
- `date`: for example `21/06/2026`
- `pax`: choose 1-6
- `preferred_times`: comma-separated, for example `17:30,18:45`; blank means any time

If you fill in `origin` and `date`, the manual form overrides `searches.json`.

## Scheduled Runs Without Redeploying Code

For automatic scheduled checks, put your search config in a GitHub repository Variable instead of editing `searches.json`.

Go to:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions -> Variables -> New repository variable
```

Create:

```text
Name: KTMB_SEARCHES_JSON
```

Value:

```json
{
  "searches": [
    {
      "origin": "WOODLANDS CIQ",
      "date": "21/06/2026",
      "pax": 2,
      "preferred_times": ["17:30", "18:45"],
      "enabled": true
    },
    {
      "origin": "JB SENTRAL",
      "date": "21/06/2026",
      "pax": 2,
      "preferred_times": ["11:30", "17:45", "19:00", "20:15"],
      "enabled": true
    }
  ]
}
```

Updating this Variable does not require a code commit or redeploy. The next scheduled workflow run will use it.

## Telegram Notifications

Add these as GitHub Actions secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Local test:

```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
python3 checker.py
```

## Important

- `checker.py` uses browserless HTTP requests. If KTMB changes hidden fields or trip table markup, update the request/parsing flow.
- Automated access may violate KTMB's ToS. Use responsibly with reasonable polling intervals.
