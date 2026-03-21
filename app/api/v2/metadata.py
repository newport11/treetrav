from flask import jsonify, request

from app import db
from app.api.errors import bad_request
from app.api.v2 import bp
from app.api.v2.auth import token_or_key_auth
from app.models import CanonicalUrl, UrlMetadata


@bp.route("/urls/<int:canonical_id>/metadata", methods=["GET"])
def get_url_metadata(canonical_id):
    """Get all metadata entries for a canonical URL."""
    canonical = CanonicalUrl.query.get_or_404(canonical_id)
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 25, type=int), 100)

    query = UrlMetadata.query.filter_by(canonical_url_id=canonical_id)

    submitted_by = request.args.get("submitted_by", type=int)
    if submitted_by:
        query = query.filter_by(submitted_by=submitted_by)

    entries = (
        query.order_by(UrlMetadata.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "canonical_url_id": canonical_id,
        "total": entries.total,
        "page": page,
        "metadata": [m.to_dict() for m in entries.items],
    })


@bp.route("/urls/<int:canonical_id>/metadata", methods=["POST"])
@token_or_key_auth.login_required
def submit_url_metadata(canonical_id):
    """Submit metadata/context for a canonical URL."""
    user = token_or_key_auth.current_user()
    canonical = CanonicalUrl.query.get_or_404(canonical_id)
    data = request.get_json() or {}

    if not any(k in data for k in ("summary", "entities", "sentiment", "relevance_justification")):
        return bad_request("must include at least one of: summary, entities, sentiment, relevance_justification")

    # Check if user already submitted metadata for this URL
    existing = UrlMetadata.query.filter_by(
        canonical_url_id=canonical_id, submitted_by=user.id
    ).first()

    if existing:
        # Update existing
        for field in ("summary", "entities", "sentiment", "relevance_justification", "source_credibility", "language"):
            if field in data:
                setattr(existing, field, data[field])
        db.session.commit()
        return jsonify(existing.to_dict())

    meta = UrlMetadata(
        canonical_url_id=canonical_id,
        submitted_by=user.id,
        summary=data.get("summary"),
        entities=data.get("entities"),
        sentiment=data.get("sentiment"),
        relevance_justification=data.get("relevance_justification"),
        source_credibility=data.get("source_credibility"),
        language=data.get("language"),
    )
    db.session.add(meta)
    db.session.commit()
    return jsonify(meta.to_dict()), 201
