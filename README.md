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

## Configure With the Local UI

The project includes a small local configuration page. It edits `searches.json` for you, so you do not need to edit JSON by hand.

```bash
cd "/Users/weekiatquek/Documents - Weekiat's MacBook Air/ktmreader"
python3 config_ui.py
```

The page opens at `http://127.0.0.1:8000`. Add or remove searches, choose the origin, date, passenger count, and preferred departure times, then select **Save searches**. The checker will use the saved configuration the next time you run `python3 checker.py` locally.

This local page updates the local `searches.json` only. Commit and push it to `main` so scheduled checks use it.

## Host the Configuration UI on GitHub Pages

The repository also contains a static version of the configuration page under `pages/`. The `Deploy KTMB Configuration UI` workflow publishes it automatically when `pages/index.html` changes.

After pushing the workflow, set the repository's Pages source to **GitHub Actions** under:

```text
GitHub repo -> Settings -> Pages -> Build and deployment -> Source -> GitHub Actions
```

The hosted page stores its draft in your browser. Use **Download JSON** or **Copy JSON**, then replace the contents of `searches.json` in the repo and commit it. GitHub Pages cannot write to the repo without a separate backend or a GitHub token.

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

## Scheduled Runs

Scheduled GitHub Actions runs read `searches.json` from `main`. To change what is checked, edit `searches.json` and commit it (GitHub's web editor works). That file is the only saved config; there is no repository variable.

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
