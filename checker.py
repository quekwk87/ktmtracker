"""
KTMB Shuttle Tebrau browserless availability checker.

Reads search configs from searches.json, checks KTMB Shuttle availability with
plain HTTP requests, and sends one consolidated Telegram notification when
matching seats are found.
"""

import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests


BASE_URL = "https://shuttleonline.ktmb.com.my"
KTMB_URL = f"{BASE_URL}/Home/Shuttle"
SHUTTLE_TRIP_URL = f"{BASE_URL}/ShuttleTrip"
CONFIG_FILE = Path(__file__).parent / "searches.json"
MAX_SEARCHES = 5
SGT = timezone(timedelta(hours=8))
REQUEST_TIMEOUT_SECONDS = 30

DEFAULT_ORIGIN = "JB SENTRAL"
DEFAULT_DESTINATION = "WOODLANDS CIQ"
SUPPORTED_STATIONS = {DEFAULT_ORIGIN, DEFAULT_DESTINATION}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


class InputParser(HTMLParser):
    """Small HTML input parser to avoid requiring a browser."""

    def __init__(self):
        super().__init__()
        self.inputs: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "input":
            self.inputs.append(dict(attrs))


class TripRowParser(HTMLParser):
    """Parse the trip table returned inside /ShuttleTrip/Trip JSON."""

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self._in_trip_row = False
        self._in_cell = False
        self._row_class = ""
        self._row_hour_minute = ""
        self._cells: list[str] = []
        self._cell_text: list[str] = []
        self._attrs_by_class: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag = tag.lower()

        if tag == "tr" and "data-hourminute" in attrs_dict:
            self._in_trip_row = True
            self._row_class = attrs_dict.get("class", "")
            self._row_hour_minute = attrs_dict.get("data-hourminute", "")
            self._cells = []
            self._attrs_by_class = {}
            return

        if not self._in_trip_row:
            return

        class_attr = attrs_dict.get("class", "")
        if class_attr:
            for class_name in class_attr.split():
                if class_name in {"btn-seat-layout", "btn-login"}:
                    self._attrs_by_class[class_name] = json.dumps(attrs_dict)

        if tag == "td":
            self._in_cell = True
            self._cell_text = []

    def handle_data(self, data):
        if self._in_cell:
            text = data.strip()
            if text:
                self._cell_text.append(text)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._in_trip_row and tag == "td":
            self._cells.append(" ".join(self._cell_text))
            self._in_cell = False
        elif self._in_trip_row and tag == "tr":
            self.rows.append(
                {
                    "class": self._row_class,
                    "hour_minute": self._row_hour_minute,
                    "cells": self._cells,
                    "attrs_by_class": self._attrs_by_class,
                }
            )
            self._in_trip_row = False


def parse_inputs(html: str) -> tuple[dict[str, str], dict[str, str]]:
    parser = InputParser()
    parser.feed(html)

    by_id = {}
    by_name = {}
    for item in parser.inputs:
        value = item.get("value", "")
        if item.get("id"):
            by_id[item["id"]] = value
        if item.get("name"):
            by_name[item["name"]] = value
    return by_id, by_name


def get_required(mapping: dict[str, str], key: str, context: str) -> str:
    value = mapping.get(key)
    if not value:
        raise ValueError(f"Could not find required field '{key}' in {context}")
    return value


def load_searches() -> list[dict]:
    """Load and validate search configurations from searches.json."""
    if not CONFIG_FILE.exists():
        print(f"[ERROR] Config file not found: {CONFIG_FILE}")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    searches = data.get("searches", [])
    enabled = []
    today = datetime.now(SGT).date()

    for search in searches:
        if not search.get("enabled", True):
            continue

        try:
            search_date = datetime.strptime(search["date"], "%d/%m/%Y").date()
            if search_date < today:
                print(
                    f"[SKIP] '{search.get('label', '?')}' - "
                    f"date {search['date']} has passed"
                )
                continue
        except (ValueError, KeyError):
            pass

        enabled.append(search)

    if len(enabled) > MAX_SEARCHES:
        print(f"[WARN] {len(enabled)} searches enabled, capping at {MAX_SEARCHES}")
        enabled = enabled[:MAX_SEARCHES]

    required_fields = ["label", "origin", "destination", "date", "pax"]
    for index, search in enumerate(enabled):
        for field in required_fields:
            if field not in search:
                print(f"[ERROR] Search #{index + 1} missing required field: {field}")
                sys.exit(1)

        if search["origin"] not in SUPPORTED_STATIONS:
            print(f"[ERROR] Search #{index + 1} has unsupported origin: {search['origin']}")
            sys.exit(1)
        if search["destination"] not in SUPPORTED_STATIONS:
            print(
                f"[ERROR] Search #{index + 1} has unsupported destination: "
                f"{search['destination']}"
            )
            sys.exit(1)
        if search["origin"] == search["destination"]:
            print(f"[ERROR] Search #{index + 1} origin and destination cannot match")
            sys.exit(1)

        try:
            pax = int(search["pax"])
        except (TypeError, ValueError):
            print(f"[ERROR] Search #{index + 1} pax must be a number")
            sys.exit(1)
        if pax < 1 or pax > 6:
            print(f"[ERROR] Search #{index + 1} pax must be between 1 and 6")
            sys.exit(1)

    print(f"[INFO] Loaded {len(enabled)} search(es) from {CONFIG_FILE.name}")
    return enabled


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
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        print("[OK] Telegram message sent.")
    except Exception as exc:
        print(f"[ERROR] Telegram send failed: {exc}")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "application/json;q=0.8,*/*;q=0.7"
            ),
        }
    )
    return session


def to_display_date(date_value: str) -> str:
    return datetime.strptime(date_value, "%d/%m/%Y").strftime("%d %b %Y")


def to_api_date(date_value: str) -> str:
    return datetime.strptime(date_value, "%d/%m/%Y").strftime("%Y-%m-%d")


def station_form_fields(home_by_id: dict[str, str], origin: str, destination: str) -> dict[str, str]:
    from_data = get_required(home_by_id, "FromStationData", "Home/Shuttle page")
    to_data = get_required(home_by_id, "ToStationData", "Home/Shuttle page")

    if origin == DEFAULT_ORIGIN and destination == DEFAULT_DESTINATION:
        return {
            "FromStationData": from_data,
            "ToStationData": to_data,
            "FromStationId": origin,
            "ToStationId": destination,
        }

    if origin == DEFAULT_DESTINATION and destination == DEFAULT_ORIGIN:
        return {
            "FromStationData": to_data,
            "ToStationData": from_data,
            "FromStationId": origin,
            "ToStationId": destination,
        }

    raise ValueError(f"Unsupported route: {origin} -> {destination}")


def fetch_trip_page(session: requests.Session, search: dict) -> str:
    home_resp = session.get(KTMB_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    home_resp.raise_for_status()
    home_by_id, home_by_name = parse_inputs(home_resp.text)

    form_data = station_form_fields(home_by_id, search["origin"], search["destination"])
    form_data.update(
        {
            "OnwardDate": to_display_date(search["date"]),
            "ReturnDate": "",
            "PassengerCount": str(search["pax"]),
            "__RequestVerificationToken": get_required(
                home_by_name,
                "__RequestVerificationToken",
                "Home/Shuttle page",
            ),
        }
    )

    trip_resp = session.post(
        SHUTTLE_TRIP_URL,
        data=form_data,
        headers={
            "Origin": BASE_URL,
            "Referer": KTMB_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    trip_resp.raise_for_status()
    return trip_resp.text


def find_trip_endpoint(html: str) -> str:
    match = re.search(r'id=["\']TripTripUrl["\']\s+value=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'value=["\']([^"\']+)["\']\s+id=["\']TripTripUrl["\']', html)
    if not match:
        return "/ShuttleTrip/Trip"
    return match.group(1)


def fetch_trip_results(session: requests.Session, trip_page_html: str, search: dict) -> str:
    trip_by_id, trip_by_name = parse_inputs(trip_page_html)

    payload = {
        "SearchData": get_required(trip_by_id, "SearchData", "ShuttleTrip page"),
        "FormValidationCode": get_required(
            trip_by_id,
            "FormValidationCode",
            "ShuttleTrip page",
        ),
        "DepartDate": to_api_date(search["date"]),
        "IsReturn": False,
        "BookingTripSequenceNo": 1,
    }
    request_token = get_required(
        trip_by_name,
        "__RequestVerificationToken",
        "ShuttleTrip page",
    )
    endpoint = urljoin(BASE_URL, find_trip_endpoint(trip_page_html))

    api_resp = session.post(
        endpoint,
        json=payload,
        headers={
            "Origin": BASE_URL,
            "Referer": SHUTTLE_TRIP_URL,
            "RequestVerificationToken": request_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    api_resp.raise_for_status()
    data = api_resp.json()

    if not data.get("status"):
        messages = data.get("messages") or ["Unknown KTMB trip API error"]
        raise RuntimeError("; ".join(str(message) for message in messages))

    return data.get("data", "")


def parse_trip_results(results_html: str) -> list[dict]:
    parser = TripRowParser()
    parser.feed(results_html)

    slots = []
    for row in parser.rows:
        cells = row["cells"]
        if len(cells) < 6:
            continue

        seats_match = re.search(r"\d+", cells[4])
        seats = int(seats_match.group(0)) if seats_match else 0
        row_classes = set(row["class"].split())
        is_available = seats > 0 and "disabled" not in row_classes

        slots.append(
            {
                "service": cells[0],
                "depart": cells[1],
                "arrive": cells[2],
                "duration": cells[3],
                "seats": seats,
                "status": "Available" if is_available else "Unavailable",
                "price": cells[5],
                "raw_text": " | ".join(cells),
                "available": is_available,
                "hour_minute": row["hour_minute"],
            }
        )

    return slots


def run_single_search(search: dict, index: int) -> list[dict]:
    label = search["label"]
    origin = search["origin"]
    destination = search["destination"]
    date = search["date"]
    pax = str(search["pax"])
    preferred_times = search.get("preferred_times", [])

    print(f"\n{'=' * 60}")
    print(f"[SEARCH {index + 1}] {label}")
    print(f"[INFO] {origin} -> {destination} on {date}, {pax} pax")
    print(f"[INFO] Preferred times: {preferred_times or 'any'}")
    print(f"{'=' * 60}")

    try:
        session = make_session()
        trip_page_html = fetch_trip_page(session, search)
        results_html = fetch_trip_results(session, trip_page_html, search)
        results = [slot for slot in parse_trip_results(results_html) if slot["available"]]

        if not results:
            print("[INFO] No available slots found.")
            return []

        if preferred_times:
            results = [slot for slot in results if slot["depart"] in preferred_times]
            if not results:
                print("[INFO] Slots exist but none match preferred times.")
                return []

        print(f"[INFO] Found {len(results)} matching slot(s).")
        for slot in results:
            slot["_label"] = label
            slot["_origin"] = origin
            slot["_destination"] = destination
            slot["_date"] = date
            slot["_pax"] = pax

        return results

    except Exception as exc:
        print(f"[ERROR] Failed search '{label}': {exc}")
        return []


def format_telegram_message(all_results: list[dict]) -> str:
    """Format results from all searches into one Telegram message."""
    now_sgt = datetime.now(SGT).strftime("%Y-%m-%d %H:%M SGT")

    lines = [
        "*KTMB Shuttle - Slots Found!*",
        f"Checked at: {now_sgt}",
        "",
    ]

    grouped = defaultdict(list)
    for result in all_results:
        grouped[result["_label"]].append(result)

    for label, slots in grouped.items():
        first = slots[0]
        lines.append(f"*{label}*")
        lines.append(f"{first['_origin']} -> {first['_destination']}")
        lines.append(f"{first['_date']} | {first['_pax']} pax")
        for slot in slots:
            lines.append(
                "  - "
                f"{slot['depart']} -> {slot['arrive']} | "
                f"{slot['service']} | "
                f"{slot['seats']} seats | "
                f"{slot['price']}"
            )
        lines.append("")

    lines.append(f"Book now: {KTMB_URL}")
    return "\n".join(lines)


def main():
    searches = load_searches()
    if not searches:
        print("[INFO] No enabled searches found. Exiting.")
        return

    all_results = []
    for index, search in enumerate(searches):
        all_results.extend(run_single_search(search, index))

        if index < len(searches) - 1:
            delay = random.uniform(2.0, 5.0)
            print(f"[INFO] Waiting {delay:.1f}s before next search...")
            time_to_wait = delay
            while time_to_wait > 0:
                chunk = min(time_to_wait, 1.0)
                time.sleep(chunk)
                time_to_wait -= chunk

    print(f"\n{'=' * 60}")
    print(f"[SUMMARY] Total matching slots found: {len(all_results)}")
    print(f"{'=' * 60}")

    if all_results:
        send_telegram(format_telegram_message(all_results))
    else:
        print("[INFO] No available slots across all searches. No notification sent.")


if __name__ == "__main__":
    main()
