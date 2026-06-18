"""
Trello Pre-Meeting Digests — Railway Cron Service
=================================================
ONE daily cron run (09:00 IST / 03:30 UTC) that, for each configured board,
checks the shared Google Calendar for that board's meeting and sends:
  - OWNER digest   : when the meeting is 2 days out  (pre-meeting prep)
  - SONAL digest   : when the meeting is today        (key discussion areas)

Boards covered: Ravi, Finance, Sneha.  Add a board by appending to BOARDS.

Runs once and EXITS (cron-safe). Exits non-zero if any board's job fails, so a
failed run is visible in Railway, not silently swallowed.
"""

import json
import os
import sys
import logging
import traceback
import requests
import anthropic
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build

EMAIL_MCP_URL = "https://valuecart-email-mcp-production.up.railway.app/mcp/valuecart2026"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Env ──────────────────────────────────────────────────────────────────────

TRELLO_API_KEY    = os.environ["TRELLO_API_KEY"]
TRELLO_TOKEN      = os.environ["TRELLO_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Test mode: when "true" (default), every email goes to TEST_EMAIL instead of the
# real recipients, so you can verify all 3 boards render before going live.
TEST_MODE  = os.environ.get("TEST_MODE", "true").lower() == "true"
TEST_EMAIL = os.environ.get("TEST_EMAIL", "aryan@valuecart.in")
SONAL_EMAIL = "sonal@valuecart.in"

# ── Board registry ────────────────────────────────────────────────────────────
# Each board:
#   cal_title  : substring matched (case-insensitive) against calendar event titles
#   board_id   : Trello board id
#   lists      : {list_id: display_name}  (only these lists are scanned)
#   skip_lists : list display-names to ignore entirely
#   labels     : {trello_label_name: priority 1..4}  (1=red/most urgent)
#   owner_email: real recipient for the 2-days-out prep digest
#   person_lane: True if lists are per-person (Finance) → show Owner column
#   default_pri: priority for cards with no recognised label

BOARDS = [
    {
        "key": "ravi",
        "cal_title": "ravi (trello)",
        "board_id": "667516b9619b636121467c4e",
        "owner_email": "ravi@valuecart.in",
        "person_lane": False,
        "default_pri": 3,
        "lists": {
            "667516b9619b636121467c4f": "Recruiting",
            "667516b9619b636121467c51": "PMS",
            "667516b9619b636121467c53": "Activities",
            "667516b9619b636121467c50": "Open Tasks",
            "667516b9619b636121467c54": "Open Tasks and Recurring",
            "667516b9619b636121467c52": "Salary and Compliances",
        },
        "skip_lists": [],
        "labels": {
            "High Preiority and High Intervention": 1,
            "Low Preiority and High Intervention":  2,
            "High Priority and Low Inetrvention":   3,
            "Low Preiority and Low Intervention":   4,
        },
    },
    {
        "key": "finance",
        "cal_title": "finance (trello)",
        "board_id": "69e6fcb2a6f11e8db5a83764",
        "owner_email": "roopa@valuecart.in",
        "person_lane": True,
        "default_pri": 3,
        "lists": {
            "69df38cc3194e95c95f1f750": "Roopa",
            "651d247889718f53d49b4435": "Manigandan Ragavan",
            "69ddeec4b74df23e0e1c1fc7": "Rajya Laxmi",
            "69ddeed6746f177268144bfb": "Raghu",
            "69e07b01248e364074f572b8": "Tapas",
            "69ddeecdc3259e09eab40a7a": "Vinay Kumar",
        },
        "skip_lists": ["Tapas"],  # excluded per Sonal
        "labels": {
            "Important & Urgent":        1,
            "Prirority":                 1,
            "Urgent but not Important":  2,
            "Important but not Urgent":  2,
            "Important Only":            3,
            "Sonal (to review)":         2,
            "BaU process":               4,
            "Done":                      4,
        },
    },
    {
        "key": "sneha",
        "cal_title": "sneha (trello)",
        "board_id": "64d5f4b811a1bc3f629882cb",
        "owner_email": "sneha@valuecart.in",
        "person_lane": False,
        "default_pri": 3,
        "lists": {
            "69ca0228540d9bb637911eb7": "Recurring Tasks",
            "64d5f4b811a1bc3f629882d2": "Personal",
            "64d5f4b811a1bc3f629882d3": "Girnar",
            "6650828949c969cdde04e5e5": "Admin",
            "65bb51c5290710953c889794": "Embassy",
            "65e94d54c4c06a1bfd8d854c": "Aarya",
            "65657d7e29f8b034b6f6f9c1": "Future",
        },
        "skip_lists": ["Future"],  # skipped per memory
        "labels": {
            "High Priority, High Intervention": 1,
            "Low Priority, High Intervention":  2,
            "High Priority, Low Intervention":  3,
            "Low Priority, Low Intervention":   4,
        },
    },
]


def recipients_for(board: dict) -> tuple[str, str]:
    """Return (owner_to, sonal_to) honouring TEST_MODE."""
    if TEST_MODE:
        return TEST_EMAIL, TEST_EMAIL
    return board["owner_email"], SONAL_EMAIL


# ── Google Calendar ───────────────────────────────────────────────────────────

def _gcal_service():
    info = {
        "type": "service_account",
        "project_id":     os.environ["GCP_PROJECT_ID"],
        "private_key_id": os.environ["GCP_PRIVATE_KEY_ID"],
        "private_key":    os.environ["GCP_PRIVATE_KEY"].replace("\\n", "\n"),
        "client_email":   os.environ["GCP_CLIENT_EMAIL"],
        "client_id":      os.environ["GCP_CLIENT_ID"],
        "auth_uri":       "https://accounts.google.com/o/oauth2/auth",
        "token_uri":      "https://oauth2.googleapis.com/token",
    }
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def find_meeting(svc, cal_title: str, target_date: datetime) -> dict | None:
    """First event whose title contains cal_title (case-insensitive) on target_date."""
    day_start = target_date.replace(hour=0,  minute=0,  second=0,  microsecond=0)
    day_end   = target_date.replace(hour=23, minute=59, second=59, microsecond=0)
    result = svc.events().list(
        calendarId=os.environ["GCAL_CALENDAR_ID"],
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True, orderBy="startTime",
    ).execute()
    for event in result.get("items", []):
        if cal_title in event.get("summary", "").lower():
            return event
    return None


# ── Email send via MCP ────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html_body: str):
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "send_email", "arguments": {
            "to": to, "subject": subject, "body_html": html_body,
            "body_text": "Please view this email in an HTML-capable client.",
        }},
    }
    res = requests.post(
        EMAIL_MCP_URL, json=payload,
        headers={"Accept": "application/json, text/event-stream"}, timeout=20,
    )
    res.raise_for_status()
    data_line = next((l for l in res.text.splitlines() if l.startswith("data:")), None)
    if not data_line:
        raise Exception("No data in MCP email response")
    parsed = json.loads(data_line[5:].strip())
    if "error" in parsed:
        raise Exception(f"Email MCP error: {parsed['error']['message']}")
    log.info("Email sent to %s: %s", to, subject)


# ── Trello ────────────────────────────────────────────────────────────────────

def trello_get(path: str, **params) -> dict | list:
    url = f"https://api.trello.com/1/{path}"
    params.update({"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_all_open_cards(board: dict) -> list[dict]:
    """Open cards across the board's lists (minus skip_lists), enriched with list name."""
    cards = []
    skip = set(board.get("skip_lists", []))
    for list_id, list_name in board["lists"].items():
        if list_name in skip:
            continue
        try:
            raw = trello_get(
                f"lists/{list_id}/cards",
                fields="name,due,dueComplete,badges,labels,idList",
                filter="open",
            )
            for c in raw:
                c["_list_name"] = list_name
            cards.extend(raw)
        except Exception as e:
            log.warning("[%s] Failed list %s (%s): %s", board["key"], list_name, list_id, e)
    return cards


def fetch_card_comments(card_id: str, limit: int = 8) -> list[str]:
    """Most-recent commentCard texts for one card (newest first)."""
    try:
        actions = trello_get(
            f"cards/{card_id}/actions", filter="commentCard", limit=limit,
        )
    except Exception as e:
        log.warning("Failed comments for card %s: %s", card_id, e)
        return []
    out = []
    for a in actions:
        txt = a.get("data", {}).get("text", "").strip()
        if txt:
            out.append(txt)
    return out


def priority_of(board: dict, card: dict) -> int:
    for lbl in card.get("labels", []):
        p = board["labels"].get(lbl.get("name", ""))
        if p:
            return p
    return board["default_pri"]


def due_status(due_str: str | None) -> str:
    if not due_str:
        return "no-due"
    now = datetime.now(IST)
    due = datetime.fromisoformat(due_str.replace("Z", "+00:00")).astimezone(IST)
    delta = (due.date() - now.date()).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "today"
    if delta <= 2:
        return "due-soon"
    return "normal"


def checklist_summary(card: dict) -> dict:
    total   = card["badges"].get("checkItems", 0)
    checked = card["badges"].get("checkItemsChecked", 0)
    return {"total": total, "checked": checked, "pending": total - checked}


def sort_key(board: dict, card: dict):
    order = {"overdue": 0, "today": 1, "due-soon": 2, "normal": 3, "no-due": 4}
    return (priority_of(board, card), order[due_status(card.get("due"))])


# ── Card serialization for the LLM ────────────────────────────────────────────

SECTION_MAP = {1: "RED", 2: "AMBER", 3: "ORANGE", 4: "GREEN"}


def build_card_data_text(board: dict, cards: list[dict], with_comments: bool) -> str:
    """Serialize cards for the LLM. When with_comments, attach commentCard history
    for priority 1-2 cards (where the real status/figures live) — per the hard-won
    lesson that field-only prompts produce hollow, generic 'what's the status?' rows.
    """
    lines = []
    for c in sorted(cards, key=lambda x: sort_key(board, x)):
        cl  = checklist_summary(c)
        due = c.get("due", "None")
        pri = priority_of(board, c)
        block = (
            f"CARD: {c['name']}\n"
            f"  List: {c['_list_name']}\n"
            f"  Section: {pri} ({SECTION_MAP[pri]})\n"
            f"  Due: {due} | Status: {due_status(due)}\n"
            f"  Checklist: {cl['checked']}/{cl['total']} (pending: {cl['pending']})\n"
        )
        if with_comments and pri <= 2:
            comments = fetch_card_comments(c["id"])
            if comments:
                joined = "\n    - ".join(comments[:5])
                block += f"  Recent comments (newest first):\n    - {joined}\n"
        lines.append(block)
    return "\n".join(lines)


# ── Claude HTML generation ────────────────────────────────────────────────────

def _claude_client():
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def generate_html_with_claude(prompt: str) -> str:
    client = _claude_client()
    chunks = []
    with client.messages.stream(
        model="claude-haiku-4-5-20251001", max_tokens=24000,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
        final = stream.get_final_message()
        log.info("Claude stop_reason=%s tokens=%s", final.stop_reason, final.usage.output_tokens)
    html = "".join(chunks).strip()
    if html.startswith("```"):
        html = html.split("\n", 1)[1] if "\n" in html else html
        if html.endswith("```"):
            html = html[:-3].rstrip()
    return html


# ── Prompt templates ──────────────────────────────────────────────────────────
# {board_name} = friendly board name, {owner} = owner first name, {meeting_label}
# = calendar title, plus the existing placeholders.

OWNER_PROMPT_TEMPLATE = """
You are an HTML email generator. Output a COMPLETE, self-contained HTML email for {owner}'s pre-meeting digest for the {board_name} board.
Output ONLY raw HTML — no markdown, no code fences, no explanation.
CRITICAL: Use ONLY inline styles with hardcoded hex colors — NO CSS classes, NO <style> blocks, NO CSS variables, NO flex/grid. This is rendered in Gmail which strips all <style> blocks.

BOARD: {board_name}
MEETING: {meeting_label}
MEETING DATE: {meeting_date}
TODAY: {today}

CARD DATA:
{card_data}

COLOR REFERENCE (use these exact hex values inline):
- Navy bg: #1C2A3A  Navy mid: #2C3E50  Page bg: #F0F2F5
- Red: #B03A2E  Red light: #FBEAE9  Red bar: #C0392B
- Amber: #9A7D0A  Amber light: #FEFDE7  Amber bar: #D4AC0D
- Orange: #A04000  Orange light: #FEF5E7  Orange bar: #CA6F1E
- Green: #1A5C35  Green light: #EAFAF1  Green bar: #1E8449
- Grid: #D5D8DC  Text: #1C1C1C  Meta: #566573

Build a clean table-based email:
1. A navy header bar naming the board and "Pre-Meeting Prep".
2. A short intro greeting {owner}, noting the {meeting_label} meeting on {meeting_date}, asking them to update each card before the meeting; OVERDUE items in red need immediate attention.
3. Four priority sections (1=red IMPORTANT·URGENT, 2=amber IMPORTANT·NOT URGENT, 3=orange URGENT·NOT IMPORTANT, 4=green NOT URGENT·NOT IMPORTANT). Each section is a <table> with columns Card | List | Due Date | Checklist. Use the section's bg color for the header row and alternate even rows with the section's light color. Show OVERDUE as a red badge, render checklist as a progress bar + "done/total" + pending/Complete/Not-started note. Omit a section if it has no cards.
4. A closing "Please update before meeting" attention box listing overdue / 0%-progress / due-soon cards with a one-line action each.
5. A small footer: "Valuecart Automation · Generated {today} · Sent 2 days before meeting".
"""

SONAL_PROMPT_TEMPLATE = """
You are an HTML email generator. Output a COMPLETE, self-contained HTML email for Sonal's key-discussion digest for the {board_name} board.
Output ONLY raw HTML — no markdown, no code fences, no explanation.
CRITICAL: Use ONLY inline styles with hardcoded hex colors — NO CSS classes, NO <style> blocks, NO CSS variables, NO flex/grid. This is rendered in Gmail which strips all <style> blocks.

BOARD: {board_name}
MEETING: {meeting_label}
MEETING TIME (IST): {meeting_time}
TODAY: {today}

CARD DATA (each card has Section 1-4; priority cards include "Recent comments"):
{card_data}

THE MOST IMPORTANT RULE: For each priority card, write a 1-2 sentence "Ask {owner}" discussion prompt GROUNDED IN THE CARD'S RECENT COMMENTS — quote the real figures, names, blockers and ETAs from the comments (e.g. amounts, deadlines, who owns what). NEVER write a generic "what's the status?" — a prompt with no specific fact from the comments is a failure. If a card has no comments, write the sharpest prompt you can from its name/due/checklist.

SECTION RULES:
- Section 1 (priority=1): "IMPORTANT · URGENT" — header bg #B03A2E, even-row bg #FBEAE9
- Section 2 (priority=2): "IMPORTANT · NOT URGENT" — header bg #9A7D0A, even-row bg #FEFDE7
- Section 3 (priority=3): "URGENT · NOT IMPORTANT" — header bg #A04000, even-row bg #FEF5E7 — INCLUDE ONLY cards that are overdue OR have 0% checklist progress
- Section 4 (priority=4): "NOT URGENT · NOT IMPORTANT" — header bg #1A5C35 — NO table, one text line listing card names + checklist %

LAYOUT:
1. A red-accented masthead: "{board_name} Meeting — Key Discussion Areas", with "{meeting_label} meeting today · {today} · For Sonal's reference only".
2. Intro greeting Sonal, noting the meeting is in ~2 hours at {meeting_time} IST.
3. Five stat tiles: Total Open / Immediate Action (S1) / Needs Involvement (S2) / Overdue Items / On Track.
4. A red "Must-Discuss Today" box: one bullet per Section-1 card (plus any overdue 0%-progress S2/S3 card) — card name in bold + one sharp sentence drawn from its comments on what decision/update is needed from {owner}.
5. Section tables 1-3 with columns Card | Due {owner_col}| Discussion Prompt. The Discussion Prompt cell shows a small "Ask {owner}" label then the verbatim question in italics. {person_lane_note}
6. Section 4 single-line summary.
7. Footer: "Valuecart Automation · Sent 2 hours before meeting · For Sonal's eyes only".
"""


# ── Per-board jobs ────────────────────────────────────────────────────────────

def owner_first(board: dict) -> str:
    return board["owner_email"].split("@")[0].capitalize()


def board_name(board: dict) -> str:
    return board["key"].capitalize()


def job_owner_digest(svc, board: dict, run_now: bool):
    bk = board["key"]
    now = datetime.now(IST)
    target = now if run_now else now + timedelta(days=2)
    event = find_meeting(svc, board["cal_title"], target)
    if not event:
        log.info("[%s] No meeting %s — owner digest skipped.",
                 bk, "today (RUN_NOW)" if run_now else "in 2 days")
        return
    meeting_date = target.strftime("%A, %d %B %Y") + (" [TEST]" if run_now else "")
    log.info("[%s] Owner meeting on %s — building.", bk, meeting_date)

    owner_to, _ = recipients_for(board)
    cards = fetch_all_open_cards(board)
    if not cards:
        send_email(owner_to, f"{board_name(board)} (Trello) — Pre-Meeting Prep | {meeting_date}",
                   "<p>All clear — no open cards on the board.</p>")
        return
    card_data = build_card_data_text(board, cards, with_comments=False)
    prompt = OWNER_PROMPT_TEMPLATE.format(
        board_name=board_name(board), owner=owner_first(board),
        meeting_label=event.get("summary", board["cal_title"]),
        meeting_date=meeting_date, today=now.strftime("%A, %d %B %Y"),
        card_data=card_data,
    )
    html = generate_html_with_claude(prompt)
    send_email(owner_to, f"{board_name(board)} (Trello) — Pre-Meeting Prep | {meeting_date}", html)
    log.info("[%s] Owner digest sent to %s.", bk, owner_to)


def job_sonal_digest(svc, board: dict):
    bk = board["key"]
    now = datetime.now(IST)
    event = find_meeting(svc, board["cal_title"], now)
    if not event:
        log.info("[%s] No meeting today — Sonal digest skipped.", bk)
        return

    start = event["start"]
    if "dateTime" in start:
        meeting_dt = datetime.fromisoformat(start["dateTime"]).astimezone(IST)
        meeting_time = meeting_dt.strftime("%I:%M %p")
    else:
        meeting_time = "time unspecified"
    log.info("[%s] Sonal meeting today at %s — building.", bk, meeting_time)

    _, sonal_to = recipients_for(board)
    cards = fetch_all_open_cards(board)
    if not cards:
        send_email(sonal_to, f"{board_name(board)} Meeting Today — Key Discussion Areas | {meeting_time} IST",
                   "<p>All clear — no open cards on the board.</p>")
        return
    card_data = build_card_data_text(board, cards, with_comments=True)
    person_lane = board.get("person_lane")
    prompt = SONAL_PROMPT_TEMPLATE.format(
        board_name=board_name(board), owner=owner_first(board),
        meeting_label=event.get("summary", board["cal_title"]),
        meeting_time=meeting_time, today=now.strftime("%A, %d %B %Y"),
        card_data=card_data,
        owner_col="| Owner " if person_lane else "",
        person_lane_note="This board's lists are per-person — add an Owner column showing each card's list (person) name." if person_lane else "",
    )
    html = generate_html_with_claude(prompt)
    send_email(sonal_to, f"{board_name(board)} Meeting Today — Key Discussion Areas | {meeting_time} IST", html)
    log.info("[%s] Sonal digest sent to %s.", bk, sonal_to)


# ── Entry point (Railway Cron: run once, then exit) ───────────────────────────

def run_once():
    log.info("=== Digest run @ %s | TEST_MODE=%s ===", datetime.now(IST).isoformat(), TEST_MODE)
    run_now = os.environ.get("RUN_NOW") == "true"  # test against TODAY instead of +2d
    svc = _gcal_service()
    failures = []
    for board in BOARDS:
        for label, fn in (
            ("owner", lambda b=board: job_owner_digest(svc, b, run_now)),
            ("sonal", lambda b=board: job_sonal_digest(svc, b)),
        ):
            try:
                fn()
            except Exception:
                failures.append(f"{board['key']}:{label}")
                log.error("Job %s:%s FAILED:\n%s", board["key"], label, traceback.format_exc())
    if failures:
        log.error("Run finished WITH FAILURES: %s", ", ".join(failures))
        sys.exit(1)
    log.info("=== Digest run finished OK ===")


if __name__ == "__main__":
    run_once()
