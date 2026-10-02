"""Web UI and JSON API."""
import hmac
import json
import os
from datetime import date

from flask import (Blueprint, Response, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)

from . import scanner
from .db import now_iso
from .tvdb import TVDBClient, TVDBError

bp = Blueprint("web", __name__)

SEASON_TYPES = [("default", "Aired order"), ("dvd", "DVD order"), ("absolute", "Absolute order"),
                ("alternate", "Alternate order"), ("regional", "Regional order")]


def db():
    return current_app.config["DB"]


@bp.before_app_request
def require_auth():
    user, password = current_app.config["AUTH_USER"], current_app.config["AUTH_PASSWORD"]
    if not (user and password):
        return None
    auth = request.authorization
    if (auth and hmac.compare_digest(auth.username or "", user)
            and hmac.compare_digest(auth.password or "", password)):
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="TV Monitor"'})


# -- template helpers ---------------------------------------------------------

@bp.app_template_filter("epcode")
def epcode(row):
    return f"S{(row.get('season') or 0):02d}E{(row.get('episode') or 0):02d}"


@bp.app_template_filter("ago")
def ago(value):
    try:
        days = (date.today() - date.fromisoformat(value[:10])).days
    except (TypeError, ValueError):
        return ""
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


@bp.app_template_filter("when")
def when(value):
    return value.replace("T", " ")[:16] if value else ""


@bp.app_template_global()
def series_url(tvdb_id, slug):
    if slug:
        return f"https://thetvdb.com/series/{slug}"
    return f"https://thetvdb.com/dereferrer/series/{tvdb_id}"


# -- pages --------------------------------------------------------------------

@bp.route("/")
def index():
    episodes = db().query(
        "SELECT m.*, s.name AS show_name, s.folder_name, s.slug, f.flagged_at "
        "FROM missing_episodes m JOIN shows s ON s.tvdb_id = m.tvdb_id "
        "LEFT JOIN retrieval_flags f ON f.episode_id = m.episode_id "
        "WHERE s.ignored = 0 AND s.present = 1 "
        "ORDER BY m.aired DESC, s.name COLLATE NOCASE, m.season DESC, m.episode DESC"
    )
    for e in episodes:
        e["show_name"] = e["show_name"] or scanner.display_name(e["folder_name"])
    settings = db().get_settings()
    return render_template(
        "index.html",
        active="missing",
        episodes=episodes,
        queued=sum(1 for e in episodes if e["flagged_at"]),
        show_count=len({e["tvdb_id"] for e in episodes}),
        last_run=db().last_run(),
        has_key=bool(settings["tvdb_api_key"].strip()),
        has_paths=bool(db().scan_paths()),
    )


@bp.route("/shows")
def shows():
    rows = db().query(
        "SELECT * FROM shows ORDER BY present DESC, COALESCE(name, folder_name) COLLATE NOCASE"
    )
    for r in rows:
        r["folders"] = json.loads(r["folders"] or "[]")
        r["display"] = r["name"] or scanner.display_name(r["folder_name"] or "")
    return render_template("shows.html", active="shows", shows=rows,
                           default_min_season=db().get_settings()["min_season"])


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        f = request.form

        def as_int(name, lo, hi, fallback):
            try:
                return str(min(max(int(f.get(name, fallback)), lo), hi))
            except ValueError:
                return str(fallback)

        values = {
            "tvdb_api_key": f.get("tvdb_api_key", "").strip(),
            "tvdb_pin": f.get("tvdb_pin", "").strip(),
            "language": f.get("language", "").strip().lower()[:3],
            "season_type": f.get("season_type", "default"),
            "include_specials": "1" if f.get("include_specials") else "0",
            "min_season": as_int("min_season", 1, 999, 1),
            "grace_days": as_int("grace_days", 0, 365, 1),
            "video_extensions": ",".join(sorted(scanner.parse_extensions(f.get("video_extensions", "")))),
            "schedule_mode": f.get("schedule_mode", "daily"),
            "schedule_time": f.get("schedule_time", "03:00") or "03:00",
            "schedule_interval_hours": as_int("schedule_interval_hours", 1, 24 * 30, 24),
            "cache_hours": as_int("cache_hours", 0, 24 * 30, 12),
        }
        if values["season_type"] not in dict(SEASON_TYPES):
            values["season_type"] = "default"
        if values["schedule_mode"] not in ("daily", "interval", "off"):
            values["schedule_mode"] = "daily"
        if not values["video_extensions"]:
            values["video_extensions"] = db().get_settings()["video_extensions"]
        db().set_settings(values)
        current_app.config["SCHEDULER"].reschedule()
        flash("Settings saved. They apply from the next scan.", "ok")
        return redirect(url_for("web.settings"))

    paths = db().scan_paths()
    for p in paths:
        p["available"] = os.path.isdir(p["path"])
    return render_template("settings.html", active="settings", s=db().get_settings(),
                           paths=paths, runs=db().recent_runs(), season_types=SEASON_TYPES)


# -- API ----------------------------------------------------------------------

@bp.route("/api/episodes/<int:episode_id>/flag", methods=["POST"])
def flag_episode(episode_id):
    flagged = bool((request.get_json(silent=True) or {}).get("flagged"))
    with db().tx() as conn:
        if flagged:
            conn.execute("INSERT OR IGNORE INTO retrieval_flags(episode_id, flagged_at) VALUES(?, ?)",
                         (episode_id, now_iso()))
        else:
            conn.execute("DELETE FROM retrieval_flags WHERE episode_id = ?", (episode_id,))
    return jsonify(ok=True, flagged=flagged)


@bp.route("/api/scan/run", methods=["POST"])
def scan_run():
    if not scanner.start_scan_async(db(), "manual"):
        return jsonify(ok=False, error="A scan is already running."), 409
    return jsonify(ok=True)


@bp.route("/api/scan/status")
def scan_status():
    nxt = current_app.config["SCHEDULER"].next_run
    return jsonify(
        running=scanner.progress["running"],
        current=scanner.progress["current"],
        total=scanner.progress["total"],
        show=scanner.progress["show"],
        last_run=db().last_run(),
        next_run=nxt.isoformat(timespec="minutes") if nxt else None,
    )


@bp.route("/api/paths", methods=["POST"])
def add_path():
    raw = ((request.get_json(silent=True) or {}).get("path") or "").strip()
    if not raw:
        return jsonify(ok=False, error="Enter a folder path."), 400
    path = os.path.normpath(os.path.expanduser(raw))
    if not os.path.isdir(path):
        return jsonify(ok=False, error=f"Folder not found or not readable: {path}"), 400
    try:
        shows = scanner.find_show_folders(path)
    except OSError as e:
        return jsonify(ok=False, error=f"Cannot read {path}: {e.strerror or e}"), 400
    if db().query_one("SELECT id FROM scan_paths WHERE path = ?", (path,)):
        return jsonify(ok=False, error="That location is already configured."), 400
    with db().tx() as conn:
        conn.execute("INSERT INTO scan_paths(path, enabled, added_at) VALUES(?, 1, ?)", (path, now_iso()))
    return jsonify(ok=True, path=path, shows_found=len(shows))


@bp.route("/api/paths/<int:path_id>", methods=["DELETE"])
def delete_path(path_id):
    with db().tx() as conn:
        conn.execute("DELETE FROM scan_paths WHERE id = ?", (path_id,))
    return jsonify(ok=True)


@bp.route("/api/paths/<int:path_id>", methods=["PATCH"])
def toggle_path(path_id):
    enabled = 1 if (request.get_json(silent=True) or {}).get("enabled") else 0
    with db().tx() as conn:
        conn.execute("UPDATE scan_paths SET enabled = ? WHERE id = ?", (enabled, path_id))
    return jsonify(ok=True, enabled=bool(enabled))


@bp.route("/api/browse")
def browse():
    """List sub-folders of a path, for the folder picker on the settings page."""
    path = request.args.get("path") or "/"
    if os.name == "nt" and path == "/":
        drives = [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{c}:\\")]
        return jsonify(ok=True, path="", parent=None, dirs=[{"name": d, "path": d} for d in drives])
    path = os.path.normpath(path)
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError as e:
        return jsonify(ok=False, error=f"Cannot open {path}: {e.strerror or e}"), 400
    dirs = []
    for entry in entries:
        try:
            if entry.is_dir() and not entry.name.startswith(".") \
                    and entry.name.lower() not in scanner.SKIP_DIRS:
                dirs.append({"name": entry.name, "path": entry.path,
                             "show": bool(scanner.SHOW_RE.search(entry.name))})
        except OSError:
            continue
    parent = os.path.dirname(path)
    if parent == path:
        parent = "/" if os.name == "nt" else None
    return jsonify(ok=True, path=path, parent=parent, dirs=dirs)


@bp.route("/api/tvdb/test", methods=["POST"])
def tvdb_test():
    body = request.get_json(silent=True) or {}
    saved = db().get_settings()
    key = (body.get("api_key") or saved["tvdb_api_key"]).strip()
    pin = (body.get("pin") if body.get("api_key") else saved["tvdb_pin"]) or ""
    if not key:
        return jsonify(ok=False, error="Enter an API key first."), 400
    try:
        TVDBClient(key, pin).login()
    except TVDBError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True)


@bp.route("/api/shows/<int:tvdb_id>", methods=["PATCH"])
def update_show(tvdb_id):
    body = request.get_json(silent=True) or {}
    fields, params = [], []
    if "min_season" in body:
        value = body["min_season"]
        try:
            value = max(1, int(value)) if value not in (None, "") else None
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Start season must be a number."), 400
        fields.append("min_season = ?")
        params.append(value)
    if "ignored" in body:
        fields.append("ignored = ?")
        params.append(1 if body["ignored"] else 0)
    if fields:
        with db().tx() as conn:
            conn.execute(f"UPDATE shows SET {', '.join(fields)} WHERE tvdb_id = ?", params + [tvdb_id])
    return jsonify(ok=True, note="Applies from the next scan.")
