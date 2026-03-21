"""WSGI entry point for production deployment.

Usage:
    gunicorn -c gunicorn.conf.py wsgi:application
"""
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from app import create_app
from shardops import create_app as create_shardops

treetrav_app = create_app()
shardops_app = create_shardops()

application = DispatcherMiddleware(treetrav_app, {
    "/shardops": shardops_app,
})
