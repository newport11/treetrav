"""End-to-end test for all v2 API phases."""
import json
import sys

from app import create_app, db
from app.models import (
    AgentProfile, AgentTrustEvent, CanonicalUrl, DomainCredibility,
    Post, PostTopicTag, Topic, TopicAlias, TopicSubscription,
    UrlMetadata, UrlPropagation, UrlTopicScore, User, WebhookDelivery,
)
from app.services.canonicalization import canonicalize_url
from app.services.taxonomy import create_topic, seed_default_topics

app = create_app()
passed = 0
failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} — {detail}")
        failed += 1


def phase_header(num, title):
    print(f"\n{'='*60}")
    print(f"Phase {num}: {title}")
    print(f"{'='*60}")


with app.app_context():
    client = app.test_client()

    # Clean up test data
    for model in [WebhookDelivery, TopicSubscription, AgentTrustEvent, PostTopicTag,
                  UrlMetadata, UrlPropagation, UrlTopicScore, DomainCredibility,
                  TopicAlias, AgentProfile]:
        model.query.delete()
    Post.query.filter(Post.canonical_url_id.isnot(None)).update({"canonical_url_id": None})
    CanonicalUrl.query.delete()
    Topic.query.delete()
    # Clean up test users
    for u in User.query.filter(User.username.in_(["testagent1", "testagent2", "testuser_v2"])).all():
        Post.query.filter_by(user_id=u.id).delete()
        db.session.delete(u)
    db.session.commit()

    # Create a test human user
    test_user = User(username="testuser_v2", email="testv2@test.local")
    test_user.set_password("testpass")
    db.session.add(test_user)
    db.session.commit()

    # Get token for test user
    r = client.post("/api/tokens", headers={
        "Authorization": "Basic " + __import__("base64").b64encode(b"testuser_v2:testpass").decode()
    })
    test(f"get token for test user (status={r.status_code})", r.status_code == 200)
    user_token = r.get_json()["token"]

    # =========================================================================
    phase_header(1, "Agent Identity & API Key Auth")
    # =========================================================================

    # Register agent
    r = client.post("/api/v2/agents/register", json={
        "username": "testagent1",
        "password": "agentpass1",
        "agent_type": "crawler",
        "description": "Test crawler agent",
    })
    test(f"register agent (status={r.status_code})", r.status_code == 201)
    agent1 = r.get_json()
    api_key1 = agent1["api_key"]
    agent1_id = agent1["user_id"]
    test("agent has API key", bool(api_key1))
    test("agent trust starts at 0.3", agent1["trust_score"] == 0.3)

    # Register second agent
    r = client.post("/api/v2/agents/register", json={
        "username": "testagent2",
        "password": "agentpass2",
        "agent_type": "curator",
    })
    test(f"register agent 2 (status={r.status_code})", r.status_code == 201)
    agent2 = r.get_json()
    api_key2 = agent2["api_key"]
    agent2_id = agent2["user_id"]

    # Duplicate username
    r = client.post("/api/v2/agents/register", json={
        "username": "testagent1", "password": "x", "agent_type": "x",
    })
    test(f"duplicate username rejected (status={r.status_code})", r.status_code == 400)

    # Get agent profile
    r = client.get(f"/api/v2/agents/{agent1_id}")
    test(f"get agent profile (status={r.status_code})", r.status_code == 200)
    test("profile has trust_score", "trust_score" in r.get_json())

    # Auth with API key
    r = client.get(f"/api/v2/agents/{agent1_id}/trust")
    test(f"get trust (no auth needed) (status={r.status_code})", r.status_code == 200)

    # Update agent
    r = client.put(f"/api/v2/agents/{agent1_id}", json={"description": "Updated desc"},
                   headers={"X-API-Key": api_key1})
    test(f"update agent (status={r.status_code})", r.status_code == 200)

    # Rotate key
    r = client.post(f"/api/v2/agents/{agent1_id}/rotate-key",
                    headers={"X-API-Key": api_key1})
    test(f"rotate key (status={r.status_code})", r.status_code == 200)
    api_key1 = r.get_json()["api_key"]  # Use new key going forward

    # Get contributions (empty)
    r = client.get(f"/api/v2/agents/{agent1_id}/contributions")
    test(f"get contributions (status={r.status_code})", r.status_code == 200)

    # =========================================================================
    phase_header(2, "Topic Taxonomy Engine")
    # =========================================================================

    # Seed default topics
    r = client.post("/api/v2/topics/seed", headers={"Authorization": f"Bearer {user_token}"})
    test(f"seed topics (status={r.status_code})", r.status_code == 200)
    seeded = r.get_json()
    test(f"seeded {seeded['created']} topics", seeded["created"] > 20)

    # List topics as tree
    r = client.get("/api/v2/topics?format=tree")
    test(f"list topics tree (status={r.status_code})", r.status_code == 200)
    topics_tree = r.get_json()["topics"]
    test("has root topics", len(topics_tree) > 0)

    # List topics flat
    r = client.get("/api/v2/topics?format=flat")
    test(f"list topics flat (status={r.status_code})", r.status_code == 200)

    # Get single topic
    ai_topic = Topic.query.filter_by(name="Artificial Intelligence").first()
    test("AI topic exists in DB", ai_topic is not None)
    r = client.get(f"/api/v2/topics/{ai_topic.id}")
    test(f"get AI topic (status={r.status_code})", r.status_code == 200)
    test("topic has path", r.get_json()["path"] == "technology/artificial-intelligence")

    # Search topics
    r = client.get("/api/v2/topics/search?q=artificial")
    test(f"search topics (status={r.status_code})", r.status_code == 200)
    test("found AI topic", len(r.get_json()["results"]) > 0)

    # Create subtopic (agent needs trust > 0.6, so use human user)
    r = client.post("/api/v2/topics", json={
        "name": "Large Language Models",
        "description": "LLMs, GPT, Claude, etc.",
        "parent_id": ai_topic.id,
    }, headers={"Authorization": f"Bearer {user_token}"})
    test(f"create subtopic (status={r.status_code})", r.status_code == 201)
    llm_topic_id = r.get_json()["id"]

    # Get ancestors
    r = client.get(f"/api/v2/topics/{llm_topic_id}/ancestors")
    test(f"get ancestors (status={r.status_code})", r.status_code == 200)
    ancestors = r.get_json()["ancestors"]
    test("ancestor chain: Technology -> AI -> LLM", len(ancestors) == 3)

    # =========================================================================
    phase_header(3, "URL Deduplication & Canonicalization")
    # =========================================================================

    # Test canonicalization service directly
    c1, h1, d1 = canonicalize_url("https://www.example.com/page?utm_source=twitter&id=42")
    c2, h2, d2 = canonicalize_url("http://example.com/page/?utm_medium=social&id=42")
    test("canonical URLs match (stripped UTM, www, scheme)", c1 == c2)
    test("hashes match", h1 == h2)
    test("domain extracted", d1 == "example.com")

    c3, h3, d3 = canonicalize_url("https://example.com/different")
    test("different URL has different hash", h1 != h3)

    # Lookup URL (not yet in system)
    r = client.get("/api/v2/urls/lookup?url=https://arxiv.org/abs/2301.00001")
    test(f"lookup unknown URL (status={r.status_code})", r.status_code == 200)
    test("not found yet", r.get_json()["found"] == False)

    # Post a link via v1 API (simulate agent posting)
    # First get agent token
    r = client.post("/api/tokens", headers={
        "Authorization": "Basic " + __import__("base64").b64encode(b"testagent1:agentpass1").decode()
    })
    agent1_token = r.get_json()["token"]

    r = client.post("/api/post_link", json={
        "link": "https://arxiv.org/abs/2301.00001",
        "text": "Quantum Error Correction Paper",
        "folder": "research",
    }, headers={"Authorization": f"Bearer {agent1_token}"})
    test(f"agent1 posts link (status={r.status_code})", r.status_code == 201)
    post1_id = r.get_json()["id"]

    # Now manually canonicalize and link (since v1 post_link doesn't auto-canonicalize yet)
    # We'll create the canonical URL and link the post
    curl, chash, cdomain = canonicalize_url("https://arxiv.org/abs/2301.00001")
    cu = CanonicalUrl.query.filter_by(url_hash=chash).first()
    if not cu:
        cu = CanonicalUrl(canonical_url=curl, url_hash=chash, domain=cdomain, submission_count=1)
        db.session.add(cu)
        db.session.flush()
    post1 = Post.query.get(post1_id)
    post1.canonical_url_id = cu.id
    post1.content_hash = chash
    db.session.commit()

    # Second agent posts same URL
    r = client.post("/api/tokens", headers={
        "Authorization": "Basic " + __import__("base64").b64encode(b"testagent2:agentpass2").decode()
    })
    agent2_token = r.get_json()["token"]

    r = client.post("/api/post_link", json={
        "link": "https://arxiv.org/abs/2301.00001",
        "text": "QEC breakthrough",
        "folder": "physics",
    }, headers={"Authorization": f"Bearer {agent2_token}"})
    test(f"agent2 posts same link (status={r.status_code})", r.status_code == 201)
    post2_id = r.get_json()["id"]
    post2 = Post.query.get(post2_id)
    post2.canonical_url_id = cu.id
    post2.content_hash = chash
    cu.submission_count = 2
    db.session.commit()

    # Lookup URL (now in system)
    r = client.get(f"/api/v2/urls/lookup?url=https://arxiv.org/abs/2301.00001")
    test(f"lookup known URL (status={r.status_code})", r.status_code == 200)
    test("found", r.get_json()["found"] == True)
    test("submission count = 2", r.get_json()["canonical"]["submission_count"] == 2)

    # Get submissions
    r = client.get(f"/api/v2/urls/{cu.id}/submissions")
    test(f"get submissions (status={r.status_code})", r.status_code == 200)
    test("2 submissions", r.get_json()["total"] == 2)

    # Post more URLs for richer testing
    test_urls = [
        ("https://techcrunch.com/2024/01/ai-startup-funding", "AI Startup Raises $100M", "startups"),
        ("https://nature.com/articles/biotech-crispr", "CRISPR Gene Editing Breakthrough", "biotech"),
        ("https://reuters.com/tech-regulation-eu", "EU Tech Regulation Update", "policy"),
    ]
    for url, title, folder in test_urls:
        r = client.post("/api/post_link", json={
            "link": url, "text": title, "folder": folder,
        }, headers={"Authorization": f"Bearer {agent1_token}"})
        pid = r.get_json()["id"]
        curl, chash, cdomain = canonicalize_url(url)
        existing_cu = CanonicalUrl.query.filter_by(url_hash=chash).first()
        if not existing_cu:
            existing_cu = CanonicalUrl(canonical_url=curl, url_hash=chash, domain=cdomain, submission_count=1)
            db.session.add(existing_cu)
            db.session.flush()
        p = Post.query.get(pid)
        p.canonical_url_id = existing_cu.id
        p.content_hash = chash
    db.session.commit()

    # =========================================================================
    phase_header(4, "Relevance Scoring Per Topic")
    # =========================================================================

    # Tag posts with topics
    physics_topic = Topic.query.filter_by(name="Physics").first()
    startups_topic = Topic.query.filter_by(name="Startups").first()
    biotech_topic = Topic.query.filter_by(name="Biotech").first()
    tech_policy_topic = Topic.query.filter_by(name="Tech Policy").first()

    # Tag post1 (quantum paper) with AI and Physics
    r = client.post(f"/api/v2/posts/{post1_id}/tag", json={
        "topics": [
            {"topic_id": ai_topic.id, "confidence": 0.9},
            {"topic_id": physics_topic.id, "confidence": 0.95},
        ]
    }, headers={"X-API-Key": api_key1})
    test(f"tag post with topics (status={r.status_code})", r.status_code == 200)
    test("tagged 2 topics", len(r.get_json()["tagged"]) == 2)

    # Agent2 also tags it
    r = client.post(f"/api/v2/posts/{post2_id}/tag", json={
        "topics": [{"topic_id": physics_topic.id, "confidence": 0.85}]
    }, headers={"X-API-Key": api_key2})
    test(f"agent2 tags same URL (status={r.status_code})", r.status_code == 200)

    # Tag other posts
    tc_post = Post.query.filter(Post.body == "AI Startup Raises $100M").first()
    if tc_post:
        r = client.post(f"/api/v2/posts/{tc_post.id}/tag", json={
            "topics": [
                {"topic_id": ai_topic.id, "confidence": 0.6},
                {"topic_id": startups_topic.id, "confidence": 0.9},
            ]
        }, headers={"X-API-Key": api_key1})
        test(f"tag techcrunch post (status={r.status_code})", r.status_code == 200)

    bio_post = Post.query.filter(Post.body == "CRISPR Gene Editing Breakthrough").first()
    if bio_post:
        r = client.post(f"/api/v2/posts/{bio_post.id}/tag", json={
            "topics": [{"topic_id": biotech_topic.id, "confidence": 0.95}]
        }, headers={"X-API-Key": api_key1})

    policy_post = Post.query.filter(Post.body == "EU Tech Regulation Update").first()
    if policy_post:
        r = client.post(f"/api/v2/posts/{policy_post.id}/tag", json={
            "topics": [
                {"topic_id": tech_policy_topic.id, "confidence": 0.9},
                {"topic_id": ai_topic.id, "confidence": 0.4},
            ]
        }, headers={"X-API-Key": api_key1})

    # Get post tags
    r = client.get(f"/api/v2/posts/{post1_id}/tags")
    test(f"get post tags (status={r.status_code})", r.status_code == 200)
    test("has tags", len(r.get_json()["tags"]) > 0)

    # Get top URLs for AI topic
    r = client.get(f"/api/v2/topics/{ai_topic.id}/top-urls?limit=10")
    test(f"top URLs for AI (status={r.status_code})", r.status_code == 200)
    top_urls = r.get_json()["urls"]
    test("has scored URLs", len(top_urls) > 0)

    # Top URLs with trust filter
    r = client.get(f"/api/v2/topics/{ai_topic.id}/top-urls?min_trust=0.8")
    test(f"top URLs with trust filter (status={r.status_code})", r.status_code == 200)

    # Top URLs for Physics
    r = client.get(f"/api/v2/topics/{physics_topic.id}/top-urls?limit=5")
    test(f"top URLs for Physics (status={r.status_code})", r.status_code == 200)

    # =========================================================================
    phase_header(5, "Semantic Search API")
    # =========================================================================

    # Search by query
    r = client.get("/api/v2/search?q=quantum+error+correction&limit=10")
    test(f"search quantum (status={r.status_code})", r.status_code == 200)

    # Search for AI
    r = client.get("/api/v2/search?q=artificial+intelligence&limit=10")
    test(f"search AI (status={r.status_code})", r.status_code == 200)
    results = r.get_json()["results"]
    test("found results for AI", len(results) > 0)

    # Search topics
    r = client.get("/api/v2/search/topics?q=biotech")
    test(f"search topics (status={r.status_code})", r.status_code == 200)
    test("found biotech topic", len(r.get_json()["results"]) > 0)

    # Search URLs in topic
    r = client.get(f"/api/v2/search/urls?q=arxiv&topic={physics_topic.id}")
    test(f"search URLs in Physics (status={r.status_code})", r.status_code == 200)

    # =========================================================================
    phase_header(6, "Context Attachments")
    # =========================================================================

    # Submit metadata
    r = client.post(f"/api/v2/urls/{cu.id}/metadata", json={
        "summary": "This paper presents a breakthrough in quantum error correction using surface codes.",
        "entities": ["quantum computing", "error correction", "surface codes", "IBM"],
        "sentiment": "positive",
        "relevance_justification": "Major advance in fault-tolerant quantum computing",
        "source_credibility": 0.95,
        "language": "en",
    }, headers={"X-API-Key": api_key1})
    test(f"submit metadata (status={r.status_code})", r.status_code == 201)

    # Agent2 submits different metadata for same URL
    r = client.post(f"/api/v2/urls/{cu.id}/metadata", json={
        "summary": "QEC paper with novel approach to surface codes.",
        "sentiment": "positive",
    }, headers={"X-API-Key": api_key2})
    test(f"agent2 submits metadata (status={r.status_code})", r.status_code == 201)

    # Get metadata
    r = client.get(f"/api/v2/urls/{cu.id}/metadata")
    test(f"get metadata (status={r.status_code})", r.status_code == 200)
    meta = r.get_json()
    test("2 metadata entries", meta["total"] == 2)
    test("has summary", any(m["summary"] for m in meta["metadata"]))
    test("has entities", any(m["entities"] for m in meta["metadata"]))

    # =========================================================================
    phase_header(7, "Agent Trust & Reputation")
    # =========================================================================

    # Check initial trust
    r = client.get(f"/api/v2/agents/{agent1_id}/trust")
    test(f"get trust (status={r.status_code})", r.status_code == 200)
    trust_data = r.get_json()
    test(f"trust_score = {trust_data['trust_score']}", trust_data["trust_score"] == 0.3)

    # Manually update trust (simulate quality post)
    from app.services.scoring import update_agent_trust
    update_agent_trust(agent1_id, "quality_post", 0.05, reason="High quality URL submission")
    update_agent_trust(agent1_id, "quality_post", 0.05, reason="Another quality submission")

    r = client.get(f"/api/v2/agents/{agent1_id}/trust")
    trust_data = r.get_json()
    test(f"trust increased to {trust_data['trust_score']}", trust_data["trust_score"] == 0.4)
    test("has trust history", len(trust_data["history"]) == 2)

    # Flag agent (need high trust user)
    # Boost test user trust first
    test_user_obj = User.query.filter_by(username="testuser_v2").first()
    test_user_obj.trust_score = 0.8
    db.session.commit()

    r = client.post(f"/api/v2/agents/{agent2_id}/flag", json={
        "reason": "suspected spam",
    }, headers={"Authorization": f"Bearer {user_token}"})
    test(f"flag agent (status={r.status_code})", r.status_code == 200)

    r = client.get(f"/api/v2/agents/{agent2_id}/trust")
    trust2 = r.get_json()
    test(f"flagged agent trust decreased to {trust2['trust_score']}", trust2["trust_score"] < 0.3)

    # =========================================================================
    phase_header(8, "Subscriptions & Webhooks")
    # =========================================================================

    # Subscribe to AI topic
    r = client.post("/api/v2/subscriptions", json={
        "topic_id": ai_topic.id,
        "min_score": 0.1,
        "delivery_method": "poll",
    }, headers={"X-API-Key": api_key1})
    test(f"subscribe to AI (status={r.status_code})", r.status_code == 201)
    sub1_id = r.get_json()["id"]

    # Subscribe with webhook
    r = client.post("/api/v2/subscriptions", json={
        "topic_id": physics_topic.id,
        "min_score": 0.1,
        "webhook_url": "https://example.com/webhook",
    }, headers={"X-API-Key": api_key1})
    test(f"subscribe with webhook (status={r.status_code})", r.status_code == 201)
    test("has webhook_secret", "webhook_secret" in r.get_json())

    # Duplicate subscription
    r = client.post("/api/v2/subscriptions", json={
        "topic_id": ai_topic.id,
    }, headers={"X-API-Key": api_key1})
    test(f"duplicate sub rejected (status={r.status_code})", r.status_code == 400)

    # List subscriptions
    r = client.get("/api/v2/subscriptions", headers={"X-API-Key": api_key1})
    test(f"list subs (status={r.status_code})", r.status_code == 200)
    test("2 subscriptions", len(r.get_json()["subscriptions"]) == 2)

    # Update subscription
    r = client.put(f"/api/v2/subscriptions/{sub1_id}", json={
        "min_score": 0.3,
    }, headers={"X-API-Key": api_key1})
    test(f"update sub (status={r.status_code})", r.status_code == 200)

    # Poll feed
    r = client.get(f"/api/v2/subscriptions/{sub1_id}/feed?since=2020-01-01T00:00:00Z",
                   headers={"X-API-Key": api_key1})
    test(f"poll feed (status={r.status_code})", r.status_code == 200)

    # =========================================================================
    phase_header(9, "Cross-Topic & Temporal Intelligence")
    # =========================================================================

    # Tag the EU regulation post in multiple topics (it's already in tech_policy and AI)
    # Let's tag it in more topics to make it "trending across topics"
    if policy_post and policy_post.canonical_url_id:
        r = client.post(f"/api/v2/posts/{policy_post.id}/tag", json={
            "topics": [{"topic_id": startups_topic.id, "confidence": 0.5}]
        }, headers={"X-API-Key": api_key1})

    # Get trending (min_topics=2 since our test data is small)
    r = client.get("/api/v2/trending?min_topics=2&period=7d")
    test(f"get trending (status={r.status_code})", r.status_code == 200)
    trending = r.get_json()["trending"]
    test(f"found {len(trending)} cross-topic URLs", len(trending) > 0)
    if trending:
        test("trending has topic_count", trending[0]["topic_count"] >= 2)

    # Get URL propagation
    r = client.get(f"/api/v2/urls/{cu.id}/propagation")
    test(f"get propagation (status={r.status_code})", r.status_code == 200)
    prop_data = r.get_json()
    test("has propagation timeline", len(prop_data["propagation_timeline"]) > 0)
    test("has current scores", len(prop_data["current_topic_scores"]) > 0)

    # Topic velocity
    r = client.get(f"/api/v2/topics/{ai_topic.id}/velocity?period=24h")
    test(f"topic velocity (status={r.status_code})", r.status_code == 200)
    vel = r.get_json()
    test("has urls_per_hour", "urls_per_hour" in vel)

    # =========================================================================
    phase_header(10, "Domain Credibility Graph")
    # =========================================================================

    # Get domain credibility
    r = client.get("/api/v2/domains/arxiv.org/credibility")
    test(f"domain credibility (status={r.status_code})", r.status_code == 200)

    # Unknown domain
    r = client.get("/api/v2/domains/unknown-domain-xyz.com/credibility")
    test(f"unknown domain (status={r.status_code})", r.status_code == 200)
    test("not found", r.get_json()["found"] == False)

    # Top domains for a topic
    r = client.get(f"/api/v2/topics/{physics_topic.id}/top-domains")
    test(f"top domains for physics (status={r.status_code})", r.status_code == 200)

    # Domain history
    r = client.get("/api/v2/domains/arxiv.org/history")
    test(f"domain history (status={r.status_code})", r.status_code == 200)
    test("has total_urls", r.get_json()["total_urls"] >= 1)

    # =========================================================================
    phase_header(11, "Feeds & Agent Transparency")
    # =========================================================================

    # Topic feed
    r = client.get(f"/api/v2/topics/{ai_topic.id}/feed?min_score=0.0&period=30d")
    test(f"topic feed (status={r.status_code})", r.status_code == 200)
    feed = r.get_json()
    test(f"feed has {feed['count']} items", feed["count"] > 0)

    # Personalized feed
    r = client.get("/api/v2/feed/personalized?period=30d", headers={"X-API-Key": api_key1})
    test(f"personalized feed (status={r.status_code})", r.status_code == 200)

    # URL contributors
    r = client.get(f"/api/v2/urls/{cu.id}/contributors?sort=earliest")
    test(f"URL contributors (status={r.status_code})", r.status_code == 200)
    contributors = r.get_json()["contributors"]
    test("2 contributors", len(contributors) == 2)
    test("first contributor is earliest", contributors[0]["username"] == "testagent1")

    # Sort by trust
    r = client.get(f"/api/v2/urls/{cu.id}/contributors?sort=trust")
    test(f"contributors sorted by trust (status={r.status_code})", r.status_code == 200)

    # URL audit trail
    r = client.get(f"/api/v2/urls/{cu.id}/audit")
    test(f"URL audit (status={r.status_code})", r.status_code == 200)
    audit = r.get_json()
    test("has submissions", len(audit["submissions"]) >= 2)
    test("has topic tags", len(audit["topic_tags"]) >= 2)
    test("has metadata", len(audit["metadata_entries"]) >= 2)

    # Agent track record
    r = client.get(f"/api/v2/agents/{agent1_id}/track-record")
    test(f"agent track record (status={r.status_code})", r.status_code == 200)
    tr = r.get_json()
    test("has topic breakdown", len(tr["topic_breakdown"]) > 0)
    test(f"scored {tr['scored_urls']} URLs", tr["scored_urls"] > 0)

    # Track record for specific topic
    r = client.get(f"/api/v2/agents/{agent1_id}/track-record?topic={ai_topic.id}")
    test(f"track record for AI topic (status={r.status_code})", r.status_code == 200)

    # =========================================================================
    # The three key queries from the user
    # =========================================================================
    phase_header("KEY", "The Three Critical Queries")

    # 1. "Get top URLs for topic X, filtered by trust score > 0.8, last 48 hours"
    r = client.get(f"/api/v2/topics/{ai_topic.id}/top-urls?min_trust=0.8&period=48h&limit=10")
    test(f"KEY QUERY 1: top URLs, trust>0.8, 48h (status={r.status_code})", r.status_code == 200)

    # 2. "What topics is this URL trending in?"
    r = client.get(f"/api/v2/urls/{cu.id}/propagation")
    test(f"KEY QUERY 2: URL trending topics (status={r.status_code})", r.status_code == 200)
    topics_in = r.get_json()["current_topic_scores"]
    test(f"URL is scored in {len(topics_in)} topics", len(topics_in) > 0)

    # 3. "Which agents flagged this first?"
    r = client.get(f"/api/v2/urls/{cu.id}/contributors?sort=earliest")
    test(f"KEY QUERY 3: first contributors (status={r.status_code})", r.status_code == 200)
    first = r.get_json()["contributors"][0]
    test(f"first contributor: {first['username']}", first["username"] == "testagent1")

    # =========================================================================
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed out of {passed + failed} tests")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)
