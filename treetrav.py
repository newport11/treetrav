from flask_cors import CORS

from app import create_app, db
from app.models import (
    AgentProfile, AgentTrustEvent, CanonicalUrl, DomainCredibility,
    Post, PostTopicTag, Topic, TopicAlias, TopicSubscription,
    UrlMetadata, UrlPropagation, UrlTopicScore, User, WebhookDelivery,
)

app = create_app()
CORS(app)


@app.shell_context_processor
def make_shell_context():
    return {
        "db": db, "User": User, "Post": Post, "Topic": Topic,
        "CanonicalUrl": CanonicalUrl, "AgentProfile": AgentProfile,
        "UrlTopicScore": UrlTopicScore, "PostTopicTag": PostTopicTag,
    }
