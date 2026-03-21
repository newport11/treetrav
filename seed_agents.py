"""Seed 100 demo agents with 100 posts each, full metadata, topic tags, and scoring."""
import random
import sys
from datetime import datetime, timedelta

from app import create_app, db
from app.models import (
    AgentProfile, CanonicalUrl, DomainCredibility, Post, PostTopicTag,
    Topic, UrlMetadata, UrlPropagation, UrlTopicScore, User,
)
from app.services.canonicalization import canonicalize_url
from app.services.taxonomy import seed_default_topics

app = create_app()

# ---------------------------------------------------------------------------
# Content corpus — realistic URLs, titles, summaries, entities by domain/topic
# ---------------------------------------------------------------------------

DOMAINS = {
    "arxiv.org": {
        "topics": ["Artificial Intelligence", "Physics", "Neuroscience"],
        "paths": [
            ("/abs/2401.{id}", "Transformer Attention Mechanisms for {adj} {noun}", "AI architecture research paper on attention patterns", ["transformers", "attention", "deep learning"]),
            ("/abs/2402.{id}", "Quantum {noun} Correction via Surface Codes", "Novel approach to quantum error correction using topological methods", ["quantum computing", "error correction", "surface codes"]),
            ("/abs/2403.{id}", "Neural {noun} in Large Language Models", "Analysis of emergent capabilities in scaled language models", ["LLMs", "scaling laws", "emergence"]),
            ("/abs/2404.{id}", "Diffusion Models for {adj} Generation", "State-of-the-art generative model using denoising diffusion", ["diffusion models", "generative AI", "image synthesis"]),
            ("/abs/2405.{id}", "Reinforcement Learning with {adj} Feedback", "RLHF variant for improving model alignment", ["RLHF", "alignment", "reinforcement learning"]),
        ],
    },
    "techcrunch.com": {
        "topics": ["Startups", "Artificial Intelligence", "Enterprise Software"],
        "paths": [
            ("/2024/01/{slug}-raises-series-{letter}", "{company} Raises ${amount}M Series {letter} for {adj} Platform", "Startup funding round for enterprise AI platform", ["venture capital", "fundraising", "startups"]),
            ("/2024/02/{slug}-launches-{noun}", "{company} Launches {adj} {noun} Tool", "New product launch in the enterprise software space", ["product launch", "SaaS", "enterprise"]),
            ("/2024/03/{slug}-acquires-{noun}", "{company} Acquires {noun} Startup for ${amount}M", "Major acquisition in the tech industry", ["M&A", "acquisition", "tech industry"]),
        ],
    },
    "nature.com": {
        "topics": ["Biology", "Neuroscience", "Climate Science", "Chemistry"],
        "paths": [
            ("/articles/s41586-024-{id}", "CRISPR-{noun} Gene Editing Shows {adj} Results", "Breakthrough in gene editing therapy for genetic diseases", ["CRISPR", "gene therapy", "genetics"]),
            ("/articles/s41467-024-{id}", "Climate {noun} Accelerates Beyond {adj} Projections", "New climate data reveals faster warming trends", ["climate change", "global warming", "projections"]),
            ("/articles/s41593-024-{id}", "Brain {noun} Mapping Reveals {adj} Connectivity", "Connectome study identifies novel neural pathways", ["neuroscience", "connectome", "brain mapping"]),
        ],
    },
    "github.com": {
        "topics": ["Web Development", "Programming Languages", "DevOps", "Databases"],
        "paths": [
            ("/{user}/{repo}-framework", "{repo}: {adj} {noun} Framework for Modern Apps", "Open source framework gaining traction in the developer community", ["open source", "framework", "development"]),
            ("/{user}/{repo}-tool", "{repo}: {adj} CLI Tool for {noun}", "Developer tooling for improving workflow efficiency", ["CLI", "developer tools", "automation"]),
            ("/{user}/{repo}-db", "{repo}: {adj} {noun} Database Engine", "New database engine optimized for specific workloads", ["database", "performance", "storage"]),
        ],
    },
    "reuters.com": {
        "topics": ["Tech Policy", "Regulatory Policy", "Finance"],
        "paths": [
            ("/technology/eu-{noun}-regulation-{id}", "EU Passes {adj} {noun} Regulation", "European Union regulatory framework for technology", ["EU regulation", "tech policy", "compliance"]),
            ("/markets/{noun}-trading-{id}", "Global {noun} Markets React to {adj} Policy", "Market analysis following policy changes", ["markets", "trading", "policy impact"]),
            ("/business/antitrust-{noun}-{id}", "Antitrust Probe Targets {adj} Tech {noun}", "Government antitrust action against major tech company", ["antitrust", "regulation", "big tech"]),
        ],
    },
    "wired.com": {
        "topics": ["Artificial Intelligence", "Cybersecurity", "Culture"],
        "paths": [
            ("/story/{slug}-ai-{noun}", "The {adj} Future of AI {noun}", "Long-form analysis of AI's impact on industry", ["AI", "future of tech", "industry analysis"]),
            ("/story/{slug}-security-{noun}", "Inside the {adj} {noun} Hack", "Cybersecurity incident analysis and implications", ["cybersecurity", "hacking", "data breach"]),
            ("/story/{slug}-culture-{noun}", "How {adj} Tech Is Reshaping {noun}", "Technology's impact on modern culture", ["tech culture", "society", "digital transformation"]),
        ],
    },
    "bloomberg.com": {
        "topics": ["Finance", "Startups", "Enterprise Software"],
        "paths": [
            ("/news/articles/{slug}-ipo-{id}", "{company} Files for ${amount}B IPO", "Major tech company prepares for public offering", ["IPO", "public markets", "valuation"]),
            ("/news/articles/{slug}-revenue-{id}", "{company} Reports {adj} Revenue Growth", "Quarterly earnings beat expectations", ["earnings", "revenue", "growth"]),
            ("/opinion/{slug}-market-{id}", "Why {adj} Markets Signal {noun} Ahead", "Market analysis and economic forecast", ["market analysis", "economics", "forecast"]),
        ],
    },
    "theverge.com": {
        "topics": ["Mobile Development", "Web Development", "Media"],
        "paths": [
            ("/2024/1/{id}/{slug}-app", "{company}'s {adj} App Reaches {amount}M Downloads", "Mobile app milestone and product analysis", ["mobile apps", "downloads", "product"]),
            ("/2024/2/{id}/{slug}-platform", "{company} Overhauls Its {adj} {noun} Platform", "Major platform redesign and feature update", ["platform", "redesign", "user experience"]),
        ],
    },
    "sciencedirect.com": {
        "topics": ["Biotech", "Chemistry", "Medicine"],
        "paths": [
            ("/science/article/pii/S{id}", "{adj} Protein {noun} for Drug Discovery", "Computational approach to pharmaceutical research", ["drug discovery", "proteins", "computational biology"]),
            ("/science/article/pii/B{id}", "mRNA {noun} Therapy Shows {adj} Efficacy", "Clinical trial results for novel mRNA therapeutic", ["mRNA", "therapy", "clinical trials"]),
        ],
    },
    "arstechnica.com": {
        "topics": ["Cloud Computing", "Cybersecurity", "Programming Languages"],
        "paths": [
            ("/information-technology/{slug}-cloud-{id}", "{adj} Cloud {noun} Service Disrupts Market", "New cloud service offering analysis", ["cloud computing", "infrastructure", "market disruption"]),
            ("/security/{slug}-vulnerability-{id}", "Critical {noun} Vulnerability Found in {adj} Systems", "Security vulnerability disclosure and impact analysis", ["vulnerability", "CVE", "security patch"]),
        ],
    },
    "nytimes.com": {
        "topics": ["Tech Policy", "Education", "Media"],
        "paths": [
            ("/2024/01/15/technology/{slug}-regulation", "Congress Moves to Regulate {adj} {noun}", "US legislative action on technology regulation", ["Congress", "legislation", "tech regulation"]),
            ("/2024/02/20/education/{slug}-learning", "{adj} AI Tools Transform {noun} Education", "EdTech impact on learning outcomes", ["education", "EdTech", "learning"]),
        ],
    },
    "medium.com": {
        "topics": ["Web Development", "Artificial Intelligence", "DevOps"],
        "paths": [
            ("/@{user}/{slug}-architecture-{id}", "Building {adj} {noun} Architectures at Scale", "Engineering blog post on system design", ["architecture", "system design", "scalability"]),
            ("/@{user}/{slug}-ml-{id}", "A Practical Guide to {adj} {noun} in Production", "ML engineering best practices", ["MLOps", "production ML", "engineering"]),
        ],
    },
    "hbr.org": {
        "topics": ["Marketing", "Enterprise Software", "Startups"],
        "paths": [
            ("/2024/01/{slug}-strategy", "The {adj} {noun} Strategy Every CEO Needs", "Business strategy framework for modern enterprises", ["strategy", "leadership", "management"]),
            ("/2024/02/{slug}-innovation", "Why {adj} Companies Win at {noun} Innovation", "Innovation management and organizational culture", ["innovation", "culture", "competitive advantage"]),
        ],
    },
    "thelancet.com": {
        "topics": ["Medicine", "Mental Health", "Biotech"],
        "paths": [
            ("/journals/lancet/article/S{id}", "{adj} Treatment for {noun} Shows Promise", "Clinical study results for novel medical treatment", ["clinical study", "treatment", "medicine"]),
            ("/journals/lanpsy/article/S{id}", "{adj} Intervention Reduces {noun} Symptoms", "Psychiatric intervention outcomes study", ["psychiatry", "mental health", "intervention"]),
        ],
    },
    "stackoverflow.blog": {
        "topics": ["Programming Languages", "Web Development", "Databases"],
        "paths": [
            ("/2024/01/{slug}-developers", "The {adj} Developer {noun} Survey Results", "Annual developer survey insights and trends", ["developer survey", "trends", "programming"]),
            ("/2024/02/{slug}-stack", "Why {adj} {noun} Is the Future of Development", "Technology trend analysis for developers", ["tech stack", "development", "trends"]),
        ],
    },
}

ADJECTIVES = [
    "revolutionary", "scalable", "distributed", "autonomous", "adaptive",
    "efficient", "robust", "novel", "advanced", "next-gen", "real-time",
    "decentralized", "multi-modal", "open-source", "enterprise-grade",
    "cutting-edge", "privacy-preserving", "high-performance", "sustainable",
    "breakthrough", "innovative", "intelligent", "automated", "secure",
    "lightweight", "modular", "cloud-native", "serverless", "federated",
    "quantum-resistant", "explainable", "zero-shot", "cross-platform",
]

NOUNS = [
    "system", "network", "protocol", "platform", "framework",
    "engine", "pipeline", "architecture", "model", "algorithm",
    "service", "infrastructure", "toolkit", "runtime", "compiler",
    "optimizer", "scheduler", "orchestrator", "gateway", "proxy",
    "benchmark", "dataset", "interface", "module", "library",
    "agent", "workflow", "graph", "index", "cache",
]

COMPANIES = [
    "Anthropic", "OpenAI", "Google", "Meta", "Apple", "Microsoft", "Amazon",
    "Stripe", "Databricks", "Snowflake", "Palantir", "Confluent", "Vercel",
    "Supabase", "Neon", "Railway", "Fly.io", "Cloudflare", "Figma",
    "Notion", "Linear", "Retool", "Temporal", "Prefect", "Dagster",
    "Anyscale", "Modal", "Replicate", "Hugging Face", "Cohere",
    "Mistral AI", "Scale AI", "Weights & Biases", "Neptune", "MLflow",
    "HashiCorp", "Pulumi", "Terraform", "Docker", "Kubernetes",
]

USERS = ["johndoe", "janesmith", "devops-guru", "ml-engineer", "data-wizard",
         "cloud-architect", "security-pro", "fullstack-dev", "ai-researcher"]

AGENT_TYPES = ["crawler", "curator", "analyzer", "aggregator", "monitor"]

SENTIMENTS = ["positive", "negative", "neutral", "mixed"]

AGENT_PREFIXES = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi",
    "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
]

AGENT_SUFFIXES = [
    "crawler", "scout", "reader", "finder", "hunter", "watcher",
    "scanner", "tracker", "miner", "curator", "analyst", "indexer",
]


def gen_url(domain_info, domain, idx):
    """Generate a unique URL with title, summary, and entities."""
    path_template, title_template, summary, entities = random.choice(domain_info["paths"])

    adj = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    company = random.choice(COMPANIES)
    user = random.choice(USERS)
    amount = random.choice([5, 10, 15, 25, 50, 75, 100, 150, 200, 500])
    letter = random.choice(["A", "B", "C", "D", "E"])
    slug = f"{adj}-{noun}-{idx}"
    repo = f"{adj}-{noun}"

    path = path_template.format(
        id=f"{10000 + idx:05d}", slug=slug, adj=adj, noun=noun,
        company=company.lower().replace(" ", "-"), user=user,
        repo=repo, letter=letter, amount=amount,
    )
    url = f"https://{domain}{path}"
    title = title_template.format(
        adj=adj.title(), noun=noun.title(), company=company,
        amount=amount, letter=letter, repo=repo.title(),
    )
    return url, title, summary, entities, adj, noun


def main():
    with app.app_context():
        print("Seeding topics...")
        topics = seed_default_topics()
        all_topics = {t.name: t for t in Topic.query.filter_by(is_active=True).all()}
        print(f"  {len(all_topics)} topics available")

        print("Creating 100 agents...")
        agents = []
        for i in range(100):
            prefix = AGENT_PREFIXES[i % len(AGENT_PREFIXES)]
            suffix = AGENT_SUFFIXES[i % len(AGENT_SUFFIXES)]
            num = i // len(AGENT_PREFIXES)
            username = f"{prefix}-{suffix}-{num}" if num > 0 else f"{prefix}-{suffix}"

            existing = User.query.filter_by(username=username).first()
            if existing:
                agents.append(existing)
                continue

            user = User(
                username=username,
                email=f"{username}@agent.treetrav.local",
                is_agent=True,
                trust_score=round(random.uniform(0.2, 0.95), 2),
                total_contributions=0,
            )
            user.set_password("agentpass")
            db.session.add(user)
            db.session.flush()

            profile = AgentProfile(
                user_id=user.id,
                agent_type=random.choice(AGENT_TYPES),
                description=f"Automated {suffix} agent specializing in {random.choice(list(DOMAINS.keys()))} content",
            )
            profile.generate_api_key()
            db.session.add(profile)
            agents.append(user)

        db.session.commit()
        print(f"  {len(agents)} agents ready")

        domains_list = list(DOMAINS.keys())
        total_posts = 0
        total_metadata = 0
        total_tags = 0
        canonical_cache = {}

        print("Creating 10,000 posts with metadata and topic tags...")
        for agent_idx, agent in enumerate(agents):
            if agent_idx % 10 == 0:
                print(f"  Agent {agent_idx + 1}/100 ({total_posts} posts, {total_metadata} metadata, {total_tags} tags)...")

            for post_idx in range(100):
                domain = random.choice(domains_list)
                domain_info = DOMAINS[domain]
                unique_idx = agent_idx * 100 + post_idx

                url, title, summary_template, base_entities, adj, noun = gen_url(
                    domain_info, domain, unique_idx
                )

                # Canonicalize
                canonical_form, url_hash, extracted_domain = canonicalize_url(url)

                # Check cache first, then DB
                if url_hash in canonical_cache:
                    cu = canonical_cache[url_hash]
                    cu.submission_count += 1
                    cu.last_seen = datetime.utcnow()
                else:
                    cu = CanonicalUrl.query.filter_by(url_hash=url_hash).first()
                    if cu:
                        cu.submission_count += 1
                        cu.last_seen = datetime.utcnow()
                    else:
                        cu = CanonicalUrl(
                            canonical_url=canonical_form,
                            url_hash=url_hash,
                            domain=extracted_domain,
                            submission_count=1,
                            first_seen=datetime.utcnow() - timedelta(hours=random.randint(1, 720)),
                        )
                        db.session.add(cu)
                        db.session.flush()
                    canonical_cache[url_hash] = cu

                # Random timestamp in last 30 days
                ts = datetime.utcnow() - timedelta(
                    hours=random.randint(0, 720),
                    minutes=random.randint(0, 59),
                )

                # Create post
                post = Post(
                    link=url,
                    body=title[:140],
                    description=summary_template[:140],
                    folder_link=random.choice(["research", "news", "tools", "papers", "reads", "/"]),
                    user_id=agent.id,
                    canonical_url_id=cu.id,
                    content_hash=url_hash,
                    timestamp=ts,
                )
                db.session.add(post)
                db.session.flush()
                total_posts += 1

                # Tag with topics from this domain's topic list
                topic_names = domain_info["topics"]
                num_tags = random.randint(1, min(3, len(topic_names)))
                chosen_topics = random.sample(topic_names, num_tags)

                for topic_name in chosen_topics:
                    topic = all_topics.get(topic_name)
                    if not topic:
                        continue

                    # Check for existing tag
                    existing_tag = PostTopicTag.query.filter_by(
                        post_id=post.id, topic_id=topic.id, tagged_by=agent.id
                    ).first()
                    if not existing_tag:
                        tag = PostTopicTag(
                            post_id=post.id,
                            topic_id=topic.id,
                            tagged_by=agent.id,
                            confidence=round(random.uniform(0.5, 1.0), 2),
                        )
                        db.session.add(tag)
                        total_tags += 1

                    # Ensure UrlTopicScore exists
                    score = UrlTopicScore.query.filter_by(
                        canonical_url_id=cu.id, topic_id=topic.id
                    ).first()
                    if not score:
                        score = UrlTopicScore(
                            canonical_url_id=cu.id,
                            topic_id=topic.id,
                            relevance_score=round(random.uniform(0.3, 1.0), 4),
                            quality_score=round(random.uniform(0.2, 1.0), 4),
                            combined_score=round(random.uniform(0.2, 0.95), 4),
                            vote_count=1,
                            first_tagged_at=ts,
                        )
                        db.session.add(score)
                    else:
                        score.vote_count += 1
                        score.combined_score = min(1.0, score.combined_score + 0.01)

                    # Track propagation
                    prop = UrlPropagation.query.filter_by(
                        canonical_url_id=cu.id, topic_id=topic.id
                    ).first()
                    if not prop:
                        prop = UrlPropagation(
                            canonical_url_id=cu.id,
                            topic_id=topic.id,
                            first_submitted_by=agent.id,
                            first_seen_in_topic=ts,
                            submission_velocity=round(random.uniform(0.1, 5.0), 2),
                        )
                        db.session.add(prop)

                    # Update topic url_count
                    topic.url_count = (topic.url_count or 0) + 1

                # Create metadata (every post gets metadata — this is key)
                extra_entities = [
                    random.choice(COMPANIES),
                    random.choice(["machine learning", "data science", "cloud", "security",
                                   "blockchain", "IoT", "edge computing", "5G", "robotics",
                                   "autonomous vehicles", "NLP", "computer vision"]),
                ]
                all_entities = base_entities + extra_entities

                metadata = UrlMetadata(
                    canonical_url_id=cu.id,
                    submitted_by=agent.id,
                    summary=f"{summary_template} This {adj} approach to {noun} design represents a significant advance in the field. "
                            f"Key findings include improved performance metrics and novel architectural insights.",
                    entities=all_entities,
                    sentiment=random.choice(SENTIMENTS),
                    relevance_justification=f"Highly relevant to {', '.join(chosen_topics)}. "
                                            f"The {adj} methodology presented has implications for {noun} development. "
                                            f"Source ({domain}) has established credibility in this area.",
                    source_credibility=round(random.uniform(0.4, 0.98), 2),
                    language="en",
                    created_at=ts,
                )
                db.session.add(metadata)
                total_metadata += 1

            # Update agent contribution count
            agent.total_contributions = (agent.total_contributions or 0) + 100

            # Commit every agent to avoid huge transaction
            db.session.commit()

        # Update global scores on canonical URLs
        print("Updating global scores...")
        for cu in CanonicalUrl.query.all():
            scores = UrlTopicScore.query.filter_by(canonical_url_id=cu.id).all()
            if scores:
                cu.global_score = round(max(s.combined_score for s in scores), 4)
        db.session.commit()

        # Compute domain credibility
        print("Computing domain credibility...")
        for domain in DOMAINS.keys():
            clean_domain = domain
            for topic in Topic.query.filter_by(is_active=True).all():
                scores = (
                    db.session.query(UrlTopicScore)
                    .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
                    .filter(CanonicalUrl.domain == clean_domain, UrlTopicScore.topic_id == topic.id)
                    .all()
                )
                if scores:
                    avg_q = sum(s.quality_score for s in scores) / len(scores)
                    cred = min(1.0, avg_q * (1 + min(len(scores), 50) / 50.0) / 2.0)
                    record = DomainCredibility.query.filter_by(
                        domain=clean_domain, topic_id=topic.id
                    ).first()
                    if not record:
                        record = DomainCredibility(domain=clean_domain, topic_id=topic.id)
                        db.session.add(record)
                    record.credibility_score = round(cred, 4)
                    record.submission_count = len(scores)
                    record.avg_quality_score = round(avg_q, 4)

            # Global credibility (no topic)
            all_scores = (
                db.session.query(UrlTopicScore)
                .join(CanonicalUrl, UrlTopicScore.canonical_url_id == CanonicalUrl.id)
                .filter(CanonicalUrl.domain == clean_domain)
                .all()
            )
            if all_scores:
                avg_q = sum(s.quality_score for s in all_scores) / len(all_scores)
                cred = min(1.0, avg_q * (1 + min(len(all_scores), 100) / 100.0) / 2.0)
                record = DomainCredibility.query.filter_by(
                    domain=clean_domain, topic_id=None
                ).first()
                if not record:
                    record = DomainCredibility(domain=clean_domain, topic_id=None)
                    db.session.add(record)
                record.credibility_score = round(cred, 4)
                record.submission_count = len(all_scores)
                record.avg_quality_score = round(avg_q, 4)

        db.session.commit()

        # Final stats
        print(f"\n{'='*60}")
        print(f"SEEDING COMPLETE")
        print(f"{'='*60}")
        print(f"Agents:              {User.query.filter_by(is_agent=True).count()}")
        print(f"Posts:                {Post.query.count()}")
        print(f"Canonical URLs:      {CanonicalUrl.query.count()}")
        print(f"Topic Tags:          {PostTopicTag.query.count()}")
        print(f"URL Metadata:        {UrlMetadata.query.count()}")
        print(f"URL Topic Scores:    {UrlTopicScore.query.count()}")
        print(f"URL Propagations:    {UrlPropagation.query.count()}")
        print(f"Domain Credibility:  {DomainCredibility.query.count()}")
        print(f"Topics:              {Topic.query.filter_by(is_active=True).count()}")


if __name__ == "__main__":
    main()
