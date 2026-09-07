"""Small local web editor for KTMB shuttle search configuration."""

import html
import json
import os
import tempfile
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse


CONFIG_FILE = Path(__file__).with_name("searches.json")
HOST = "127.0.0.1"
PORT = int(os.getenv("KTMB_UI_PORT", "8000"))
MAX_SEARCHES = 5
STATIONS = ("WOODLANDS CIQ", "JB SENTRAL")


def escape(value) -> str:
    return html.escape(str(value or ""), quote=True)


def destination_for(origin: str) -> str:
    return "JB SENTRAL" if origin == "WOODLANDS CIQ" else "WOODLANDS CIQ"


def parse_date(value: str) -> datetime:
    value = value.strip()
    for date_format in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    raise ValueError("date must use DD/MM/YYYY")


def input_date(value: str) -> str:
    return parse_date(value).strftime("%Y-%m-%d")


def config_date(value: str) -> str:
    return parse_date(value).strftime("%d/%m/%Y")


def parse_times(value) -> list[str]:
    if isinstance(value, list):
        values = value
    else:
        values = str(value or "").split(",")

    times = []
    for raw_time in values:
        time_value = str(raw_time).strip()
        if not time_value:
            continue
        try:
            datetime.strptime(time_value, "%H:%M")
        except ValueError as exc:
            raise ValueError(f"invalid departure time: {time_value}") from exc
        if time_value not in times:
            times.append(time_value)
    return times


def clean_search(raw: dict, index: int) -> dict:
    origin = " ".join(str(raw.get("origin", "")).upper().split())
    if origin not in STATIONS:
        raise ValueError(f"Search {index}: choose a supported origin station")

    date_value = str(raw.get("date", "")).strip()
    if not date_value:
        raise ValueError(f"Search {index}: choose a travel date")

    try:
        date_value = config_date(date_value)
    except ValueError as exc:
        raise ValueError(f"Search {index}: date must use DD/MM/YYYY") from exc

    try:
        pax = int(raw.get("pax", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Search {index}: passenger count must be 1-6") from exc
    if pax < 1 or pax > 6:
        raise ValueError(f"Search {index}: passenger count must be 1-6")

    return {
        "origin": origin,
        "date": date_value,
        "pax": pax,
        "preferred_times": parse_times(raw.get("preferred_times", [])),
        "enabled": bool(raw.get("enabled", True)),
    }


def load_searches() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []

    with CONFIG_FILE.open("r", encoding="utf-8") as config_file:
        data = json.load(config_file)

    raw_searches = data.get("searches", [])
    if not isinstance(raw_searches, list):
        raise ValueError("searches.json must contain a 'searches' list")
    return [clean_search(search, index + 1) for index, search in enumerate(raw_searches)]


def save_searches(searches: list[dict]) -> None:
    data = {"searches": searches}
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=CONFIG_FILE.parent,
        prefix="searches.",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        json.dump(data, temporary_file, indent=2)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    os.replace(temporary_path, CONFIG_FILE)


def row_html(index, search: Optional[dict] = None) -> str:
    search = search or {
        "origin": "WOODLANDS CIQ",
        "date": "",
        "pax": 1,
        "preferred_times": [],
        "enabled": True,
    }
    origin = search["origin"]
    destination = destination_for(origin)
    times = ",".join(search.get("preferred_times", []))
    checked = " checked" if search.get("enabled", True) else ""
    date_value = input_date(search["date"]) if search.get("date") else ""
    display_index = "__INDEX__" if isinstance(index, str) else index + 1
    station_options = "".join(
        f'<option value="{escape(station)}"'
        f'{" selected" if station == origin else ""}>{escape(station)}</option>'
        for station in STATIONS
    )
    return f"""
    <section class="search-row" data-index="{index}">
      <div class="row-heading">
        <strong>Search <span class="search-number">{display_index}</span></strong>
        <button class="button button-danger remove-search" type="button">Remove</button>
      </div>
      <div class="field-grid">
        <label>
          <span>Origin</span>
          <select name="origin_{index}" class="origin-input">{station_options}</select>
        </label>
        <label>
          <span>Destination</span>
          <output class="destination-output">{escape(destination)}</output>
        </label>
        <label>
          <span>Travel date</span>
          <input name="date_{index}" type="date" value="{escape(date_value)}" required>
        </label>
        <label>
          <span>Passengers</span>
          <select name="pax_{index}">
            {"".join(f'<option value="{count}"{" selected" if count == int(search.get("pax", 1)) else ""}>{count}</option>' for count in range(1, 7))}
          </select>
        </label>
      </div>
      <div class="time-field">
        <span class="field-label">Preferred departure times</span>
        <div class="time-controls">
          <input class="time-input" type="time" aria-label="Departure time">
          <button class="button button-secondary add-time" type="button">+ Add time</button>
          <button class="button button-quiet clear-times" type="button">Clear times</button>
        </div>
        <div class="time-list" aria-live="polite"></div>
        <input class="times-value" name="times_{index}" type="hidden" value="{escape(times)}">
        <small>Leave empty to accept any available departure time.</small>
      </div>
      <label class="enabled-field">
        <input name="enabled_{index}" type="checkbox" value="1"{checked}>
        <span>Include this search in checks</span>
      </label>
    </section>
    """


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KTMB Search Configuration</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #18212b;
      --muted: #637080;
      --line: #d9e0e7;
      --surface: #ffffff;
      --page: #f3f6f8;
      --accent: #0969a8;
      --accent-dark: #07517f;
      --danger: #b42318;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--ink); background: var(--page); }
    main { width: min(1080px, calc(100% - 32px)); margin: 32px auto 56px; }
    header { display: flex; align-items: end; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
    h1 { margin: 0 0 8px; font-size: clamp(1.6rem, 4vw, 2.3rem); letter-spacing: 0; }
    p { margin: 0; color: var(--muted); line-height: 1.5; }
    .status { padding: 10px 12px; border: 1px solid #b8dec9; color: #155c39; background: #eefaf2; border-radius: 6px; white-space: nowrap; }
    .error { margin-bottom: 16px; padding: 12px 14px; border: 1px solid #f0b7b2; color: var(--danger); background: #fff5f4; border-radius: 6px; }
    .search-list { display: grid; gap: 14px; }
    .search-row { padding: 18px; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; }
    .row-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
    .field-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
    label, .time-field { display: grid; gap: 7px; }
    label > span, .field-label { color: var(--muted); font-size: .82rem; font-weight: 600; }
    input, select, output { width: 100%; min-height: 42px; padding: 9px 10px; color: var(--ink); background: #fff; border: 1px solid #bcc7d2; border-radius: 4px; font: inherit; }
    output { display: flex; align-items: center; background: #f7f9fb; }
    .time-field { margin-top: 16px; }
    .time-controls { display: flex; flex-wrap: wrap; gap: 8px; }
    .time-controls input { width: 150px; }
    .button { min-height: 38px; padding: 8px 12px; border: 1px solid transparent; border-radius: 4px; font: inherit; font-weight: 600; cursor: pointer; }
    .button-primary { color: #fff; background: var(--accent); }
    .button-primary:hover { background: var(--accent-dark); }
    .button-secondary { color: var(--accent-dark); background: #e8f3fa; border-color: #b4d4e8; }
    .button-quiet { color: var(--muted); background: #fff; border-color: var(--line); }
    .button-danger { color: var(--danger); background: #fff; border-color: #efc0bc; }
    .time-list { display: flex; flex-wrap: wrap; gap: 8px; min-height: 10px; margin-top: 10px; }
    .time-chip { display: inline-flex; align-items: center; gap: 7px; padding: 6px 8px; color: #164d70; background: #e9f4fb; border: 1px solid #c3dfef; border-radius: 4px; font-size: .9rem; }
    .remove-time { padding: 0; color: #164d70; background: none; border: 0; font-size: 1.1rem; line-height: 1; cursor: pointer; }
    small { color: var(--muted); }
    .enabled-field { display: flex; grid-template-columns: none; align-items: center; gap: 8px; margin-top: 16px; }
    .enabled-field input { width: 17px; min-height: 17px; }
    .toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; margin-top: 18px; }
    .toolbar-actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .file-note { margin-top: 18px; font-size: .9rem; }
    @media (max-width: 760px) {
      main { width: min(100% - 20px, 600px); margin-top: 20px; }
      header { display: block; }
      .status { display: inline-block; margin-top: 14px; }
      .field-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 460px) {
      .field-grid { grid-template-columns: 1fr; }
      .time-controls input { width: 100%; }
      .time-controls .button { flex: 1; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>KTMB Search Configuration</h1>
        <p>Manage the routes and times that the availability checker monitors.</p>
      </div>
      <div class="status">Saved searches: <strong id="search-count">0</strong> / 5</div>
    </header>
    {{MESSAGE}}
    <form method="post" action="/save" id="search-form">
      <div class="search-list" id="search-list">
        {{ROWS}}
      </div>
      <input type="hidden" name="search_count" id="search-count-input" value="0">
      <div class="toolbar">
        <button class="button button-secondary" type="button" id="add-search">+ Add search</button>
        <div class="toolbar-actions">
          <button class="button button-quiet" type="button" id="refresh-page">Refresh</button>
          <button class="button button-primary" type="submit">Save searches</button>
        </div>
      </div>
    </form>
    <p class="file-note">Saved locally to <strong>searches.json</strong>. The checker reads this file when run locally.</p>
  </main>
  <template id="search-template">{{TEMPLATE_ROW}}</template>
  <script>
    const list = document.querySelector("#search-list");
    const template = document.querySelector("#search-template");
    const countLabel = document.querySelector("#search-count");
    const countInput = document.querySelector("#search-count-input");

    function updateDestination(row) {
      const origin = row.querySelector(".origin-input").value;
      row.querySelector(".destination-output").textContent = origin === "WOODLANDS CIQ" ? "JB SENTRAL" : "WOODLANDS CIQ";
    }

    function renderTimes(row) {
      const value = row.querySelector(".times-value");
      const list = row.querySelector(".time-list");
      const times = value.value.split(",").map(item => item.trim()).filter(Boolean);
      list.replaceChildren();
      times.forEach(time => {
        const chip = document.createElement("span");
        chip.className = "time-chip";
        chip.append(document.createTextNode(time));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "remove-time";
        remove.textContent = "x";
        remove.setAttribute("aria-label", "Remove " + time);
        remove.addEventListener("click", () => {
          value.value = value.value.split(",").filter(item => item !== time).join(",");
          renderTimes(row);
        });
        chip.append(remove);
        list.append(chip);
      });
    }

    function renumberRows() {
      [...list.querySelectorAll(".search-row")].forEach((row, index) => {
        row.dataset.index = index;
        row.querySelector(".search-number").textContent = index + 1;
        row.querySelectorAll("[name]").forEach(input => {
          input.name = input.name.replace(/_[0-9]+$/, "_" + index);
        });
      });
      countLabel.textContent = list.querySelectorAll(".search-row").length;
      countInput.value = list.querySelectorAll(".search-row").length;
    }

    function bindRow(row) {
      row.querySelector(".origin-input").addEventListener("change", () => updateDestination(row));
      row.querySelector(".add-time").addEventListener("click", () => {
        const picker = row.querySelector(".time-input");
        if (!picker.value) return;
        const value = row.querySelector(".times-value");
        const times = value.value.split(",").filter(Boolean);
        if (!times.includes(picker.value)) times.push(picker.value);
        times.sort();
        value.value = times.join(",");
        picker.value = "";
        renderTimes(row);
      });
      row.querySelector(".clear-times").addEventListener("click", () => {
        row.querySelector(".times-value").value = "";
        renderTimes(row);
      });
      row.querySelector(".remove-search").addEventListener("click", () => {
        row.remove();
        renumberRows();
      });
      updateDestination(row);
      renderTimes(row);
    }

    function addRow() {
      if (list.querySelectorAll(".search-row").length >= 5) return;
      const fragment = template.content.cloneNode(true);
      const wrapper = document.createElement("div");
      wrapper.append(fragment);
      wrapper.innerHTML = wrapper.innerHTML.replaceAll("__INDEX__", String(list.querySelectorAll(".search-row").length));
      const row = wrapper.firstElementChild;
      list.append(row);
      bindRow(row);
      renumberRows();
    }

    document.querySelectorAll(".search-row").forEach(bindRow);
    document.querySelector("#add-search").addEventListener("click", addRow);
    document.querySelector("#refresh-page").addEventListener("click", () => window.location.reload());
    renumberRows();
  </script>
</body>
</html>
"""


def render_page(searches: list[dict], message: str = "", error: str = "") -> str:
    rows = "".join(row_html(index, search) for index, search in enumerate(searches))
    template_row = row_html("__INDEX__")
    message_html = ""
    if message:
        message_html = f'<div class="status" role="status">{escape(message)}</div>'
    if error:
        message_html = f'<div class="error" role="alert">{escape(error)}</div>'
    return PAGE_TEMPLATE.replace("{{ROWS}}", rows).replace("{{TEMPLATE_ROW}}", template_row).replace(
        "{{MESSAGE}}", message_html
    ).replace('Saved searches: <strong id="search-count">0</strong>', 'Saved searches: <strong id="search-count">0</strong>')


def form_searches(form: dict[str, list[str]]) -> list[dict]:
    try:
        count = min(int(form.get("search_count", ["0"])[0]), MAX_SEARCHES)
    except ValueError as exc:
        raise ValueError("invalid search form") from exc

    searches = []
    for index in range(count):
        date_value = form.get(f"date_{index}", [""])[0].strip()
        times_value = form.get(f"times_{index}", [""])[0].strip()
        if not date_value and not times_value:
            continue
        searches.append(
            clean_search(
                {
                    "origin": form.get(f"origin_{index}", [""])[0],
                    "date": date_value,
                    "pax": form.get(f"pax_{index}", ["1"])[0],
                    "preferred_times": times_value,
                    "enabled": bool(form.get(f"enabled_{index}")),
                },
                len(searches) + 1,
            )
        )
    return searches


class ConfigHandler(BaseHTTPRequestHandler):
    def send_html(self, content: str, status: int = 200) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_error(404)
            return

        try:
            searches = load_searches()
            params = parse_qs(parsed.query)
            message = "Searches saved." if params.get("saved") == ["1"] else ""
            self.send_html(render_page(searches, message=message))
        except Exception as exc:  # Show config problems in the UI.
            self.send_html(render_page([], error=f"Could not load searches.json: {exc}"), 500)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/save":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            searches = form_searches(form)
            save_searches(searches)
            self.send_response(303)
            self.send_header("Location", "/?saved=1")
            self.end_headers()
        except Exception as exc:
            self.send_html(render_page([], error=f"Could not save searches: {exc}"), 400)

    def log_message(self, format, *args):
        print(f"[UI] {self.address_string()} - {format % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ConfigHandler)
    url = f"http://{HOST}:{PORT}"
    print(f"KTMB configuration UI: {url}")
    print(f"Editing: {CONFIG_FILE}")
    print("Press Ctrl+C to stop.")
    try:
        webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping configuration UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
