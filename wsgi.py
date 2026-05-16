"""WSGI entry for Render / gunicorn — preloads events so first request is fast."""

from triangulation.server import _load_events, app

_load_events()
