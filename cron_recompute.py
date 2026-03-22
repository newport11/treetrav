"""Daily cron job to recompute all scores, credibility, and embeddings.

Add to crontab:
    0 3 * * * cd /home/treetrav/treetrav && /home/treetrav/treetrav/venv/bin/python cron_recompute.py >> /var/log/treetrav/cron.log 2>&1
"""
from datetime import datetime

from app import create_app, db
from app.models import CanonicalUrl, Topic, UrlTopicScore
from app.services.scoring import recompute_domain_credibility, recompute_all_scores_for_topic

app = create_app()

with app.app_context():
    start = datetime.utcnow()
    print(f"[{start.isoformat()}] Starting daily recompute...")

    # 1. Recompute all topic scores
    topics = Topic.query.filter_by(is_active=True).all()
    print(f"  Recomputing scores for {len(topics)} topics...")
    for t in topics:
        recompute_all_scores_for_topic(t.id)

    # 2. Recompute domain credibility
    domains = set(r[0] for r in db.session.query(CanonicalUrl.domain).distinct().all() if r[0])
    print(f"  Recomputing credibility for {len(domains)} domains...")
    for domain in domains:
        recompute_domain_credibility(domain)
        # Per-topic credibility for topics this domain appears in
        topic_ids = set(
            r[0] for r in db.session.query(UrlTopicScore.topic_id)
            .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
            .filter(CanonicalUrl.domain == domain)
            .distinct().all()
        )
        for tid in topic_ids:
            recompute_domain_credibility(domain, tid)

    end = datetime.utcnow()
    print(f"[{end.isoformat()}] Done in {(end - start).total_seconds():.1f}s")
