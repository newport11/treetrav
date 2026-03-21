"""Gunicorn configuration for production deployment.

Usage:
    gunicorn -c gunicorn.conf.py "app:create_app()"
"""
import multiprocessing
import os

# Server
bind = os.environ.get("BIND", "0.0.0.0:5000")
workers = int(os.environ.get("WEB_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "gthread"
threads = int(os.environ.get("WEB_THREADS", "4"))
timeout = 120
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# Security
limit_request_line = 8190
limit_request_fields = 100
