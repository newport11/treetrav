from flask import Blueprint

bp = Blueprint("api_v2", __name__)

from app.api.v2 import (
    actions,
    agents,
    domains,
    feeds,
    metadata,
    scoring,
    search,
    social,
    subscriptions,
    topics,
    transparency,
    trending,
    urls,
)
