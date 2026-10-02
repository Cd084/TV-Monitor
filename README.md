# TV Monitor

Scans a pool of drives for TV shows, compares what's on disk with TheTVDB, and lists aired episodes you don't have, newest first.

## Requirements

- Linux with systemd (Rocky/RHEL/Alma 8+, Fedora, Debian 11+, Ubuntu 20.04+, …) or Docker
- Python 3.9+ (the stock `python3` on Rocky 9 is fine)
- A TheTVDB v4 API key: <https://thetvdb.com/api-information>

## Folder layout it expects

```
/mnt/disk1/TVShows/
  Show Name {tvdb-123456}/
    Season 1/
      Show Name S01E01.mkv
```

The show is matched by the `{tvdb-…}` id in the folder name. Episodes come from the `SxxEyy` in each file name. It also understands `S01E01E02`, `S01E01-E02`, `S01E01-03`, `1x01`, and air-date names like `2024.05.01`. If the same show is on several drives, the folders are merged.

## Install (systemd)

Copy this folder to the server, then:

```bash
cd tvmonitor
sudo bash deploy/install.sh --user plex --open-firewall
```

- `--user plex` runs the service as the Plex user, which can already read your media. Leave it out and a dedicated `tvmonitor` user is created; that user then needs read access to the TV folders.
- `--open-firewall` opens the port in firewalld. Leave it out and the script just prints the command.
- `--port 9494` sets the port (9494 is the default). Re-run with a different `--port` to change it later.

Then open `http://<server>:9494`, go to **Settings**, add your scan locations, enter the API key, and click **Run scan now**.

| What | Where |
|---|---|
| Code + virtualenv | `/opt/tvmonitor` |
| Database | `/var/lib/tvmonitor/tvmonitor.db` |
| Host/port/login | `/etc/tvmonitor/tvmonitor.env` |
| Logs | `journalctl -u tvmonitor -f` |

To upgrade, copy the new files over and run `sudo bash deploy/install.sh` again. Your data and config are kept. To remove it, run `sudo bash deploy/uninstall.sh` (add `--purge` to delete the data too).

**Login:** by default the web UI has no password. To require one, uncomment `TVMONITOR_USER` / `TVMONITOR_PASSWORD` in `/etc/tvmonitor/tvmonitor.env`, then run `systemctl restart tvmonitor`.

## Docker (alternative)

Edit the volume lines in `docker-compose.yml` and run `docker compose up -d`. Then add the container paths (`/media/disk1`, …) as scan locations. On SELinux hosts such as Rocky, see the note in the compose file.

## Settings

- **Scan locations**: add, remove, or disable them, with a folder browser. If a location goes offline, the scan reports a warning. Its shows drop off the list until the location is back, and your ticks are kept.
- **Specials**: Season 0 is skipped unless you turn it on.
- **Start at season N**: a global default, overridable per show on the **Shows** page. Each show can also be ignored there.
- **Grace period**: how many days after the air date before an episode counts as missing. Episodes not yet aired, or with no air date, are never counted.
- **Episode order**: aired order by default, or DVD/absolute/etc.
- **Schedule**: once a day at a set time (default 03:00), every N hours, or off. **Run scan now** works on every page.
- **Cache**: TheTVDB data is reused for 12 hours by default, so a re-scan after adding files is quick.

## Main screen

Missing episodes are sorted newest air date first. Tick **Set up** when an episode is set up for retrieval. The tick is saved, and the episode drops off the list once the file appears on disk. You can filter the list by text and hide ticked episodes.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python run.py            # http://127.0.0.1:9494, data in ./data
python -m unittest discover -s tests
```
