import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tvmonitor import create_app, scanner  # noqa: E402
from tvmonitor.tvdb import TVDBAuthError  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(scanner.parse_episodes("Show abc S03E05.mkv"), [(3, 5)])
        self.assertEqual(scanner.parse_episodes("show.s1e2.720p.mkv"), [(1, 2)])

    def test_multi_episode(self):
        self.assertEqual(scanner.parse_episodes("Show S01E01E02.mkv"), [(1, 1), (1, 2)])
        self.assertEqual(scanner.parse_episodes("Show S01E01-E02.mkv"), [(1, 1), (1, 2)])
        self.assertEqual(scanner.parse_episodes("Show - S01E01 - S01E02 - Title.mkv"), [(1, 1), (1, 2)])
        self.assertEqual(scanner.parse_episodes("Show S01E01-03.mkv"), [(1, 1), (1, 2), (1, 3)])

    def test_no_false_extras(self):
        self.assertEqual(scanner.parse_episodes("Show S01E01-1080p.mkv"), [(1, 1)])
        self.assertEqual(scanner.parse_episodes("Show S01E01 - Episode 5.mkv"), [(1, 1)])
        self.assertEqual(scanner.parse_episodes("Show S02E10 2019 x264.mkv"), [(2, 10)])

    def test_x_format_and_dates(self):
        self.assertEqual(scanner.parse_episodes("Show 2x05 Title.mkv"), [(2, 5)])
        self.assertEqual(scanner.parse_episodes("Show 1920x1080.mkv"), [])
        self.assertEqual(scanner.parse_air_date("Daily Show 2024.05.01.mkv"), "2024-05-01")


class MissingTests(unittest.TestCase):
    today = date(2026, 10, 1)
    eps = [
        {"id": 1, "season": 0, "number": 1, "aired": "2020-01-01"},
        {"id": 2, "season": 1, "number": 1, "aired": "2020-01-01"},
        {"id": 3, "season": 3, "number": 1, "aired": "2022-01-01"},
        {"id": 4, "season": 3, "number": 2, "aired": "2022-01-08"},
        {"id": 5, "season": 3, "number": 3, "aired": "2026-10-01"},  # within grace
        {"id": 6, "season": 3, "number": 4, "aired": "2026-12-01"},  # future
        {"id": 7, "season": 3, "number": 5, "aired": ""},            # no date
    ]

    def ids(self, **kw):
        args = dict(have={(3, 1)}, have_dates=set(), include_specials=False, min_season=1,
                    grace_days=1, today=self.today)
        args.update(kw)
        return [e["id"] for e in scanner.compute_missing(self.eps, **args)]

    def test_defaults(self):
        self.assertEqual(self.ids(), [2, 4])

    def test_min_season_and_specials(self):
        self.assertEqual(self.ids(min_season=3), [4])
        self.assertEqual(self.ids(min_season=3, include_specials=True), [1, 4])

    def test_grace(self):
        self.assertEqual(self.ids(min_season=3, grace_days=0), [4, 5])


class FakeClient:
    calls = 0

    def __init__(self, key, pin=""):
        self.key = key

    def login(self):
        if self.key == "bad":
            raise TVDBAuthError("TheTVDB login failed (HTTP 401): bad key")

    def get_series_episodes(self, series_id, season_type="default", language=""):
        FakeClient.calls += 1
        old = (date.today() - timedelta(days=30)).isoformat()
        eps = [{"id": series_id * 1000 + s * 10 + n, "season": s, "number": n, "name": f"Ep {s}x{n}",
                "aired": old, "overview": ""} for s in (0, 1, 2) for n in (1, 2, 3)]
        return {"name": f"Show {series_id}", "slug": f"show-{series_id}", "status": "Ended"}, eps


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        # Pool of two drives; show 111 is split across both.
        d1 = os.path.join(root, "disk1", "TVShows")
        d2 = os.path.join(root, "disk2")  # drive root - shows are one level down
        files = [
            (d1, "Alpha {tvdb-111}", "Season 1", "Alpha S01E01.mkv"),
            (d1, "Alpha {tvdb-111}", "Season 1", "Alpha S01E02.mkv"),
            (os.path.join(d2, "TVShows"), "Alpha {tvdb-111}", "Season 2", "Alpha S02E01E02.mkv"),
            (d1, "Beta {tvdb-222}", "Season 2", "Beta S02E03.mp4"),
            (d1, "Beta {tvdb-222}", "Season 2", "Beta S02E01.nfo"),  # not a video
        ]
        for base, show, season, fn in files:
            folder = os.path.join(base, show, season)
            os.makedirs(folder, exist_ok=True)
            open(os.path.join(folder, fn), "w").close()
        self.app = create_app(os.path.join(root, "data"), start_scheduler=False)
        self.db = self.app.config["DB"]
        self.db.set_settings({"tvdb_api_key": "good"})
        c = self.app.test_client()
        for p in (d1, d2, os.path.join(root, "offline")):
            if p.endswith("offline"):
                os.makedirs(p)
            self.assertEqual(c.post("/api/paths", json={"path": p}).status_code, 200)
        os.rmdir(os.path.join(root, "offline"))  # simulate a drive that went away
        self.client = c

    def tearDown(self):
        self.tmp.cleanup()

    def missing(self):
        return {(r["tvdb_id"], r["season"], r["episode"])
                for r in self.db.query("SELECT * FROM missing_episodes")}

    def test_full_scan(self):
        self.assertEqual(scanner.run_scan(self.db, "test", FakeClient), "warning")  # offline drive
        self.assertEqual(self.missing(), {(111, 1, 3), (111, 2, 3), (222, 1, 1), (222, 1, 2),
                                          (222, 1, 3), (222, 2, 1), (222, 2, 2)})
        run = self.db.last_run()
        self.assertIn("unavailable", run["message"])

        # Flag one, change settings, rescan: flags survive, rules apply, cache is used.
        ep = 222 * 1000 + 1 * 10 + 1
        self.client.post("/api/episodes/222/flag")  # bogus id is harmless
        self.client.post(f"/api/episodes/{ep}/flag", json={"flagged": True})
        self.client.patch("/api/shows/111", json={"min_season": 2})
        self.db.set_settings({"include_specials": "1"})
        calls = FakeClient.calls
        scanner.run_scan(self.db, "test", FakeClient)
        self.assertEqual(FakeClient.calls, calls)  # served from cache
        got = self.missing()
        self.assertNotIn((111, 1, 3), got)
        self.assertIn((111, 2, 3), got)
        self.assertIn((111, 0, 1), got)

        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Show 222", page)
        self.assertIn(f'data-id="{ep}" class="queued"', page)
        for url in ("/shows", "/settings", "/api/scan/status", "/api/browse?path=" + self.tmp.name):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_bad_key_aborts(self):
        self.db.set_settings({"tvdb_api_key": "bad"})
        self.assertEqual(scanner.run_scan(self.db, "test", FakeClient), "failed")
        self.assertIn("login failed", self.db.last_run()["message"])

    def test_auth(self):
        self.app.config.update(AUTH_USER="u", AUTH_PASSWORD="p")
        self.assertEqual(self.client.get("/").status_code, 401)
        ok = self.client.get("/", headers={"Authorization": "Basic dTpw"})
        self.assertEqual(ok.status_code, 200)


if __name__ == "__main__":
    unittest.main()
