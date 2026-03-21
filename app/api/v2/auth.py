from flask import request
from flask_httpauth import HTTPTokenAuth

from app.api.errors import error_response
from app.models import AgentProfile, User

api_key_auth = HTTPTokenAuth()
token_or_key_auth = HTTPTokenAuth()


def _resolve_user():
    """Try to authenticate via X-API-Key header, then Authorization: Bearer token."""
    # 1. Check X-API-Key header
    api_key = request.headers.get("X-API-Key")
    if api_key:
        profile = AgentProfile.query.filter_by(api_key=api_key, is_active=True).first()
        if profile:
            return profile.user

    # 2. Check Authorization: Bearer header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        # Try as API key
        profile = AgentProfile.query.filter_by(api_key=token, is_active=True).first()
        if profile:
            return profile.user
        # Try as session token
        user = User.check_token(token)
        if user:
            return user

    return None


@api_key_auth.verify_token
def verify_api_key(token):
    return _resolve_user()


@api_key_auth.error_handler
def api_key_error(status):
    return error_response(status)


@token_or_key_auth.verify_token
def verify_token_or_key(token):
    return _resolve_user()


@token_or_key_auth.error_handler
def token_or_key_error(status):
    return error_response(status)
