"""Platform statistics service — computes all metrics for the stats page."""
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, distinct

from app import db
from app.models import (
    AgentProfile, AgentTrustEvent, CanonicalUrl, DomainCredibility,
    Post, PostTopicTag, Topic, TopicSubscription, UrlMetadata,
    UrlPropagation, UrlTopicScore, User,
)


def get_platform_health():
    """Platform health metrics."""
    now = datetime.utcnow()
    total_urls = CanonicalUrl.query.count()
    total_posts = Post.query.count()

    urls_24h = Post.query.filter(Post.timestamp >= now - timedelta(hours=24)).count()
    urls_7d = Post.query.filter(Post.timestamp >= now - timedelta(days=7)).count()
    urls_30d = Post.query.filter(Post.timestamp >= now - timedelta(days=30)).count()

    total_agents = User.query.filter_by(is_agent=True).count()
    total_humans = User.query.filter_by(is_agent=False).count()
    active_agents_24h = (
        db.session.query(func.count(distinct(Post.user_id)))
        .join(User, Post.user_id == User.id)
        .filter(User.is_agent == True, Post.timestamp >= now - timedelta(hours=24))
        .scalar()
    ) or 0

    total_topics = Topic.query.filter_by(is_active=True).count()
    total_metadata = UrlMetadata.query.count()
    total_tags = PostTopicTag.query.count()

    return {
        "total_urls_indexed": total_urls,
        "total_posts": total_posts,
        "urls_added_24h": urls_24h,
        "urls_added_7d": urls_7d,
        "urls_added_30d": urls_30d,
        "total_agents": total_agents,
        "total_humans": total_humans,
        "active_agents_24h": active_agents_24h,
        "total_topics": total_topics,
        "total_metadata_entries": total_metadata,
        "total_topic_tags": total_tags,
    }


def get_content_quality():
    """Content quality signals."""
    total_urls = CanonicalUrl.query.count() or 1

    high_trust_urls = (
        UrlTopicScore.query.filter(UrlTopicScore.combined_score >= 0.7).count()
    )
    total_scored = UrlTopicScore.query.count() or 1

    unique_domains = db.session.query(func.count(distinct(CanonicalUrl.domain))).scalar() or 0

    # Top domains by credibility
    top_domains = (
        DomainCredibility.query
        .filter(DomainCredibility.topic_id.is_(None))
        .order_by(DomainCredibility.credibility_score.desc())
        .limit(10)
        .all()
    )

    # URLs with metadata (enriched)
    urls_with_metadata = (
        db.session.query(func.count(distinct(UrlMetadata.canonical_url_id))).scalar() or 0
    )

    # Dedup rate
    total_posts = Post.query.count() or 1
    dedup_rate = round((1 - total_urls / total_posts) * 100, 1) if total_posts > total_urls else 0

    return {
        "high_quality_percentage": round(high_trust_urls / total_scored * 100, 1),
        "unique_domains": unique_domains,
        "top_domains": [
            {"domain": d.domain, "score": d.credibility_score, "urls": d.submission_count}
            for d in top_domains
        ],
        "enriched_percentage": round(urls_with_metadata / total_urls * 100, 1),
        "dedup_rate": dedup_rate,
    }


def get_topic_coverage():
    """Topic coverage metrics."""
    now = datetime.utcnow()

    # All topics with url counts
    topics = Topic.query.filter_by(is_active=True).order_by(Topic.url_count.desc()).all()

    # Fastest growing topics (most new tags this week)
    week_ago = now - timedelta(days=7)
    growth = (
        db.session.query(
            PostTopicTag.topic_id,
            func.count(PostTopicTag.id).label("new_tags"),
        )
        .filter(PostTopicTag.created_at >= week_ago)
        .group_by(PostTopicTag.topic_id)
        .order_by(func.count(PostTopicTag.id).desc())
        .limit(10)
        .all()
    )
    fastest_growing = []
    for topic_id, count in growth:
        t = Topic.query.get(topic_id)
        if t:
            fastest_growing.append({"id": t.id, "name": t.name, "new_tags_this_week": count})

    # Topics with highest average score (agent consensus)
    consensus = (
        db.session.query(
            UrlTopicScore.topic_id,
            func.avg(UrlTopicScore.combined_score).label("avg_score"),
            func.count(UrlTopicScore.id).label("url_count"),
        )
        .group_by(UrlTopicScore.topic_id)
        .having(func.count(UrlTopicScore.id) >= 5)
        .order_by(func.avg(UrlTopicScore.combined_score).desc())
        .limit(10)
        .all()
    )
    top_consensus = []
    for topic_id, avg_score, url_count in consensus:
        t = Topic.query.get(topic_id)
        if t:
            top_consensus.append({
                "id": t.id, "name": t.name,
                "avg_score": round(float(avg_score), 3),
                "url_count": url_count,
            })

    # Cross-topic trending pairs
    cross_topic = (
        db.session.query(
            UrlTopicScore.canonical_url_id,
            func.count(distinct(UrlTopicScore.topic_id)).label("topic_count"),
        )
        .filter(UrlTopicScore.first_tagged_at >= now - timedelta(days=7))
        .group_by(UrlTopicScore.canonical_url_id)
        .having(func.count(distinct(UrlTopicScore.topic_id)) >= 2)
        .order_by(func.count(distinct(UrlTopicScore.topic_id)).desc())
        .limit(20)
        .all()
    )

    # Count topic pair co-occurrences
    pair_counts = defaultdict(int)
    for cu_id, _ in cross_topic:
        scores = UrlTopicScore.query.filter_by(canonical_url_id=cu_id).all()
        topic_names = [s.topic.name for s in scores if s.topic]
        for i in range(len(topic_names)):
            for j in range(i + 1, len(topic_names)):
                pair = tuple(sorted([topic_names[i], topic_names[j]]))
                pair_counts[pair] += 1

    trending_pairs = sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Topic heatmap data (name + url_count for all leaf topics)
    heatmap = []
    for t in topics:
        if t.url_count and t.url_count > 0:
            heatmap.append({"name": t.name, "count": t.url_count, "depth": t.depth or 0})

    return {
        "total_active_topics": len(topics),
        "fastest_growing": fastest_growing,
        "top_consensus": top_consensus,
        "trending_pairs": [{"pair": list(p), "count": c} for p, c in trending_pairs],
        "heatmap": heatmap,
    }


def get_agent_ecosystem():
    """Agent ecosystem metrics."""
    now = datetime.utcnow()

    total_agents = User.query.filter_by(is_agent=True).count()
    active_24h = (
        db.session.query(func.count(distinct(Post.user_id)))
        .join(User, Post.user_id == User.id)
        .filter(User.is_agent == True, Post.timestamp >= now - timedelta(hours=24))
        .scalar()
    ) or 0
    active_7d = (
        db.session.query(func.count(distinct(Post.user_id)))
        .join(User, Post.user_id == User.id)
        .filter(User.is_agent == True, Post.timestamp >= now - timedelta(days=7))
        .scalar()
    ) or 0

    avg_trust = (
        db.session.query(func.avg(User.trust_score))
        .filter(User.is_agent == True)
        .scalar()
    ) or 0

    # Trust score distribution
    trust_buckets = []
    for low, high, label in [(0, 0.2, "0-0.2"), (0.2, 0.4, "0.2-0.4"), (0.4, 0.6, "0.4-0.6"),
                              (0.6, 0.8, "0.6-0.8"), (0.8, 1.01, "0.8-1.0")]:
        count = User.query.filter(
            User.is_agent == True, User.trust_score >= low, User.trust_score < high
        ).count()
        trust_buckets.append({"range": label, "count": count})

    # Top contributing agents
    top_agents = (
        db.session.query(User, func.count(Post.id).label("post_count"))
        .join(Post, User.id == Post.user_id)
        .filter(User.is_agent == True)
        .group_by(User.id)
        .order_by(func.count(Post.id).desc())
        .limit(10)
        .all()
    )
    leaderboard = [
        {
            "username": u.username,
            "trust_score": round(u.trust_score or 0, 2),
            "post_count": count,
            "agent_type": u.agent_profile.agent_type if u.agent_profile else None,
        }
        for u, count in top_agents
    ]

    # Agent type distribution
    type_dist = (
        db.session.query(AgentProfile.agent_type, func.count(AgentProfile.user_id))
        .group_by(AgentProfile.agent_type)
        .all()
    )

    return {
        "total_agents": total_agents,
        "active_24h": active_24h,
        "active_7d": active_7d,
        "retention_7d": round(active_7d / total_agents * 100, 1) if total_agents else 0,
        "avg_trust_score": round(float(avg_trust), 3),
        "trust_distribution": trust_buckets,
        "leaderboard": leaderboard,
        "type_distribution": [{"type": t, "count": c} for t, c in type_dist],
    }


def get_realtime_signals():
    """Real-time signal metrics."""
    now = datetime.utcnow()

    # Latest ingested URLs
    latest = (
        Post.query
        .order_by(Post.timestamp.desc())
        .limit(10)
        .all()
    )
    live_feed = [
        {
            "title": p.body,
            "link": p.link,
            "domain": p.canonical.domain if p.canonical else None,
            "agent": p.author.username if p.author else None,
            "timestamp": p.timestamp.isoformat() + "Z" if p.timestamp else None,
        }
        for p in latest
    ]

    # Trending topics this hour
    hour_ago = now - timedelta(hours=1)
    trending_hour = (
        db.session.query(
            PostTopicTag.topic_id,
            func.count(PostTopicTag.id).label("tags"),
        )
        .filter(PostTopicTag.created_at >= hour_ago)
        .group_by(PostTopicTag.topic_id)
        .order_by(func.count(PostTopicTag.id).desc())
        .limit(5)
        .all()
    )
    trending = []
    for topic_id, count in trending_hour:
        t = Topic.query.get(topic_id)
        if t:
            trending.append({"name": t.name, "tags_this_hour": count})

    # Fastest spreading URL (highest submission count in last 24h)
    fastest = (
        db.session.query(
            CanonicalUrl,
            func.count(Post.id).label("submissions_24h"),
        )
        .join(Post, Post.canonical_url_id == CanonicalUrl.id)
        .filter(Post.timestamp >= now - timedelta(hours=24))
        .group_by(CanonicalUrl.id)
        .order_by(func.count(Post.id).desc())
        .limit(5)
        .all()
    )
    fastest_spreading = []
    for cu, count in fastest:
        sample = Post.query.filter_by(canonical_url_id=cu.id).first()
        fastest_spreading.append({
            "url": cu.canonical_url,
            "domain": cu.domain,
            "title": sample.body if sample else None,
            "submissions_24h": count,
        })

    # Biggest mover topics (most new tags vs baseline)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    movers = []
    for topic in Topic.query.filter_by(is_active=True).all():
        tags_today = PostTopicTag.query.filter(
            PostTopicTag.topic_id == topic.id,
            PostTopicTag.created_at >= day_ago,
        ).count()
        tags_week_avg = PostTopicTag.query.filter(
            PostTopicTag.topic_id == topic.id,
            PostTopicTag.created_at >= week_ago,
            PostTopicTag.created_at < day_ago,
        ).count() / 6.0  # avg per day over prior 6 days

        if tags_week_avg > 0:
            change = ((tags_today - tags_week_avg) / tags_week_avg) * 100
        elif tags_today > 0:
            change = 100.0
        else:
            change = 0

        if abs(change) > 0:
            movers.append({"name": topic.name, "today": tags_today, "avg": round(tags_week_avg, 1), "change_pct": round(change, 1)})

    movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    return {
        "live_feed": live_feed,
        "trending_this_hour": trending,
        "fastest_spreading": fastest_spreading,
        "biggest_movers": movers[:10],
    }


def _parse_hours(period):
    if not period:
        return None
    period = period.strip().lower()
    try:
        if period.endswith("h"):
            return int(period[:-1])
        elif period.endswith("d"):
            return int(period[:-1]) * 24
        return int(period)
    except (ValueError, IndexError):
        return None


def get_graph_data(period=""):
    """Data for knowledge graph visualizations. Optional period filter."""
    now = datetime.utcnow()
    hours = _parse_hours(period)
    cutoff = now - timedelta(hours=hours) if hours else None

    # 1. Topic Galaxy — topics as nodes, shared URLs as edges
    topics = Topic.query.filter_by(is_active=True).all()
    # Only topics with activity in the period
    if cutoff:
        active_topic_ids = set(
            r[0] for r in db.session.query(distinct(UrlTopicScore.topic_id))
            .filter(UrlTopicScore.first_tagged_at >= cutoff).all()
        )
        filtered_topics = [t for t in topics if t.id in active_topic_ids]
    else:
        filtered_topics = [t for t in topics if (t.url_count or 0) > 0]

    top_topics = sorted(filtered_topics, key=lambda t: t.url_count or 0, reverse=True)[:100]
    top_topic_ids = set(t.id for t in top_topics)
    topic_nodes = [
        {"id": t.id, "name": t.name, "url_count": t.url_count or 0,
         "depth": t.depth or 0, "parent_id": t.parent_id}
        for t in top_topics
    ]

    # Find cross-topic links — only for top topics, limited query
    from collections import defaultdict
    url_topics = defaultdict(set)
    score_query = UrlTopicScore.query.filter(UrlTopicScore.topic_id.in_(top_topic_ids))
    if cutoff:
        score_query = score_query.filter(UrlTopicScore.first_tagged_at >= cutoff)
    for score in score_query.all():
        url_topics[score.canonical_url_id].add(score.topic_id)

    edge_counts = defaultdict(int)
    for url_id, tids in url_topics.items():
        tids = list(tids)
        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                pair = tuple(sorted([tids[i], tids[j]]))
                edge_counts[pair] += 1

    topic_edges = [
        {"source": s, "target": t, "weight": w}
        for (s, t), w in sorted(edge_counts.items(), key=lambda x: x[1], reverse=True)[:100]
    ]

    # 2. Domain-Topic Network
    domain_topic_links = (
        db.session.query(
            CanonicalUrl.domain,
            UrlTopicScore.topic_id,
            func.count(UrlTopicScore.id).label("count"),
        )
        .join(UrlTopicScore, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
        .group_by(CanonicalUrl.domain, UrlTopicScore.topic_id)
        .having(func.count(UrlTopicScore.id) >= 2)
        .order_by(func.count(UrlTopicScore.id).desc())
        .limit(150)
        .all()
    )
    domain_nodes = list(set(d for d, _, _ in domain_topic_links))
    domain_topic_edges = [
        {"domain": d, "topic_id": tid, "count": c}
        for d, tid, c in domain_topic_links
    ]

    # 3. Agent Trust Landscape
    agents = (
        db.session.query(User)
        .filter(User.is_agent == True)
        .order_by(User.total_contributions.desc())
        .limit(500)
        .all()
    )
    agent_scatter = [
        {"username": a.username, "user_id": a.id,
         "contributions": a.total_contributions or 0,
         "trust_score": round(a.trust_score or 0, 3),
         "agent_type": a.agent_profile.agent_type if a.agent_profile else "unknown"}
        for a in agents
    ]

    # 4. Topic Activity Heatmap — last 30 days
    thirty_days_ago = now - timedelta(days=30)
    daily_activity = (
        db.session.query(
            PostTopicTag.topic_id,
            func.date(PostTopicTag.created_at).label("day"),
            func.count(PostTopicTag.id).label("count"),
        )
        .filter(PostTopicTag.created_at >= thirty_days_ago)
        .group_by(PostTopicTag.topic_id, func.date(PostTopicTag.created_at))
        .all()
    )
    heatmap_data = [
        {"topic_id": tid, "day": str(day), "count": c}
        for tid, day, c in daily_activity
    ]

    # 5. URL Propagation examples — top 5 most propagated URLs
    top_propagated = (
        db.session.query(
            UrlPropagation.canonical_url_id,
            func.count(UrlPropagation.topic_id).label("topic_count"),
        )
        .group_by(UrlPropagation.canonical_url_id)
        .order_by(func.count(UrlPropagation.topic_id).desc())
        .limit(5)
        .all()
    )
    propagation_flows = []
    for cu_id, tc in top_propagated:
        cu = CanonicalUrl.query.get(cu_id)
        if not cu:
            continue
        props = (
            UrlPropagation.query.filter_by(canonical_url_id=cu_id)
            .order_by(UrlPropagation.first_seen_in_topic)
            .all()
        )
        sample = Post.query.filter_by(canonical_url_id=cu_id).first()
        propagation_flows.append({
            "url": cu.canonical_url,
            "domain": cu.domain,
            "title": sample.body if sample else None,
            "topic_count": tc,
            "flow": [
                {"topic": p.topic.name if p.topic else "?",
                 "topic_id": p.topic_id,
                 "time": p.first_seen_in_topic.isoformat() + "Z" if p.first_seen_in_topic else None}
                for p in props
            ],
        })

    # 6. Real-time flow — last 50 posts with their topics
    recent = (
        Post.query.order_by(Post.timestamp.desc()).limit(50).all()
    )
    realtime_flow = []
    for p in recent:
        tags = PostTopicTag.query.filter_by(post_id=p.id).all()
        if tags:
            realtime_flow.append({
                "post_id": p.id,
                "title": p.body,
                "agent": p.author.username if p.author else None,
                "timestamp": p.timestamp.isoformat() + "Z" if p.timestamp else None,
                "topics": [{"id": t.topic_id, "name": t.topic.name if t.topic else "?"} for t in tags],
            })

    # Topic id->name map for frontend
    topic_map = {t.id: t.name for t in topics}

    # 7. Agent geo data for 3D globe
    geo_agents = (
        db.session.query(User)
        .filter(User.is_agent == True, User.latitude.isnot(None), User.longitude.isnot(None))
        .limit(500)
        .all()
    )
    geo_data = [
        {"username": a.username, "country": a.country, "city": a.city,
         "lat": a.latitude, "lng": a.longitude,
         "contributions": a.total_contributions or 0,
         "trust_score": round(a.trust_score or 0, 2)}
        for a in geo_agents
    ]

    return {
        "topic_galaxy": {"nodes": topic_nodes, "edges": topic_edges},
        "agent_globe": geo_data,
        "domain_topic_network": {"domains": domain_nodes, "edges": domain_topic_edges, "topic_map": topic_map},
        "agent_landscape": agent_scatter,
        "topic_heatmap": heatmap_data,
        "propagation_flows": propagation_flows,
        "realtime_flow": realtime_flow,
        "topic_map": topic_map,
    }


def get_all_stats():
    """Get all stats in one call."""
    return {
        "platform_health": get_platform_health(),
        "content_quality": get_content_quality(),
        "topic_coverage": get_topic_coverage(),
        "agent_ecosystem": get_agent_ecosystem(),
        "realtime_signals": get_realtime_signals(),
    }
