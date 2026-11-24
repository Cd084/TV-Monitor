"""TV Monitor - finds missing TV episodes by comparing disk against TheTVDB."""
import logging
import os

from flask import Flask

from .db import Database
from .scheduler import Scheduler

__version__ = "1.0.0"


def create_app(data_dir=None, start_scheduler=True):
    data_dir = data_dir or os.environ.get("TVMONITOR_DATA") or os.path.join(os.getcwd(), "data")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db = Database(os.path.join(data_dir, "tvmonitor.db"))
    app = Flask(__name__)
    app.secret_key = db.secret_key()
    app.config["DB"] = db
    app.config["AUTH_USER"] = os.environ.get("TVMONITOR_USER", "")
    app.config["AUTH_PASSWORD"] = os.environ.get("TVMONITOR_PASSWORD", "")
    app.jinja_env.globals["app_version"] = __version__

    scheduler = Scheduler(db)
    app.config["SCHEDULER"] = scheduler

    from .web import bp
    app.register_blueprint(bp)

    if start_scheduler:
        scheduler.start()
    return app
