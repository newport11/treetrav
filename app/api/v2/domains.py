from flask import jsonify, request

from app import db
from app.api.v2 import bp
from app.models import CanonicalUrl, DomainCredibility, Topic, UrlTopicScore
from app.services.scoring import recompute_domain_credibility


@bp.route("/domains/<path:domain>/credibility", methods=["GET"])
def get_domain_credibility(domain):
    """Get credibility scores for a domain — global and per-topic."""
    records = DomainCredibility.query.filter_by(domain=domain).all()

    if not records:
        # Compute on the fly if we have data
        url_count = CanonicalUrl.query.filter_by(domain=domain).count()
        if url_count == 0:
            return jsonify({
                "domain": domain,
                "found": False,
                "message": "no URLs from this domain in the system",
            })

        # Compute global credibility
        recompute_domain_credibility(domain)
        records = DomainCredibility.query.filter_by(domain=domain).all()

    global_record = None
    topic_records = []
    for r in records:
        if r.topic_id is None:
            global_record = r
        else:
            topic_records.append(r)

    return jsonify({
        "domain": domain,
        "found": True,
        "global": global_record.to_dict() if global_record else None,
        "per_topic": [r.to_dict() for r in topic_records],
    })


@bp.route("/topics/<int:topic_id>/top-domains", methods=["GET"])
def get_top_domains_for_topic(topic_id):
    """Get the most credible domains for a specific topic."""
    topic = Topic.query.get_or_404(topic_id)
    limit = min(request.args.get("limit", 10, type=int), 100)

    records = (
        DomainCredibility.query
        .filter_by(topic_id=topic_id)
        .order_by(DomainCredibility.credibility_score.desc())
        .limit(limit)
        .all()
    )

    return jsonify({
        "topic_id": topic_id,
        "topic_name": topic.name,
        "domains": [r.to_dict() for r in records],
    })


@bp.route("/domains/<path:domain>/history", methods=["GET"])
def get_domain_history(domain):
    """Get domain credibility data and URL count over time."""
    records = DomainCredibility.query.filter_by(domain=domain).all()
    url_count = CanonicalUrl.query.filter_by(domain=domain).count()

    return jsonify({
        "domain": domain,
        "total_urls": url_count,
        "credibility_records": [r.to_dict() for r in records],
    })
