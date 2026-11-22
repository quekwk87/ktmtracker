"""
Telegram command handling for the KTMB checker.

There is no always-on server: each scheduled GitHub Actions run (every ~15 min)
reads new Telegram messages with getUpdates, applies the commands, and saves
the result to searches.json / bot_state.json, which the workflow commits back.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


SGT = timezone(timedelta(hours=8))
ROOT = Path(__file__).parent
SEARCHES_FILE = ROOT / "searches.json"
STATE_FILE = ROOT / "bot_state.json"
REQUEST_TIMEOUT_SECONDS = 30
DAILY_SUMMARY_HOUR_SGT = 8

STATION_ALIASES = {
    "WL": "WOODLANDS CIQ",
    "WOODLANDS": "WOODLANDS CIQ",
    "SG": "WOODLANDS CIQ",
    "JB": "JB SENTRAL",
    "JBS": "JB SENTRAL",
}
OTHER_STATION = {"WOODLANDS CIQ": "JB SENTRAL", "JB SENTRAL": "WOODLANDS CIQ"}

HELP_TEXT = """KTMB Shuttle checker commands

/add <from> <date> <pax> [times]
  from: wl (Woodlands -> JB) or jb (JB -> Woodlands)
  date: DD/MM/YYYY or a range DD/MM/YYYY-DD/MM/YYYY
  times: optional, e.g. 17:30 18:45 (blank = any time)
  e.g. /add wl 04/12/2026 5
  e.g. /add jb 06/12/2026-07/12/2026 5 17:30 18:45

/list - show searches
/remove <n> - remove search number n (see /list)
/remove all - remove every search
/pause - stop searching (searches are kept)
/resume - start searching again
/status - what is being searched now

Commands are picked up on the next check run (about every 15 min)."""


# ---------- storage ----------

def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_state() -> dict:
    state = load_json(STATE_FILE, {})
    state.setdefault("update_offset", 0)
    state.setdefault("paused", False)
    state.setdefault("last_summary_date", "")
    return state


def load_search_list() -> list[dict]:
    return load_json(SEARCHES_FILE, {"searches": []}).get("searches", [])


def save_search_list(searches: list[dict]) -> None:
    save_json(SEARCHES_FILE, {"searches": searches})


# ---------- telegram api ----------

def api(token: str, method: str, **params):
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json().get("result")


def reply(token: str, chat_id: str, text: str) -> None:
    try:
        api(token, "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
    except Exception as exc:
        print(f"[ERROR] Telegram reply failed: {exc}")


# ---------- formatting ----------

def describe(search: dict) -> str:
    origin = search["origin"]
    times = ", ".join(search.get("preferred_times") or []) or "any time"
    flag = "" if search.get("enabled", True) else " (disabled)"
    return f"{origin} -> {OTHER_STATION.get(origin, '?')} | {search['date']} | {search['pax']} pax | {times}{flag}"


def list_text(searches: list[dict]) -> str:
    if not searches:
        return "No searches set. Use /add to add one."
    return "\n".join(f"{i}. {describe(s)}" for i, s in enumerate(searches, 1))


def status_text(searches: list[dict], paused: bool, header: str = "KTMB checker status") -> str:
    state_line = "PAUSED - not searching (/resume to restart)" if paused else "ACTIVE - checking about every 15 min"
    return f"{header}\n{state_line}\n\n{list_text(searches)}"


# ---------- commands ----------

def parse_date(value: str):
    return datetime.strptime(value, "%d/%m/%Y").date()


def expand_dates(value: str) -> list[str]:
    if "-" in value:
        start_s, end_s = value.split("-", 1)
        start, end = parse_date(start_s), parse_date(end_s)
        if end < start:
            raise ValueError("range end is before start")
        if (end - start).days > 13:
            raise ValueError("range is too long (max 14 days)")
        days = (end - start).days + 1
        return [(start + timedelta(days=d)).strftime("%d/%m/%Y") for d in range(days)]
    return [parse_date(value).strftime("%d/%m/%Y")]


def cmd_add(args: list[str], searches: list[dict], max_searches: int) -> str:
    if len(args) < 3:
        return "Usage: /add <wl|jb> <DD/MM/YYYY[-DD/MM/YYYY]> <pax> [times]\ne.g. /add wl 04/12/2026 5"
    origin = STATION_ALIASES.get(args[0].upper())
    if not origin:
        return f"Unknown station '{args[0]}'. Use wl (from Woodlands) or jb (from JB Sentral)."
    try:
        dates = expand_dates(args[1])
    except ValueError as exc:
        return f"Bad date '{args[1]}': {exc}. Use DD/MM/YYYY."
    today = datetime.now(SGT).date()
    if any(parse_date(d) < today for d in dates):
        return "That date has already passed."
    try:
        pax = int(args[2])
    except ValueError:
        return "pax must be a number 1-6."
    if not 1 <= pax <= 6:
        return "pax must be between 1 and 6."
    times = []
    for t in args[3:]:
        for part in t.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                times.append(datetime.strptime(part, "%H:%M").strftime("%H:%M"))
            except ValueError:
                return f"Bad time '{part}'. Use HH:MM, e.g. 17:30."

    new = [
        {"origin": origin, "date": d, "pax": pax, "preferred_times": times, "enabled": True}
        for d in dates
    ]
    if len(searches) + len(new) > max_searches:
        return f"Too many searches (max {max_searches}). Remove some with /remove first."
    searches.extend(new)
    return "Added:\n" + "\n".join(describe(s) for s in new) + "\n\nNow:\n" + list_text(searches)


def cmd_remove(args: list[str], searches: list[dict]) -> str:
    if not args:
        return "Usage: /remove <n> or /remove all\n\n" + list_text(searches)
    if args[0].lower() == "all":
        searches.clear()
        return "All searches removed."
    try:
        n = int(args[0])
        if n < 1:
            raise IndexError
        removed = searches.pop(n - 1)
    except (ValueError, IndexError):
        return f"No search number {args[0]}.\n\n" + list_text(searches)
    return f"Removed: {describe(removed)}\n\nNow:\n" + list_text(searches)


def handle_command(text: str, searches: list[dict], state: dict, max_searches: int) -> str:
    parts = text.split()
    command = parts[0].split("@")[0].lower()
    args = parts[1:]

    if command in ("/start", "/help"):
        return HELP_TEXT
    if command == "/add":
        return cmd_add(args, searches, max_searches)
    if command == "/list":
        return list_text(searches)
    if command == "/remove":
        return cmd_remove(args, searches)
    if command == "/pause":
        state["paused"] = True
        return "Searching paused. Your searches are kept. Use /resume to start again."
    if command == "/resume":
        state["paused"] = False
        return "Searching resumed.\n\n" + list_text(searches)
    if command == "/status":
        return status_text(searches, state["paused"])
    return "Unknown command. Send /help for the list."


# ---------- entry points ----------

def prune_past(searches: list[dict]) -> bool:
    today = datetime.now(SGT).date()
    kept = []
    for s in searches:
        try:
            if parse_date(str(s.get("date", ""))) < today:
                continue
        except ValueError:
            pass
        kept.append(s)
    changed = len(kept) != len(searches)
    searches[:] = kept
    return changed


def process_updates(token: str, chat_id: str, max_searches: int) -> dict:
    """Apply pending Telegram commands and send the daily summary. Returns state."""
    state = load_state()
    searches = load_search_list()
    searches_changed = prune_past(searches)
    state_before = json.dumps(state, sort_keys=True)

    if token and chat_id:
        try:
            updates = api(token, "getUpdates", offset=state["update_offset"], timeout=0) or []
        except Exception as exc:
            print(f"[ERROR] Could not read Telegram updates: {exc}")
            updates = []

        for update in updates:
            state["update_offset"] = update["update_id"] + 1
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            from_chat = str((message.get("chat") or {}).get("id", ""))
            if not text.startswith("/"):
                continue
            if from_chat != str(chat_id):
                print(f"[WARN] Ignoring command from unknown chat {from_chat}")
                continue
            print(f"[BOT] {text}")
            before = json.dumps(searches, sort_keys=True)
            answer = handle_command(text, searches, state, max_searches)
            if json.dumps(searches, sort_keys=True) != before:
                searches_changed = True
            reply(token, chat_id, answer)

        now = datetime.now(SGT)
        today = now.date().isoformat()
        if (
            now.hour >= DAILY_SUMMARY_HOUR_SGT
            and state["last_summary_date"] != today
            and searches
            and not state["paused"]
        ):
            reply(token, chat_id, status_text(searches, False, header="Daily update - KTMB searches"))
            state["last_summary_date"] = today
    else:
        print("[WARN] Telegram credentials not set. Skipping bot commands.")

    if searches_changed:
        save_search_list(searches)
    if json.dumps(state, sort_keys=True) != state_before or not STATE_FILE.exists():
        save_json(STATE_FILE, state)
    return state
