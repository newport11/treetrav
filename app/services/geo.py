"""IP geolocation service. Uses ip-api.com (free, no key needed)."""
import threading

import requests

from app import db


def get_ip_from_request(request):
    """Extract real IP from request, handling proxies."""
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    if request.headers.get("X-Real-IP"):
        return request.headers.get("X-Real-IP")
    return request.remote_addr


def geolocate_ip(ip):
    """Resolve IP to location using ip-api.com. Returns dict or None."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return None
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,city,lat,lon",
            timeout=3,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                return {
                    "country": data.get("country"),
                    "city": data.get("city"),
                    "latitude": data.get("lat"),
                    "longitude": data.get("lon"),
                }
    except Exception:
        pass
    return None


def update_user_geo(user, request):
    """Update user's geo data from their request IP. Runs in background thread."""
    from flask import current_app
    app = current_app._get_current_object()
    ip = get_ip_from_request(request)

    if not ip or ip == user.last_ip:
        return  # Same IP, skip

    def run():
        with app.app_context():
            try:
                from app.models import User
                u = User.query.get(user.id)
                if not u:
                    return
                geo = geolocate_ip(ip)
                u.last_ip = ip
                if geo:
                    u.country = geo["country"]
                    u.city = geo["city"]
                    u.latitude = geo["latitude"]
                    u.longitude = geo["longitude"]
                db.session.commit()
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()
