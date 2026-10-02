#!/usr/bin/env python3
"""Start TV Monitor.

    python3 run.py [--host 0.0.0.0] [--port 9494] [--data-dir /var/lib/tvmonitor]

Each option can also come from the environment: TVMONITOR_HOST, TVMONITOR_PORT,
TVMONITOR_DATA. Set TVMONITOR_USER and TVMONITOR_PASSWORD to require a login.
"""
import argparse
import logging
import os

from tvmonitor import create_app


def main():
    parser = argparse.ArgumentParser(description="TV Monitor - missing episode tracker")
    parser.add_argument("--host", default=os.environ.get("TVMONITOR_HOST", "127.0.0.1"),
                        help="address to listen on (0.0.0.0 = all interfaces)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TVMONITOR_PORT", "9494")))
    parser.add_argument("--data-dir", default=os.environ.get("TVMONITOR_DATA"),
                        help="where the database is kept (default: ./data)")
    args = parser.parse_args()

    app = create_app(args.data_dir)
    log = logging.getLogger("tvmonitor")
    if args.host not in ("127.0.0.1", "localhost") and not app.config["AUTH_USER"]:
        log.warning("Listening on %s with no login. Set TVMONITOR_USER/TVMONITOR_PASSWORD "
                    "if this network is not trusted.", args.host)
    log.info("TV Monitor on http://%s:%d", args.host, args.port)
    try:
        from waitress import serve
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    else:
        serve(app, host=args.host, port=args.port, threads=8, ident="tvmonitor")


if __name__ == "__main__":
    main()
