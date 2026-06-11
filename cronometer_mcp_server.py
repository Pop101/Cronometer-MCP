#!/usr/bin/env python3
"""
Cronometer MCP Server — uses the Cronometer mobile REST API.
No Gold subscription required. Standard email/password login.

API: POST /api/v2/* via JSON-RPC (auth block in body)
     DELETE /api/v3/user/{id}/* via REST (x-crono-session header)
"""
import os, json, logging, sys, time, uuid
from datetime import date as _date_cls, datetime, timedelta
from typing import Optional
import httpx
from mcp.server.fastmcp import FastMCP

# ── config (reads from ~/.hermes/.env.cronometer) ──────────────────
_env_path = os.path.expanduser("~/.hermes/.env.cronometer")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v)

BASE_URL = "https://mobile.cronometer.com"
_DEVICE = "Android 14 (SDK 34), Google Pixel 6 Pro"

mcp = FastMCP("cronometer", instructions="""Cronometer nutrition via mobile API (no Gold needed).
Tools: search_foods, get_food_details, get_food_log, get_daily_nutrition,
add_food_entry, remove_food_entry, mark_day_complete, copy_day,
get_macro_targets, get_fasting_history, get_fasting_stats.""")

http = httpx.Client(timeout=30)
log = logging.getLogger("cronometer")


def _get_app_auth():
    """Build app auth dict. Called at runtime, not import time."""
    return {"api": 3, "os": "Android", "build": "2807", "flavour": "free"}

def _get_creds():
    """Read credentials from env at runtime."""
    user = os.environ.get("CRONOMETER_USERNAME") or os.environ.get("CRONOMETER_EMAIL")
    pw = os.environ.get("CRONOMETER_PASSWORD")
    if not user or not pw:
        raise RuntimeError("Set CRONOMETER_USERNAME and CRONOMETER_PASSWORD in ~/.hermes/.env.cronometer")
    return user, pw


# ── client ──────────────────────────────────────────────────────────
class CronoClient:
    def __init__(self):
        self._user_id = None
        self._token = None
        self._headers = {}
        self._login_attempted = False
        self._recent_adds = {}
        self._local_diary = {}

    def _ensure_auth(self):
        if not self._login_attempted:
            self._login_attempted = True
            self._login()

    def _auth_block(self):
        return {"userId": self._user_id, "token": self._token, **_get_app_auth()}

    def _login(self):
        username, password = _get_creds()
        app_auth = _get_app_auth()
        for attempt in range(3):
            try:
                payload = {
                    "email": username, "password": password,
                    "timezone": "America/Los_Angeles", "userCode": None,
                    "build": "4.48.2 b2807-a", "device": _DEVICE,
                    "firebaseToken": "", "features": {},
                    "auth": {"userId": None, "token": None, **app_auth},
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
                log.info(f"Logged in: userId={self._user_id}")
                today = _date_cls.today().isoformat()
                self._cache_login_diary(data, today)
                return
            except (httpx.HTTPStatusError, httpx.TimeoutException, ConnectionError) as e:
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise Exception("Login failed after 3 attempts")

    def _cache_login_diary(self, data, day):
        diaries = data.get("diaries", [])
        if diaries:
            d0 = diaries[0]
            diary_entries = d0.get("diary", [])
            entries = [{
                "id": s.get("servingId"),
                "food_id": s.get("foodId"),
                "food_name": s.get("foodName", ""),
                "measure_id": s.get("measureId"),
                "grams": s.get("grams", 0),
                "group": s.get("order", 0),
                "source": "api",
            } for s in diary_entries if s.get("foodId")]
            local = self._local_diary.get(day, [])
            seen = {(e["food_id"], e["measure_id"], round(e["grams"], 1)) for e in entries}
            for le in local:
                key = (le["food_id"], le["measure_id"], round(le["grams"], 1))
                if key not in seen:
                    entries.append(le)
            self._local_diary[day] = entries

    def _v2(self, endpoint, **extra):
        payload = {"auth": self._auth_block(), **extra}
        r = http.post(f"{BASE_URL}/api/v2/{endpoint}", json=payload, headers=self._headers)
        if r.status_code in (401, 403):
            log.warning(f"{endpoint}: auth expired, re-logging in")
            self._login()
            payload["auth"] = self._auth_block()
            r = http.post(f"{BASE_URL}/api/v2/{endpoint}", json=payload, headers=self._headers)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("result") == "FAIL":
                raise Exception(f"Cronometer API error: {j.get('error', 'unknown')}")
        r.raise_for_status()
        return r.json() if r.status_code == 200 else {}

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

    def get_diary(self, day: str):
        entries = self._local_diary.get(day, [])
        energy_summary = {}
        try:
            nuts = self._v2("get_nutrients", date=day)
            for n in nuts.get("nutrients", []):
                name = n.get("name", "")
                if name == "Energy":
                    energy_summary["target_kcal"] = n.get("rdi_AMERICAN", n.get("rdi", 2000))
                elif name == "Protein":
                    energy_summary["target_protein_g"] = n.get("rdi_AMERICAN", n.get("rdi", 50))
                elif name == "Fat":
                    energy_summary["target_fat_g"] = n.get("rdi_AMERICAN", n.get("rdi", 65))
                elif name == "Carbs":
                    energy_summary["target_carbs_g"] = n.get("rdi_AMERICAN", n.get("rdi", 300))
        except Exception as e:
            log.warning(f"get_nutrients failed for {day}: {e}")
        return {"date": day, "entries": entries, "energy_summary": energy_summary, "entry_count": len(entries)}

    def get_nutrients(self, day: str):
        return self._v2("get_nutrients", date=day)

    def add_serving(self, food_id: int, measure_id: int, grams: float,
                    day: str, diary_group: int = 0):
        dedup_key = (food_id, measure_id, round(grams, 1), day)
        now = time.time()

        # CHECK 1: Cooldown
        last_added = self._recent_adds.get(dedup_key)
        if last_added and (now - last_added) < 60:
            return {"skipped": True, "reason": "duplicate_cooldown",
                    "note": f"Same food+amount added to {day} <60s ago. Skipped."}

        # CHECK 2: Already in local diary
        for e in self._local_diary.get(day, []):
            if (e.get("food_id") == food_id
                    and e.get("measure_id") == measure_id
                    and abs(e.get("grams", 0) - grams) < 0.5):
                return {"skipped": True, "reason": "already_in_diary",
                        "note": f"Food {food_id} ({grams}g) already logged on {day}."}

        # CHECK 3: Idempotency key
        idem_key = str(uuid.uuid4())
        serving = dict(userId=self._user_id, foodId=food_id, measureId=measure_id,
                       grams=grams, day=day, diaryGroup=diary_group,
                       order=(diary_group << 16) | 1, idempotencyKey=idem_key)
        result = self._v2("add_serving", serving=serving)

        # Record locally
        entry = {"food_id": food_id, "measure_id": measure_id, "grams": grams,
                 "group": diary_group, "source": "mcp"}
        try:
            food_data = self._v2("get_food", id=food_id)
            entry["food_name"] = food_data.get("name", "")
        except Exception:
            entry["food_name"] = ""
        self._local_diary.setdefault(day, []).append(entry)
        self._recent_adds[dedup_key] = now
        return result

    def remove_serving(self, serving_id: int):
        result = self._v2("delete_serving", servingId=serving_id)
        for day, entries in self._local_diary.items():
            self._local_diary[day] = [e for e in entries if e.get("id") != serving_id]
        return result

    def mark_day_complete(self, day: str, complete: bool = True):
        return self._v2("set_complete", date=day, complete=complete)

    def copy_day(self, from_day: str, to_day: str):
        return self._v2("copy", fromDate=from_day, toDate=to_day)

    def get_macro_schedules(self):
        return self._v2("get_macro_schedules")

    def get_macro_target_templates(self):
        return self._v2("get_macro_target_templates")

    def get_fasting_history(self, start: str, end: str):
        return self._v2("get_fasting_with_date_range", startDate=start, endDate=end)

    def get_fasting_stats(self):
        return self._v2("get_fasting_stats")

    def get_nutrition_scores(self, day: str):
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
def _today():
    return _date_cls.today().isoformat()

def _parse_date(d: Optional[str]) -> str:
    if d is None:
        return _today()
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return d
    except ValueError:
        return _today()

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
    Includes energy_summary with target calories and entry list."""
    try:
        d = _parse_date(date)
        return _ok(_get_client().get_diary(d))
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_daily_nutrition(date: Optional[str] = None) -> str:
    """Get daily macro and micronutrient targets for a date (YYYY-MM-DD).
    Note: Returns RDI targets. Use get_food_log for actual intake."""
    try:
        d = _parse_date(date)
        return _ok(_get_client().get_nutrients(d))
    except Exception as e:
        return _err(e)

@mcp.tool()
def get_nutrition_scores(date: Optional[str] = None) -> str:
    """Get nutrition category scores (Vitamins, Minerals, etc.) with consumed amounts."""
    try:
        d = _parse_date(date)
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
        d = _parse_date(date)
        result = _get_client().add_serving(food_id, measure_id, grams, d, g)
        return _ok({"entry": result, "note": "Logged successfully"})
    except Exception as e:
        return _err(e)

@mcp.tool()
def remove_food_entry(serving_id: int) -> str:
    """Remove a food entry from your Cronometer diary by its serving ID.
    Use get_food_log to find serving IDs first."""
    try:
        return _ok(_get_client().remove_serving(serving_id))
    except Exception as e:
        return _err(e)

@mcp.tool()
def mark_day_complete(date: Optional[str] = None, complete: bool = True) -> str:
    """Mark a diary day as complete or incomplete."""
    try:
        d = _parse_date(date)
        return _ok(_get_client().mark_day_complete(d, complete))
    except Exception as e:
        return _err(e)

@mcp.tool()
def copy_day(date: Optional[str] = None) -> str:
    """Copy all entries from the previous day to the given date (or today)."""
    try:
        d = _parse_date(date)
        from_day = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        return _ok(_get_client().copy_day(from_day=from_day, to_day=d))
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
        s = _parse_date(start_date) if start_date else (_date_cls.today() - timedelta(days=30)).isoformat()
        e = _parse_date(end_date) if end_date else _today()
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
