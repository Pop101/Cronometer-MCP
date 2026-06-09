#!/usr/bin/env python3
"""
Cronometer MCP Server — uses the Cronometer mobile REST API.
No Gold subscription required. Standard email/password login.

API: POST /api/v2/* via JSON-RPC (auth block in body)
     DELETE /api/v3/user/{id}/* via REST (x-crono-session header)
"""
import os, json, logging, sys, time
from datetime import date, datetime, timedelta
from typing import Optional
import httpx
from mcp.server.fastmcp import FastMCP

# ── config ──────────────────────────────────────────────────────────
BASE_URL = "https://mobile.cronometer.com"
USERNAME = os.environ.get("CRONOMETER_USERNAME") or os.environ.get("CRONOMETER_EMAIL")
PASSWORD = os.environ.get("CRONOMETER_PASSWORD")
if not USERNAME or not PASSWORD:
    print("ERROR: Set CRONOMETER_USERNAME and CRONOMETER_PASSWORD", file=sys.stderr)
    sys.exit(1)

_APP_AUTH = {"api": 3, "os": "Android", "build": "2807", "flavour": "free"}
_DEVICE = "Android 14 (SDK 34), Google Pixel 6 Pro"

mcp = FastMCP("cronometer", instructions="""Cronometer nutrition via mobile API (no Gold needed).
Tools: search_foods, get_food_details, get_food_log, get_daily_nutrition,
add_food_entry, remove_food_entry, mark_day_complete, copy_day,
get_macro_targets, get_fasting_history, get_fasting_stats.""")

http = httpx.Client(timeout=30)


# ── client ──────────────────────────────────────────────────────────
class CronoClient:
    def __init__(self):
        self._user_id = None
        self._token = None
        self._headers = {}
        self._last_diary = None
        self._last_diary_date = None
        self._login_attempted = False

    def _ensure_auth(self):
        if not self._login_attempted:
            self._login_attempted = True
            self._login()

    def _auth_block(self):
        return {"userId": self._user_id, "token": self._token, **_APP_AUTH}

    def _login(self):
        for attempt in range(3):
            try:
                payload = {
                    "email": USERNAME, "password": PASSWORD,
                    "timezone": "America/Los_Angeles", "userCode": None,
                    "build": "4.48.2 b2807-a", "device": _DEVICE,
                    "firebaseToken": "", "features": {},
                    "auth": {"userId": None, "token": None, **_APP_AUTH},
                    "lastSeen": 0, "config": {"call_version": 2},
                }
                r = http.post(f"{BASE_URL}/api/v2/login", json=payload)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, dict) and data.get("result") == "FAIL":
                    err = data.get("error", "")
                    if "Too Many Attempts" in err and attempt < 2:
                        time.sleep(5 * (attempt + 1))
                        continue
                    raise Exception(f"Login failed: {err}")
                self._user_id = data["id"]
                self._token = data["sessionKey"]
                self._headers = {"x-crono-session": self._token, "content-type": "application/json"}
                today = date.today().isoformat()
                self._cache_diary(data, today)
                return
            except (httpx.HTTPStatusError, httpx.TimeoutException, ConnectionError) as e:
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise Exception("Login failed after 3 attempts")

    def _cache_diary(self, data, day):
        diaries = data.get("diaries", [])
        if diaries:
            d0 = diaries[0]
            summary = d0.get("summary", {}).get("consumed", {})
            targets = d0.get("summary", {}).get("macros", {})
            diary_entries = d0.get("diary", [])
            self._last_diary = {
                "date": day,
                "energy_summary": {
                    "consumed_kcal": summary.get("total", 0),
                    "target_kcal": targets.get("energy", 0),
                    "remaining_kcal": targets.get("energy", 0) - summary.get("total", 0),
                    "protein_g": summary.get("protein_g", 0),
                    "carbs_g": summary.get("carbs_g", 0),
                    "fat_g": summary.get("fat_g", 0),
                },
                "entries": [{
                    "id": s.get("servingId"),
                    "food_id": s.get("foodId"),
                    "food_name": s.get("foodName", ""),
                    "measure_id": s.get("measureId"),
                    "grams": s.get("grams", 0),
                    "meals": s.get("meals", ""),
                    "group": s.get("order", 0),
                } for s in diary_entries if s.get("foodId")],
            }
            self._last_diary_date = day

    def _v2(self, endpoint, **extra):
        payload = {"auth": self._auth_block(), **extra}
        r = http.post(f"{BASE_URL}/api/v2/{endpoint}", json=payload, headers=self._headers)
        if r.status_code in (401, 403):
            self._login()
            payload["auth"] = self._auth_block()
            r = http.post(f"{BASE_URL}/api/v2/{endpoint}", json=payload, headers=self._headers)
        # Some endpoints return 200 with {result: FAIL} instead of error HTTP codes
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("result") == "FAIL":
                raise Exception(f"Cronometer API error: {j.get('error', 'unknown')}")
        r.raise_for_status()
        return r.json() if r.status_code == 200 else {}

    # ── food search (correct endpoint: find_food, field: foods) ───
    def search_food(self, query: str, limit: int = 15):
        data = self._v2("find_food", query=query, tab="ALL", sources=["All"])
        results = data.get("foods", [])[:limit]
        return [{
            "food_id": r["id"],
            "name": r.get("name", ""),
            "source": r.get("source", ""),
            "measure_id": r.get("measureId"),
            "measure_display": r.get("measureDisplayName", ""),
            "score": r.get("score", 0),
        } for r in results]

    def get_food(self, food_id: int):
        return self._v2("get_food", id=food_id)

    # ── diary (from login cache + get_nutrients) ──────────────────
    def get_diary(self, day: str = None):
        day = day or date.today().isoformat()
        # Use cached diary for today
        if day == self._last_diary_date and self._last_diary:
            return self._last_diary
        # For other days, re-login or try get_diary (might 500)
        # Fallback: use nutrients data
        try:
            # get_nutrients works — use it for macro summary
            nuts = self._v2("get_nutrients", date=day)
            return {
                "date": day,
                "energy_summary": {},
                "nutrients": nuts,
                "note": "Full diary unavailable for past dates; nutrient totals shown.",
            }
        except Exception as e:
            return {
                "date": day,
                "error": str(e),
                "note": "Could not retrieve diary for this date.",
            }

    def get_nutrients(self, day: str = None):
        day = day or date.today().isoformat()
        return self._v2("get_nutrients", date=day)

    def add_serving(self, food_id: int, measure_id: int, grams: float,
                    day: str = None, diary_group: int = 0):
        day = day or date.today().isoformat()
        serving = dict(userId=self._user_id, foodId=food_id, measureId=measure_id,
                       grams=grams, day=day, diaryGroup=diary_group,
                       order=(diary_group << 16) | 1)
        return self._v2("add_serving", serving=serving)

    def mark_day_complete(self, day: str = None, complete: bool = True):
        day = day or date.today().isoformat()
        return self._v2("set_complete", date=day, complete=complete)

    def copy_day(self, from_day: str = None, to_day: str = None):
        from_day = from_day or (date.today() - timedelta(days=1)).isoformat()
        to_day = to_day or date.today().isoformat()
        return self._v2("copy", fromDate=from_day, toDate=to_day)

    # ── macros ───────────────────────────────────────────────────
    def get_macro_schedules(self):
        return self._v2("get_macro_schedules")

    def get_macro_target_templates(self):
        return self._v2("get_macro_target_templates")

    # ── fasting ──────────────────────────────────────────────────
    def get_fasting_history(self, start: str = None, end: str = None):
        start = start or (date.today() - timedelta(days=30)).isoformat()
        end = end or date.today().isoformat()
        return self._v2("get_fasting_with_date_range", startDate=start, endDate=end)

    def get_fasting_stats(self):
        return self._v2("get_fasting_stats")

    # ── nutrition scores ─────────────────────────────────────────
    def get_nutrition_scores(self, day: str = None):
        day = day or date.today().isoformat()
        return self._v2("get_nutrition_scores", date=day)


# ── lazy init ───────────────────────────────────────────────────────
_client = None
def _get_client():
    global _client
    if _client is None:
        _client = CronoClient()
    _client._ensure_auth()
    return _client


# ── helpers ─────────────────────────────────────────────────────────
def _parse_date(d: Optional[str]) -> Optional[str]:
    if d is None:
        return None
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return d
    except ValueError:
        return None

def _ok(data) -> str:
    return json.dumps({"status": "success", "data": data})

def _err(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        s = e.response.status_code
        if s in (401, 403):
            msg = "Auth failed. Check your CRONOMETER_USERNAME/PASSWORD."
        elif s == 429:
            msg = "Rate limited. Wait a bit."
        else:
            msg = f"HTTP {s}"
    else:
        msg = f"{type(e).__name__}: {e}"
    return json.dumps({"status": "error", "message": msg})


# ══════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def search_foods(query: str, limit: int = 15) -> str:
    """Search the Cronometer food database by name."""
    try:
        return _ok(_get_client().search_food(query, limit))
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_food_details(food_id: int) -> str:
    """Get full nutrition profile and serving sizes for a food item."""
    try:
        f = _get_client().get_food(food_id)
        measures = [{"id": m.get("id"), "name": m.get("displayName", ""),
                     "grams": m.get("amount", 0)} for m in f.get("measures", [])]
        nutrients = {}
        for n in f.get("nutrients", []):
            name = n.get("name", f"nutrient_{n.get('nutrientId', 0)}")
            nutrients[name] = {"amount": n.get("amount", 0), "unit": n.get("unit", "")}
        return _ok({"name": f.get("name", ""), "measures": measures, "nutrients": nutrients})
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_food_log(date: Optional[str] = None) -> str:
    """Get diary entries for a date (YYYY-MM-DD, defaults to today).
    Includes energy_summary with remaining calories when available."""
    try:
        d = _parse_date(date) or date.today().isoformat()
        return _ok(_get_client().get_diary(d))
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_daily_nutrition(date: Optional[str] = None) -> str:
    """Get daily macro and micronutrient totals for a date (YYYY-MM-DD)."""
    try:
        d = _parse_date(date) or date.today().isoformat()
        return _ok(_get_client().get_nutrients(d))
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_nutrition_scores(date: Optional[str] = None) -> str:
    """Get nutrition category scores (Vitamins, Minerals, etc.) with consumed amounts."""
    try:
        d = _parse_date(date) or date.today().isoformat()
        return _ok(_get_client().get_nutrition_scores(d))
    except Exception as e:
        return _err(e)

@mcp.tool()
def add_food_entry(food_id: int, measure_id: int, grams: float,
                   date: Optional[str] = None,
                   diary_group: str = "auto") -> str:
    """Log a food serving to your Cronometer diary.
    diary_group: auto, breakfast, lunch, dinner, or snacks."""
    groups = {"auto": 0, "breakfast": 1, "lunch": 2, "dinner": 3, "snacks": 4}
    g = groups.get(diary_group.lower(), 0)
    try:
        d = _parse_date(date) or date.today().isoformat()
        result = _get_client().add_serving(food_id, measure_id, grams, d, g)
        return _ok({"entry": result, "note": "Logged successfully"})
    except Exception as e:
        return _err(e)

@mcp.tool()
def mark_day_complete(date: Optional[str] = None, complete: bool = True) -> str:
    """Mark a diary day as complete or incomplete."""
    try:
        d = _parse_date(date) or date.today().isoformat()
        return _ok(_get_client().mark_day_complete(d, complete))
    except Exception as e:
        return _err(e)

@mcp.tool()
def copy_day(date: Optional[str] = None) -> str:
    """Copy all entries from the previous day to the given date (or today)."""
    try:
        d = _parse_date(date) or date.today().isoformat()
        return _ok(_get_client().copy_day(to_day=d))
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_macro_targets() -> str:
    """Get weekly macro schedule and saved target templates."""
    try:
        schedules = _get_client().get_macro_schedules()
        templates = _get_client().get_macro_target_templates()
        return _ok({"schedules": schedules, "templates": templates})
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_fasting_history(start_date: Optional[str] = None,
                        end_date: Optional[str] = None) -> str:
    """View fasts within a date range (default: last 30 days)."""
    try:
        s = _parse_date(start_date)
        e = _parse_date(end_date)
        return _ok(_get_client().get_fasting_history(s, e))
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_fasting_stats() -> str:
    """Aggregate fasting statistics (total hours, longest, averages)."""
    try:
        return _ok(_get_client().get_fasting_stats())
    except Exception as e:
        return _err(e)


if __name__ == "__main__":
    mcp.run()
