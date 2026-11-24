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
SHORT_NAME = {"WOODLANDS CIQ": "SG", "JB SENTRAL": "JB"}

HELP_TEXT = """KTMB Shuttle checker commands

/add <from> <date> <pax> [times]
  from: sg (SG TO JB) or jb (JB TO SG)
  date: DD/MM/YYYY or a range DD/MM/YYYY-DD/MM/YYYY
  times: optional, e.g. 17:30 18:45 (blank = any time)
  e.g. /add sg 04/12/2026 5
  e.g. /add jb 06/12/2026-07/12/2026 5 17:30 18:45

/times <n> - show KTMB timetable for search n and tap times to watch
/settimes <n> [times] - set times by typing (no times = any time)
/list - show searches
/remove <n> - remove search number n (see /list)
/remove <n> <times> - remove only those times, e.g. /remove 3 10:00 11:30
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


BOT_COMMANDS = [
    ("add", "Add a search: /add sg 04/12/2026 5"),
    ("list", "Show searches"),
    ("times", "Pick departure times: /times 1"),
    ("settimes", "Type times: /settimes 1 17:30 18:45"),
    ("remove", "Remove a search (/remove 1) or a time (/remove 1 17:30)"),
    ("pause", "Stop searching"),
    ("resume", "Start searching again"),
    ("status", "What is being searched"),
    ("help", "How to use"),
]
COMMANDS_VERSION = 4


# ---------- telegram api ----------

def api(token: str, method: str, **params):
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json().get("result")


def reply(token: str, chat_id: str, text: str, markup: dict | None = None) -> None:
    params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup:
        params["reply_markup"] = markup
    try:
        api(token, "sendMessage", **params)
    except Exception as exc:
        print(f"[ERROR] Telegram reply failed: {exc}")


def safe_api(token: str, method: str, **params) -> None:
    try:
        api(token, method, **params)
    except Exception as exc:
        print(f"[WARN] Telegram {method} failed: {exc}")


# ---------- formatting ----------

def describe(search: dict) -> str:
    origin = search["origin"]
    times = ", ".join(search.get("preferred_times") or []) or "any time"
    flag = "" if search.get("enabled", True) else " (disabled)"
    route = f"{SHORT_NAME.get(origin, origin)} TO {SHORT_NAME.get(OTHER_STATION.get(origin), '?')}"
    try:
        day = datetime.strptime(search["date"], "%d/%m/%Y").strftime("%a %d %b")
    except ValueError:
        day = search["date"]
    return f"{route} | {day} | {search['pax']} pax | {times}{flag}"


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
        return "Usage: /add <sg|jb> <DD/MM/YYYY[-DD/MM/YYYY]> <pax> [times]\ne.g. /add sg 04/12/2026 5"
    origin = STATION_ALIASES.get(args[0].upper())
    if not origin:
        return f"Unknown station '{args[0]}'. Use sg (SG TO JB) or jb (JB TO SG)."
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
        return "Usage: /remove <n>, /remove <n> <times> or /remove all\n\n" + list_text(searches)
    if args[0].lower() == "all":
        searches.clear()
        return "All searches removed."
    search = get_numbered(args, searches)
    if not search:
        return f"No search number {args[0]}.\n\n" + list_text(searches)

    if len(args) > 1:
        # /remove <n> <times...>: drop only those times from search n
        to_drop = []
        for t in args[1:]:
            for part in t.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    to_drop.append(datetime.strptime(part, "%H:%M").strftime("%H:%M"))
                except ValueError:
                    return f"Bad time '{part}'. Use HH:MM, e.g. /remove {args[0]} 17:30"
        current = list(search.get("preferred_times") or [])
        if not current:
            return (
                f"Search {args[0]} watches any time, so there are no times to remove.\n"
                f"Use /settimes {args[0]} <times> to pick the times you want instead."
            )
        missing = [t for t in to_drop if t not in current]
        if missing:
            return f"Search {args[0]} is not watching {', '.join(missing)}.\n\n" + list_text(searches)
        remaining = [t for t in current if t not in to_drop]
        if not remaining:
            return (
                "That would remove every time, which would mean 'any time'.\n"
                f"To delete the whole search use /remove {args[0]}."
            )
        search["preferred_times"] = remaining
        return f"Removed {', '.join(to_drop)}.\n\nNow:\n" + list_text(searches)

    searches.remove(search)
    return f"Removed: {describe(search)}\n\nNow:\n" + list_text(searches)


# ---------- time picker ----------

ORIGIN_CODE = {"WOODLANDS CIQ": "WL", "JB SENTRAL": "JB"}
CODE_ORIGIN = {v: k for k, v in ORIGIN_CODE.items()}
TICK = "\u2705 "


def find_search(searches: list[dict], code: str, date: str):
    origin = CODE_ORIGIN.get(code)
    for s in searches:
        if s["origin"] == origin and s["date"] == date:
            return s
    return None


def picker_text(search: dict) -> str:
    times = ", ".join(search.get("preferred_times") or []) or "any time"
    return (
        f"{describe(search)}\n\n"
        f"Tap the times you want alerts for ({TICK.strip()} = watching).\n"
        f"Nothing ticked = any time. Now watching: {times}\n"
        "Taps are saved on the next check run."
    )


def picker_markup(search: dict, buttons: list[tuple[str, str]]) -> dict:
    """buttons: list of (time, extra label)."""
    code = ORIGIN_CODE[search["origin"]]
    chosen = set(search.get("preferred_times") or [])
    keys = [
        {
            "text": f"{TICK if t in chosen else ''}{t}{extra}",
            "callback_data": f"t|{code}|{search['date']}|{t}",
        }
        for t, extra in buttons
    ]
    rows = [keys[i:i + 3] for i in range(0, len(keys), 3)]
    rows.append([{"text": "Any time (clear)", "callback_data": f"a|{code}|{search['date']}"}])
    return {"inline_keyboard": rows}


def picker_for(search: dict, fetch_slots):
    """Return (text, markup) for a search's timetable picker."""
    if fetch_slots is None:
        return "Timetable lookup not available here. Use /settimes instead.", None
    try:
        slots = fetch_slots(search)
    except Exception as exc:
        return (
            f"Could not load KTMB timetable for {describe(search)}: {exc}\n"
            "Set times by typing, e.g. /settimes 1 17:30 18:45",
            None,
        )
    if not slots:
        return f"KTMB shows no trains for {describe(search)}.", None
    buttons = []
    for slot in slots:
        extra = f" ({slot['seats']})" if slot.get("available") else " (full)"
        buttons.append((slot["depart"], extra))
    return picker_text(search), picker_markup(search, buttons)


def buttons_from_markup(markup: dict) -> list[tuple[str, str]]:
    buttons = []
    for row in (markup or {}).get("inline_keyboard", []):
        for key in row:
            data = key.get("callback_data", "")
            if not data.startswith("t|"):
                continue
            t = data.split("|")[3]
            label = key.get("text", "").replace(TICK, "")
            buttons.append((t, label[len(t):]))
    return buttons


def handle_callback(token: str, query: dict, searches: list[dict]) -> None:
    parts = query.get("data", "").split("|")
    search = find_search(searches, parts[1], parts[2]) if len(parts) >= 3 else None
    if not search:
        safe_api(token, "answerCallbackQuery", callback_query_id=query["id"], text="That search no longer exists.")
        return
    if parts[0] == "t" and len(parts) == 4:
        times = list(search.get("preferred_times") or [])
        if parts[3] in times:
            times.remove(parts[3])
        else:
            times.append(parts[3])
        search["preferred_times"] = sorted(times)
    elif parts[0] == "a":
        search["preferred_times"] = []
    safe_api(token, "answerCallbackQuery", callback_query_id=query["id"], text="Saved")


def refresh_picker(token: str, chat_id: str, message: dict, search: dict) -> None:
    buttons = buttons_from_markup(message.get("reply_markup"))
    safe_api(
        token,
        "editMessageText",
        chat_id=chat_id,
        message_id=message.get("message_id"),
        text=picker_text(search),
        reply_markup=picker_markup(search, buttons),
    )


def get_numbered(args: list[str], searches: list[dict]):
    try:
        n = int(args[0])
        if n < 1:
            raise IndexError
        return searches[n - 1]
    except (ValueError, IndexError):
        return None


def cmd_settimes(args: list[str], searches: list[dict]) -> str:
    search = get_numbered(args, searches) if args else None
    if not search:
        return "Usage: /settimes <n> [times], e.g. /settimes 1 17:30 18:45\n\n" + list_text(searches)
    times = []
    for t in args[1:]:
        for part in t.split(","):
            part = part.strip()
            if part:
                try:
                    times.append(datetime.strptime(part, "%H:%M").strftime("%H:%M"))
                except ValueError:
                    return f"Bad time '{part}'. Use HH:MM, e.g. 17:30."
    search["preferred_times"] = sorted(set(times))
    return "Updated:\n" + describe(search)


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
    if command == "/settimes":
        return cmd_settimes(args, searches)
    if command == "/pause":
        state["paused"] = True
        return "Searching paused. Your searches are kept. Use /resume to start again."
    if command == "/resume":
        state["paused"] = False
        return "Searching resumed.\n\n" + list_text(searches)
    if command == "/status":
        return status_text(searches, state["paused"])
    return "Unknown command. Send /help for the list."


def raw_api(token: str, method: str, **params) -> dict:
    """Like api() but returns Telegram's full JSON reply (including errors) for logging."""
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
        return resp.json()
    except Exception as exc:
        return {"ok": False, "description": str(exc)}


def setup_commands(token: str, chat_id: str) -> bool:
    """Register the command menu for all chats and for this chat, and log what Telegram reports."""
    commands = [{"command": c, "description": d} for c, d in BOT_COMMANDS]
    me = raw_api(token, "getMe")
    print(f"[BOT] getMe: @{(me.get('result') or {}).get('username')} ok={me.get('ok')}")
    for label, scope in (
        ("before/default", None),
        ("before/chat", {"type": "chat", "chat_id": chat_id}),
    ):
        got = raw_api(token, "getMyCommands", **({"scope": scope} if scope else {}))
        names = [c.get("command") for c in got.get("result") or []]
        print(f"[BOT] getMyCommands {label}: ok={got.get('ok')} {names} {got.get('description', '')}")
    ok = True
    for label, scope in (
        ("default", None),
        ("private chats", {"type": "all_private_chats"}),
        ("this chat", {"type": "chat", "chat_id": chat_id}),
    ):
        res = raw_api(token, "setMyCommands", commands=commands, **({"scope": scope} if scope else {}))
        print(f"[BOT] setMyCommands {label}: ok={res.get('ok')} {res.get('description', '')}")
        ok = ok and bool(res.get("ok"))
    res = raw_api(token, "setChatMenuButton", chat_id=chat_id, menu_button={"type": "commands"})
    print(f"[BOT] setChatMenuButton: ok={res.get('ok')} {res.get('description', '')}")
    got = raw_api(token, "getMyCommands", scope={"type": "chat", "chat_id": chat_id})
    print(f"[BOT] getMyCommands after/chat: {[c.get('command') for c in got.get('result') or []]}")
    return ok


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


def process_updates(token: str, chat_id: str, max_searches: int, fetch_slots=None) -> dict:
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

        if state.get("commands_version") != COMMANDS_VERSION:
            if setup_commands(token, chat_id):
                state["commands_version"] = COMMANDS_VERSION

        pickers_to_refresh = {}
        for update in updates:
            state["update_offset"] = update["update_id"] + 1
            query = update.get("callback_query")
            if query:
                q_chat = str(((query.get("message") or {}).get("chat") or {}).get("id", ""))
                if q_chat != str(chat_id):
                    continue
                before = json.dumps(searches, sort_keys=True)
                handle_callback(token, query, searches)
                if json.dumps(searches, sort_keys=True) != before:
                    searches_changed = True
                msg = query.get("message") or {}
                parts = query.get("data", "").split("|")
                search = find_search(searches, parts[1], parts[2]) if len(parts) >= 3 else None
                if search and msg.get("message_id"):
                    pickers_to_refresh[msg["message_id"]] = (search, msg)
                continue
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            from_chat = str((message.get("chat") or {}).get("id", ""))
            if not text.startswith("/"):
                continue
            if from_chat != str(chat_id):
                print(f"[WARN] Ignoring command from unknown chat {from_chat}")
                continue
            print(f"[BOT] {text}")
            before_list = [dict(s) for s in searches]
            before = json.dumps(searches, sort_keys=True)
            command = text.split()[0].split("@")[0].lower()
            if command == "/times":
                args = text.split()[1:]
                search = get_numbered(args, searches) if args else None
                if not search:
                    reply(token, chat_id, "Usage: /times <n>\n\n" + list_text(searches))
                else:
                    reply(token, chat_id, *picker_for(search, fetch_slots))
                continue
            answer = handle_command(text, searches, state, max_searches)
            if json.dumps(searches, sort_keys=True) != before:
                searches_changed = True
            reply(token, chat_id, answer)
            if command == "/add":
                for s in searches:
                    if s not in before_list:
                        reply(token, chat_id, *picker_for(s, fetch_slots))

        for search, msg in pickers_to_refresh.values():
            refresh_picker(token, chat_id, msg, search)

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
