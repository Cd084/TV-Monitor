"""Disk scanning, TheTVDB comparison and the scan job itself."""
import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta

from .db import now_iso
from .tvdb import TVDBClient, TVDBError, TVDBNotFound

log = logging.getLogger(__name__)

SHOW_RE = re.compile(r"\{tvdb-(\d+)\}", re.I)
# S01E02, S01.E02, s1e2
SXE_RE = re.compile(r"(?<![a-z0-9])s(\d{1,3})[ ._-]?e(\d{1,4})(?!\d)", re.I)
# Extra episodes after the first: E03, -E03, - S01E03, or a range like -03
TAIL_RE = re.compile(
    r"(?:[ ._]?-?[ ._]?(?:s\d{1,3})?e(?P<e>\d{1,4})|-(?P<r>\d{1,4}))(?!\d)", re.I
)
# 1x02 style
XFORMAT_RE = re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?![0-9a-z])", re.I)
# Daily shows named by air date: 2024-05-01 / 2024.05.01
DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[ ._-](\d{2})[ ._-](\d{2})(?!\d)")

SKIP_DIRS = {
    "$recycle.bin", "system volume information", "lost+found", "@eadir",
    ".snapshots", ".trash", ".trashes", ".recycle",
}

# Shared with the web UI so it can show live progress.
progress = {"running": False, "trigger": None, "started_at": None,
            "current": 0, "total": 0, "show": ""}
_scan_lock = threading.Lock()


class ScanAbort(Exception):
    pass


# -- parsing ------------------------------------------------------------------

def parse_episodes(filename):
    """Return [(season, episode), ...] found in a file name."""
    m = SXE_RE.search(filename)
    if m:
        season, numbers, pos = int(m.group(1)), [int(m.group(2))], m.end()
        while True:
            t = TAIL_RE.match(filename, pos)
            if not t:
                break
            if t.group("e") is not None:
                numbers.append(int(t.group("e")))
            else:
                n, last = int(t.group("r")), numbers[-1]
                if not last < n <= last + 20:
                    break
                numbers.extend(range(last + 1, n + 1))
            pos = t.end()
        return [(season, n) for n in numbers]
    m = XFORMAT_RE.search(filename)
    if m:
        return [(int(m.group(1)), int(m.group(2)))]
    return []


def parse_air_date(filename):
    m = DATE_RE.search(filename)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def parse_extensions(value):
    return {e.strip().lower().lstrip(".") for e in value.split(",") if e.strip()}


def display_name(folder_name):
    return SHOW_RE.sub("", folder_name).strip(" -_")


# -- disk ---------------------------------------------------------------------

def find_show_folders(root, depth=1):
    """Find 'Show Name {tvdb-123}' folders in root, or one level below it.

    The extra level means a drive root (containing a TVShows folder) works as
    well as the TVShows folder itself. Raises OSError if root is unreadable.
    """
    found = []
    for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        m = SHOW_RE.search(entry.name)
        if m:
            found.append((int(m.group(1)), entry.path, entry.name))
        elif depth > 0 and entry.name.lower() not in SKIP_DIRS and not entry.name.startswith("."):
            try:
                found.extend(find_show_folders(entry.path, depth - 1))
            except OSError:
                pass
    return found


def scan_show_files(folders, extensions):
    """Return (set of (season, episode), set of air dates, video file count)."""
    have, dates, count = set(), set(), 0
    for folder in folders:
        for _dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower().lstrip(".") not in extensions:
                    continue
                count += 1
                episodes = parse_episodes(fn)
                if episodes:
                    have.update(episodes)
                else:
                    aired = parse_air_date(fn)
                    if aired:
                        dates.add(aired)
    return have, dates, count


# -- comparison ---------------------------------------------------------------

def _to_date(value):
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def compute_missing(episodes, have, have_dates, include_specials, min_season, grace_days, today=None):
    """Episodes that have aired (plus grace days) but are not on disk."""
    today = today or date.today()
    missing = []
    for ep in episodes:
        season, number = ep.get("season"), ep.get("number")
        if season is None or number is None or ep.get("id") is None:
            continue
        if season == 0:
            if not include_specials:
                continue
        elif season < min_season:
            continue
        aired = _to_date(ep.get("aired"))
        if aired is None or aired + timedelta(days=grace_days) > today:
            continue  # unaired, or no air date known
        if (season, number) in have or aired.isoformat() in have_dates:
            continue
        missing.append(ep)
    return missing


# -- TheTVDB with cache -------------------------------------------------------

def get_series_data(db, client, series_id, season_type, language, cache_hours):
    row = db.query_one(
        "SELECT * FROM tvdb_cache WHERE series_id = ? AND season_type = ? AND language = ?",
        (series_id, season_type, language),
    )
    if row:
        age = datetime.now() - datetime.fromisoformat(row["fetched_at"])
        if age < timedelta(hours=cache_hours):
            return json.loads(row["series_json"]), json.loads(row["episodes_json"])
    try:
        series, episodes = client.get_series_episodes(series_id, season_type, language)
    except TVDBNotFound:
        raise
    except TVDBError:
        if row:  # TheTVDB is having a bad day; stale data beats no data
            log.warning("Using cached TheTVDB data for %s", series_id)
            return json.loads(row["series_json"]), json.loads(row["episodes_json"])
        raise
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tvdb_cache(series_id, season_type, language, fetched_at, series_json, episodes_json) "
            "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(series_id, season_type, language) DO UPDATE SET "
            "fetched_at = excluded.fetched_at, series_json = excluded.series_json, "
            "episodes_json = excluded.episodes_json",
            (series_id, season_type, language, now_iso(), json.dumps(series), json.dumps(episodes)),
        )
    return series, episodes


# -- scan job -----------------------------------------------------------------

def _upsert_show(conn, tvdb_id, info, **fields):
    values = {
        "folder_name": info["folder_name"],
        "folders": json.dumps(info["folders"]),
        "present": 1,
        "last_scanned": now_iso(),
    }
    values.update(fields)
    cols = ["tvdb_id"] + list(values)
    conn.execute(
        f"INSERT INTO shows({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT(tvdb_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in values)}",
        [tvdb_id] + list(values.values()),
    )


def _save_missing(conn, tvdb_id, missing):
    first_seen = {
        r["episode_id"]: r["first_detected"]
        for r in conn.execute(
            "SELECT episode_id, first_detected FROM missing_episodes WHERE tvdb_id = ?", (tvdb_id,)
        )
    }
    conn.execute("DELETE FROM missing_episodes WHERE tvdb_id = ?", (tvdb_id,))
    stamp = now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO missing_episodes"
        "(episode_id, tvdb_id, season, episode, name, aired, overview, first_detected) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (e["id"], tvdb_id, e["season"], e["number"], e["name"], e["aired"][:10],
             e["overview"], first_seen.get(e["id"], stamp))
            for e in missing
        ],
    )


def _scan(db, trigger, client_factory):
    settings = db.get_settings()
    started = now_iso()
    with db.tx() as conn:
        run_id = conn.execute(
            "INSERT INTO scan_runs(started_at, trigger, status) VALUES(?, ?, 'running')",
            (started, trigger),
        ).lastrowid
    progress.update(running=True, trigger=trigger, started_at=started, current=0, total=0, show="")

    warnings, errors, scanned, total_missing = [], 0, 0, 0
    status, message = "ok", ""
    try:
        if not settings["tvdb_api_key"].strip():
            raise ScanAbort("TheTVDB API key is not set (Settings).")
        paths = db.scan_paths(enabled_only=True)
        if not paths:
            raise ScanAbort("No scan locations are configured (Settings).")

        extensions = parse_extensions(settings["video_extensions"])
        include_specials = settings["include_specials"] == "1"
        default_min_season = max(1, int(settings["min_season"] or 1))
        grace_days = max(0, int(settings["grace_days"] or 0))
        cache_hours = max(0, float(settings["cache_hours"] or 0))
        season_type = settings["season_type"] or "default"
        language = settings["language"].strip()

        # A show may be spread across several drives in the pool: merge by id.
        shows = {}
        for p in paths:
            try:
                for tvdb_id, folder, name in find_show_folders(p["path"]):
                    info = shows.setdefault(tvdb_id, {"folder_name": name, "folders": []})
                    info["folders"].append(folder)
            except OSError as e:
                warnings.append(f"Scan location unavailable: {p['path']} ({e.strerror or e})")

        client = client_factory(settings["tvdb_api_key"], settings["tvdb_pin"])
        try:
            client.login()
        except TVDBError as e:
            raise ScanAbort(str(e))

        overrides = {r["tvdb_id"]: r for r in db.query("SELECT tvdb_id, min_season, ignored FROM shows")}
        progress["total"] = len(shows)
        ordered = sorted(shows.items(), key=lambda kv: kv[1]["folder_name"].lower())
        for i, (tvdb_id, info) in enumerate(ordered, 1):
            progress.update(current=i, show=display_name(info["folder_name"]))
            cfg = overrides.get(tvdb_id) or {}
            try:
                have, have_dates, file_count = scan_show_files(info["folders"], extensions)
                if cfg.get("ignored"):
                    with db.tx() as conn:
                        _upsert_show(conn, tvdb_id, info, episodes_on_disk=file_count,
                                     missing_count=0, last_error=None)
                        conn.execute("DELETE FROM missing_episodes WHERE tvdb_id = ?", (tvdb_id,))
                    continue
                series, episodes = get_series_data(db, client, tvdb_id, season_type, language, cache_hours)
                min_season = cfg.get("min_season") or default_min_season
                missing = compute_missing(episodes, have, have_dates, include_specials,
                                          min_season, grace_days)
                with db.tx() as conn:
                    _upsert_show(conn, tvdb_id, info,
                                 name=series.get("name") or display_name(info["folder_name"]),
                                 slug=series.get("slug") or "", status=series.get("status") or "",
                                 episodes_on_disk=file_count, missing_count=len(missing),
                                 last_error=None)
                    _save_missing(conn, tvdb_id, missing)
                scanned += 1
                total_missing += len(missing)
            except Exception as e:  # one bad show shouldn't stop the scan
                errors += 1
                log.exception("Failed to check %s", info["folder_name"])
                with db.tx() as conn:
                    _upsert_show(conn, tvdb_id, info, last_error=str(e))

        # Shows no longer found anywhere (deleted, or their drive is offline).
        with db.tx() as conn:
            seen = list(shows)
            marks = ",".join("?" for _ in seen) or "NULL"
            conn.execute(f"UPDATE shows SET present = 0 WHERE tvdb_id NOT IN ({marks})", seen)
            conn.execute(
                f"DELETE FROM missing_episodes WHERE tvdb_id NOT IN ({marks})", seen
            )

        parts = [f"Checked {scanned} of {len(shows)} shows, {total_missing} missing episodes."]
        if errors:
            parts.append(f"{errors} show(s) failed - see the Shows page.")
        parts.extend(warnings)
        message = " ".join(parts)
        status = "warning" if errors or warnings else "ok"
    except ScanAbort as e:
        status, message = "failed", str(e)
    except Exception as e:
        log.exception("Scan failed")
        status, message = "failed", f"Unexpected error: {e}"
    finally:
        with db.tx() as conn:
            conn.execute(
                "UPDATE scan_runs SET finished_at = ?, status = ?, shows_scanned = ?, "
                "missing_found = ?, errors = ?, message = ? WHERE id = ?",
                (now_iso(), status, scanned, total_missing, errors, message, run_id),
            )
        progress.update(running=False, show="")
        log.info("Scan finished (%s): %s", status, message)
    return status


def run_scan(db, trigger="scheduled", client_factory=TVDBClient):
    """Run a scan in the calling thread. Returns None if one is already running."""
    if not _scan_lock.acquire(blocking=False):
        return None
    try:
        return _scan(db, trigger, client_factory)
    finally:
        _scan_lock.release()


def start_scan_async(db, trigger="manual", client_factory=TVDBClient):
    """Start a scan in the background. Returns False if one is already running."""
    if not _scan_lock.acquire(blocking=False):
        return False

    def worker():
        try:
            _scan(db, trigger, client_factory)
        finally:
            _scan_lock.release()

    threading.Thread(target=worker, name="scan", daemon=True).start()
    return True
