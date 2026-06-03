# KTMB Shuttle Tebrau — Automated Slot Checker

Checks [KTMB Shuttle Tebrau](https://shuttleonline.ktmb.com.my/Home/Shuttle) for available train slots and sends a Telegram notification when seats open up. Runs on GitHub Actions (free tier) every 15 minutes.

## Setup

1. **Create a Telegram bot** via @BotFather and note your `BOT_TOKEN` and `CHAT_ID`
2. **Add GitHub Secrets**: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
3. **Edit `searches.json`** with the dates/routes you want to monitor (up to 5)
4. **Push to GitHub** — the workflow triggers automatically on schedule

## Local development

```bash
pip install -r requirements.txt
python checker.py
```

## Configuration

Edit `searches.json` to change what to monitor. See the guide for field descriptions.

## Important

- `checker.py` uses browserless HTTP requests. If KTMB changes the hidden fields or trip table markup, update the request/parsing flow.
- Automated access may violate KTMB's ToS. Use responsibly with reasonable polling intervals.
