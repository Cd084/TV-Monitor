"""Minimal client for TheTVDB API v4 (https://thetvdb.github.io/v4-api/)."""
import time

import requests

BASE_URL = "https://api4.thetvdb.com/v4"


class TVDBError(Exception):
    pass


class TVDBAuthError(TVDBError):
    pass


class TVDBNotFound(TVDBError):
    pass


def _message(response):
    try:
        return response.json().get("message") or response.text[:200]
    except ValueError:
        return response.text[:200]


def normalize_series(data, series_id):
    status = data.get("status")
    if isinstance(status, dict):
        status = status.get("name")
    return {
        "id": data.get("id") or series_id,
        "name": data.get("name") or "",
        "slug": data.get("slug") or "",
        "status": status or "",
        "year": data.get("year") or "",
    }


def normalize_episode(data):
    return {
        "id": data.get("id"),
        "season": data.get("seasonNumber"),
        "number": data.get("number"),
        "name": data.get("name") or "",
        "aired": data.get("aired") or "",
        "overview": (data.get("overview") or "")[:1000],
    }


class TVDBClient:
    def __init__(self, api_key, pin="", timeout=30, base_url=BASE_URL):
        self.api_key = (api_key or "").strip()
        self.pin = (pin or "").strip()
        self.timeout = timeout
        self.base_url = base_url
        self.token = None
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "tvmonitor/1.0"})

    def login(self):
        payload = {"apikey": self.api_key}
        if self.pin:
            payload["pin"] = self.pin
        try:
            r = self.session.post(f"{self.base_url}/login", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise TVDBError(f"Could not reach TheTVDB: {e}") from e
        if r.status_code != 200:
            raise TVDBAuthError(f"TheTVDB login failed (HTTP {r.status_code}): {_message(r)}")
        self.token = r.json()["data"]["token"]
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _get(self, path, params=None):
        if not self.token:
            self.login()
        last_error = None
        for attempt in range(4):
            try:
                r = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last_error = TVDBError(f"Request to TheTVDB failed: {e}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 401 and attempt == 0:
                self.login()
                continue
            if r.status_code == 404:
                raise TVDBNotFound(f"Not found on TheTVDB ({path})")
            if r.status_code == 429 or r.status_code >= 500:
                last_error = TVDBError(f"TheTVDB returned HTTP {r.status_code}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                raise TVDBError(f"TheTVDB returned HTTP {r.status_code}: {_message(r)}")
            return r.json()
        raise last_error

    def _paged_episodes(self, path, series_id):
        series, episodes, page = None, [], 0
        while True:
            body = self._get(path, {"page": page})
            data = body.get("data") or {}
            series = series or data.get("series")
            episodes.extend(data.get("episodes") or [])
            if not (body.get("links") or {}).get("next") or page >= 200:
                break
            page += 1
        return normalize_series(series or {}, series_id), [normalize_episode(e) for e in episodes]

    def get_series_episodes(self, series_id, season_type="default", language=""):
        """Return (series, episodes) for a series, following pagination."""
        path = f"/series/{int(series_id)}/episodes/{season_type}"
        if language:
            try:
                return self._paged_episodes(f"{path}/{language}", series_id)
            except TVDBNotFound:
                pass  # no translation in that language; fall back to the original
        return self._paged_episodes(path, series_id)
