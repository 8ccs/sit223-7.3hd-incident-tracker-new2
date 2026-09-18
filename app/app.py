"""Incident Tracker Flask application factory.

A small but functionally deep app used as the subject of the SIT223/SIT753
7.3HD Jenkins DevOps pipeline: CRUD incidents, filtering, input validation,
persistent SQLite storage, a health endpoint, and Prometheus metrics.
Routes live in app/routes.py; this module only wires the app together.
"""
from __future__ import annotations

import os

from flask import Flask

from app.metrics import APP_INFO
from app.models import IncidentStore
from app.routes import bp


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    app_version = os.environ.get("APP_VERSION", "0.0.0-dev")
    git_commit = os.environ.get("GIT_COMMIT", "unknown")
    environment = os.environ.get("APP_ENV", "development")

    db_path = db_path or os.environ.get("DB_PATH", "data/incidents.db")
    store = IncidentStore(db_path)

    app.config["STORE"] = store
    app.config["APP_VERSION"] = app_version
    app.config["GIT_COMMIT"] = git_commit
    app.config["APP_ENVIRONMENT"] = environment

    APP_INFO.labels(app_version, git_commit, environment).set(1)

    app.register_blueprint(bp)
    return app


if __name__ == "__main__":  # pragma: no cover
    # Defaults to localhost-only: this app is only ever run on the same
    # machine as Jenkins/Prometheus in this project's local demo setup,
    # so there is no need to expose it on every network interface.
    # Waitress in scripts/deploy.ps1 uses the same default for the same
    # reason.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    application = create_app()
    application.run(host=host, port=port)
