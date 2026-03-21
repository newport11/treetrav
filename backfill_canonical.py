"""Backfill canonical URLs for existing posts that don't have them.
Run once after upgrading to auto-canonicalization."""
import urllib.parse
from datetime import datetime

from app import create_app, db
from app.models import CanonicalUrl, Post
from app.services.canonicalization import canonicalize_url

app = create_app()

with app.app_context():
    posts = Post.query.filter_by(canonical_url_id=None).all()
    print(f"Posts without canonical URL: {len(posts)}")

    count = 0
    for post in posts:
        try:
            raw_url = urllib.parse.unquote(post.link) if post.link else None
            if not raw_url:
                continue

            canonical_form, url_hash, domain = canonicalize_url(raw_url)
            cu = CanonicalUrl.query.filter_by(url_hash=url_hash).first()
            if cu:
                cu.submission_count = (cu.submission_count or 0) + 1
                cu.last_seen = datetime.utcnow()
            else:
                cu = CanonicalUrl(
                    canonical_url=canonical_form,
                    url_hash=url_hash,
                    domain=domain,
                    submission_count=1,
                    first_seen=post.timestamp or datetime.utcnow(),
                )
                db.session.add(cu)
                db.session.flush()

            post.canonical_url_id = cu.id
            post.content_hash = url_hash
            count += 1

            if count % 100 == 0:
                db.session.commit()
                print(f"  {count}/{len(posts)} processed...")
        except Exception as e:
            print(f"  Error on post {post.id}: {e}")

    db.session.commit()
    print(f"Done. Backfilled {count} posts.")
    print(f"Canonical URLs in DB: {CanonicalUrl.query.count()}")
