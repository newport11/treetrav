import base64
import json
import os
from datetime import datetime, timedelta
from hashlib import md5
from time import time

import jwt
from flask import current_app, url_for
from flask_login import UserMixin
from sqlalchemy import ForeignKeyConstraint, PrimaryKeyConstraint, func
from werkzeug.security import check_password_hash, generate_password_hash

from app import db, login
from app.search import add_to_index, query_index, remove_from_index


class SearchableMixin(object):
    @classmethod
    def search(cls, expression, page, per_page):
        ids, total = query_index(cls.__tablename__, expression, page, per_page)
        if total == 0:
            return cls.query.filter_by(id=0), 0
        when = {}
        for i in range(len(ids)):
            when[ids[i]] = i
        return (
            cls.query.filter(cls.id.in_(ids)).order_by(db.case(when, value=cls.id)),
            total,
        )

    @classmethod
    def before_commit(cls, session):
        session._changes = {
            "add": list(session.new),
            "update": list(session.dirty),
            "delete": list(session.deleted),
        }

    @classmethod
    def after_commit(cls, session):
        for obj in session._changes["add"]:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes["update"]:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes["delete"]:
            if isinstance(obj, SearchableMixin):
                remove_from_index(obj.__tablename__, obj)
        session._changes = None

    @classmethod
    def reindex(cls):
        for obj in cls.query:
            add_to_index(cls.__tablename__, obj)


db.event.listen(db.session, "before_commit", SearchableMixin.before_commit)
db.event.listen(db.session, "after_commit", SearchableMixin.after_commit)


followers = db.Table(
    "followers",
    db.Column("follower_id", db.Integer, db.ForeignKey("user.id")),
    db.Column("followed_id", db.Integer, db.ForeignKey("user.id")),
)

follower_requests = db.Table(
    "follower_requests",
    db.Column("requestor_id", db.Integer, db.ForeignKey("user.id")),
    db.Column("requestee_id", db.Integer, db.ForeignKey("user.id")),
)

user_favorites = db.Table(
    "user_favorites",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id")),
    db.Column("post_id", db.Integer, db.ForeignKey("post.id")),
)


class PaginatedAPIMixin(object):
    @staticmethod
    def to_collection_dict(query, page, per_page, endpoint, **kwargs):
        resources = query.paginate(page=page, per_page=per_page, error_out=False)
        data = {
            "items": [item.to_dict() for item in resources.items],
            "_meta": {
                "page": page,
                "per_page": per_page,
                "total_pages": resources.pages,
                "total_items": resources.total,
            },
            "_links": {
                "self": url_for(endpoint, page=page, per_page=per_page, **kwargs),
                "next": url_for(endpoint, page=page + 1, per_page=per_page, **kwargs)
                if resources.has_next
                else None,
                "prev": url_for(endpoint, page=page - 1, per_page=per_page, **kwargs)
                if resources.has_prev
                else None,
            },
        }
        return data


class User(SearchableMixin, UserMixin, PaginatedAPIMixin, db.Model):
    __searchable__ = ["username"]
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    display_name = db.Column(db.String(64))
    email = db.Column(db.String(120), index=True, unique=True)
    password_hash = db.Column(db.String(128))
    posts = db.relationship("Post", backref="author", lazy="dynamic")
    pic_posts = db.relationship("PostPic", backref="author", lazy="dynamic")
    about_me = db.Column(db.String(140))
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    verified = db.Column(db.Boolean, default=False)
    profile_pic = db.Column(db.String(500))
    private_mode = db.Column(db.Boolean, default=False)
    is_agent = db.Column(db.Boolean, default=False)
    trust_score = db.Column(db.Float, default=0.5)
    total_contributions = db.Column(db.Integer, default=0)
    dark_mode = db.Column(db.Boolean, default=False)
    toggle_color = db.Column(db.String(10), default="#5a9b16")
    toggle_name = db.Column(db.String(8), default="pics")
    description_text_color = db.Column(db.String(10), default="#000000")
    token = db.Column(db.String(32), index=True, unique=True)
    token_expiration = db.Column(db.DateTime)
    followed = db.relationship(
        "User",
        secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref("followers", lazy="dynamic"),
        lazy="dynamic",
    )
    follow_requests = db.relationship(
        "User",
        secondary=follower_requests,
        primaryjoin=(follower_requests.c.requestor_id == id),
        secondaryjoin=(follower_requests.c.requestee_id == id),
        backref=db.backref("follower_requests", lazy="dynamic"),
        lazy="dynamic",
    )
    favorites = db.relationship(
        "Post",
        secondary="user_favorites",
        primaryjoin=(user_favorites.c.user_id == id),
        backref=db.backref("user_favorites", lazy="dynamic"),
        lazy="dynamic",
    )
    share_requests_made = db.relationship(
        "ShareFolderRequest",
        primaryjoin=("User.id == ShareFolderRequest.requestor_id"),
        backref="requestor",
        lazy="dynamic",
    )
    share_requests_received = db.relationship(
        "ShareFolderRequest",
        primaryjoin=("User.id == ShareFolderRequest.requestee_id"),
        backref="requestee",
        lazy="dynamic",
    )
    outbound_shares = db.relationship(
        "ShareFolder",
        primaryjoin=("User.id == ShareFolder.sharer_id"),
        backref="sharer",
        lazy="dynamic",
    )
    inbound_shares = db.relationship(
        "ShareFolder",
        primaryjoin=("User.id == ShareFolder.sharee_id"),
        backref="sharee",
        lazy="dynamic",
    )
    leafs = db.relationship(
        "Leaf", primaryjoin=("User.id == Leaf.user_id"), backref="leaf", lazy="dynamic"
    )

    def to_dict(self, include_email=False):
        data = {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "last_seen": self.last_seen.isoformat() + "Z",
            "about_me": self.about_me,
            "post_count": self.posts.count(),
            "pic_post_count": self.pic_posts.count(),
            "follower_count": self.followers.count(),
            "followed_count": self.followed.count(),
            "verified": self.verified,
            "_links": {
                "self": url_for("api.get_user", id=self.id),
                "followers": url_for("api.get_followers", id=self.id),
                "followed": url_for("api.get_followed", id=self.id),
                "avatar": self.avatar(128),
            },
        }
        if include_email:
            data["email"] = self.email
        return data

    def from_dict(self, data, new_user=False):
        fields = ["username", "email", "about_me"] if new_user else ["email", "about_me"]
        for field in fields:
            if field in data:
                setattr(self, field, data[field])
        if new_user and "password" in data:
            self.set_password(data["password"])

    def update_verification(self, verified):
        setattr(self, "verified", verified)

    def get_token(self, expires_in=3600):
        now = datetime.utcnow()
        if self.token and self.token_expiration > now + timedelta(seconds=60):
            return self.token
        self.token = base64.b64encode(os.urandom(24)).decode("utf-8")
        self.token_expiration = now + timedelta(seconds=expires_in)
        db.session.add(self)
        return self.token

    def revoke_token(self):
        self.token_expiration = datetime.utcnow() - timedelta(seconds=1)

    @staticmethod
    def check_token(token):
        user = User.query.filter_by(token=token).first()
        if user is None or user.token_expiration < datetime.utcnow():
            return None
        return user

    @staticmethod
    def search(query, page, per_page):
        search = f"%{query}%"
        users = User.query.filter(
            db.or_(
                func.lower(User.username).startswith(func.lower(query)),
                func.lower(User.display_name).startswith(func.lower(query)),
            )
        ).paginate(page=page, per_page=per_page, error_out=False)
        return users.items, users.total

    @staticmethod
    def get_suggestions(query):
        # Example implementation: find users whose usernames start with the query
        users = (
            User.query.filter(
                db.or_(
                    func.lower(User.username).startswith(func.lower(query)),
                    func.lower(User.display_name).startswith(func.lower(query)),
                )
            )
            .order_by(User.username.asc())
            .limit(5)
            .all()
        )
        return [user.username for user in users]

    def __repr__(self):
        return f"<User {self.username}>"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def avatar(self, size):
        digest = md5(self.email.lower().encode("utf-8")).hexdigest()
        return "https://www.gravatar.com/avatar/{}?d=robohash&s={}".format(
            digest, size
        )

    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)

    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)

    def is_following(self, user):
        return self.followed.filter(followers.c.followed_id == user.id).count() > 0

    def request_follow(self, user):
        if not self.is_requested(user):
            self.follow_requests.append(user)

    def unrequest_follow(self, user):
        if self.is_requested(user):
            self.follow_requests.remove(user)

    def is_requested(self, user):
        return (
            self.follow_requests.filter(
                follower_requests.c.requestee_id == user.id
            ).count()
            > 0
        )

    def get_follow_requestors(self):
        requests = User.query.join(
            follower_requests, (follower_requests.c.requestor_id == User.id)
        ).filter(follower_requests.c.requestee_id == self.id)
        return requests.order_by(User.id.desc())

    # Share request function for pushing shared folders
    def is_share_requested(self, user, folder_path):
        existing_request = ShareFolderRequest.query.filter_by(
            requestor_id=self.id, requestee_id=user.id, shared_folder_path=folder_path
        ).first()
        return existing_request

    # check if share already exists
    def is_share(self, sharer_id, sharer_folder_path, sharee_folder_path):
        existing_share = ShareFolder.query.filter_by(
            sharer_id=sharer_id,
            sharee_id=self.id,
            sharer_folder_path=sharer_folder_path,
            sharee_folder_path=sharee_folder_path,
        ).first()
        return existing_share

    def followed_posts(self):
        followed = Post.query.join(
            followers, (followers.c.followed_id == Post.user_id)
        ).filter(followers.c.follower_id == self.id)
        own = Post.query.filter_by(user_id=self.id)
        return followed.union(own).order_by(Post.timestamp.desc())

    def favorite(self, post):
        if not self.is_favorite(post):
            self.favorites.append(post)

    def unfavorite(self, post):
        if self.is_favorite(post):
            self.favorites.remove(post)

    def is_favorite(self, post):
        return self.favorites.filter(user_favorites.c.post_id == post.id).count() > 0

    def favorite_posts(self):
        favorites = Post.query.join(
            user_favorites, (user_favorites.c.post_id == Post.id)
        ).filter(user_favorites.c.user_id == self.id)
        return favorites.order_by(Post.timestamp.desc())

    def get_reset_password_token(self, expires_in=600):
        return jwt.encode(
            {"reset_password": self.id, "exp": time() + expires_in},
            current_app.config["SECRET_KEY"],
            algorithm="HS256",
        )

    @staticmethod
    def verify_reset_password_token(token):
        try:
            id = jwt.decode(
                token, current_app.config["SECRET_KEY"], algorithms=["HS256"]
            )["reset_password"]
        except:
            return
        return User.query.get(id)


@login.user_loader
def load_user(id):
    return User.query.get(int(id))


class Post(db.Model):
    # __searchable__ = ['body']
    id = db.Column(db.Integer, primary_key=True)
    link = db.Column(db.String(2048))
    body = db.Column(db.String(140))
    description = db.Column(db.String(140))
    folder_name = db.Column(db.String(255))
    folder_link = db.Column(db.String(1000))
    favicon_file_name = db.Column(db.String(500))
    is_shared = db.Column(db.Boolean)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    canonical_url_id = db.Column(
        db.Integer, db.ForeignKey("canonical_url.id"), nullable=True, index=True
    )
    content_hash = db.Column(db.String(64), nullable=True, index=True)

    # New columns to link to Leaf
    leaves = db.relationship("Leaf", backref="post", lazy=True)
    topic_tags = db.relationship("PostTopicTag", backref="post", lazy=True)

    def to_dict(self):
        data = {
            "id": self.id,
            "link": self.link,
            "body": self.body,
            "description": self.description,
            "folder_name": self.folder_name,
            "folder_link": self.folder_link,
            "favicon_file_name": self.favicon_file_name,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "canonical_url_id": self.canonical_url_id,
            "leaves": [leaf.to_dict() for leaf in self.leaves],
            "topics": [
                {"topic_id": t.topic_id, "confidence": t.confidence}
                for t in self.topic_tags
            ],
            "_links": {"self": url_for("api.get_post", id=self.id)},
        }

        return data

    def __repr__(self):
        return "<Post {}>".format(self.body)


class PostPic(db.Model):
    # __searchable__ = ['body']
    id = db.Column(db.Integer, primary_key=True)
    link = db.Column(db.String(2048))
    body = db.Column(db.String(140))
    description = db.Column(db.String(140))
    folder_name = db.Column(db.String(255))
    folder_link = db.Column(db.String(1000))
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    def to_dict(self):
        data = {
            "id": self.id,
            "link": self.link,
            "body": self.body,
            "description": self.description,
            "folder_name": self.folder_name,
            "folder_link": self.folder_link,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "_links": {"self": url_for("api.get_pic_post", id=self.id)},
        }

        return data

    def __repr__(self):
        return "<PostPic {}>".format(self.body)


class ShareFolderRequest(db.Model):
    __tablename__ = "share_requests"

    requestor_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), primary_key=True, nullable=False
    )
    requestee_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), primary_key=True, nullable=False
    )
    shared_folder_path = db.Column(db.String(1000), primary_key=True, nullable=False)

    def __init__(self, requestor_id, requestee_id, shared_folder_path):
        self.requestor_id = requestor_id
        self.requestee_id = requestee_id
        self.shared_folder_path = shared_folder_path


class ShareFolder(db.Model):
    __tablename__ = "shares"

    sharer_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), primary_key=True, nullable=False
    )
    sharee_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), primary_key=True, nullable=False
    )
    sharer_folder_path = db.Column(db.String(255), primary_key=True, nullable=False)
    sharee_folder_path = db.Column(db.String(255), primary_key=True, nullable=False)

    def __init__(self, sharer_id, sharee_id, sharer_folder_path, sharee_folder_path):
        self.sharer_id = sharer_id
        self.sharee_id = sharee_id
        self.sharer_folder_path = sharer_folder_path
        self.sharee_folder_path = sharee_folder_path


class Leaf(db.Model):
    __tablename__ = "leaf"
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), primary_key=True, nullable=False
    )
    file_name = db.Column(db.String(75), primary_key=True, nullable=False)
    folder_path = db.Column(db.String(255), primary_key=True, nullable=False)
    md_text = db.Column(db.String(8000), nullable=False)
    post_id = db.Column(db.Integer, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["post_id"], ["post.id"], name="fk_leaf_post"),
    )

    def __init__(self, user_id, file_name, folder_path, md_text, post_id):
        self.user_id = user_id
        self.file_name = file_name
        self.folder_path = folder_path
        self.md_text = md_text
        self.post_id = post_id

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "file_name": self.file_name,
            "folder_path": self.folder_path,
            "md_text": self.md_text,
            "post_id": self.post_id,
        }


# ---------------------------------------------------------------------------
# Agent Identity & Reputation
# ---------------------------------------------------------------------------


class AgentProfile(db.Model):
    __tablename__ = "agent_profile"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    agent_type = db.Column(db.String(50), default="curator")
    description = db.Column(db.Text, nullable=True)
    source_url = db.Column(db.String(2048), nullable=True)
    api_key = db.Column(db.String(64), unique=True, index=True)
    api_key_created = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    rate_limit_rpm = db.Column(db.Integer, default=60)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("agent_profile", uselist=False))

    def generate_api_key(self):
        self.api_key = base64.urlsafe_b64encode(os.urandom(48)).decode("utf-8")[:64]
        self.api_key_created = datetime.utcnow()
        return self.api_key

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "username": self.user.username,
            "agent_type": self.agent_type,
            "description": self.description,
            "source_url": self.source_url,
            "is_active": self.is_active,
            "rate_limit_rpm": self.rate_limit_rpm,
            "trust_score": self.user.trust_score if self.user.trust_score is not None else 0.3,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


class AgentTrustEvent(db.Model):
    __tablename__ = "agent_trust_event"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    event_type = db.Column(db.String(50))
    delta = db.Column(db.Float)
    reason = db.Column(db.Text, nullable=True)
    related_post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=True)
    related_topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="trust_events")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "event_type": self.event_type,
            "delta": self.delta,
            "reason": self.reason,
            "related_post_id": self.related_post_id,
            "related_topic_id": self.related_topic_id,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Topic Taxonomy
# ---------------------------------------------------------------------------


class Topic(db.Model):
    __tablename__ = "topic"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), index=True)
    slug = db.Column(db.String(255), index=True)
    description = db.Column(db.Text, nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=True, index=True)

    __table_args__ = (
        db.UniqueConstraint("name", "parent_id", name="uq_topic_name_parent"),
    )
    depth = db.Column(db.Integer, default=0)
    path = db.Column(db.String(1000), index=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    url_count = db.Column(db.Integer, default=0)
    subscriber_count = db.Column(db.Integer, default=0)

    children = db.relationship(
        "Topic", backref=db.backref("parent", remote_side="Topic.id"), lazy=True
    )
    aliases = db.relationship("TopicAlias", backref="topic", lazy=True)
    url_scores = db.relationship("UrlTopicScore", backref="topic", lazy="dynamic")
    subscriptions = db.relationship("TopicSubscription", backref="topic", lazy="dynamic")

    def to_dict(self, include_children=False):
        data = {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "path": self.path,
            "is_active": self.is_active,
            "url_count": self.url_count,
            "subscriber_count": self.subscriber_count,
            "aliases": [a.alias_name for a in self.aliases],
        }
        if include_children:
            data["children"] = [c.to_dict(include_children=True) for c in self.children]
        return data

    def __repr__(self):
        return f"<Topic {self.name}>"


class TopicAlias(db.Model):
    __tablename__ = "topic_alias"
    id = db.Column(db.Integer, primary_key=True)
    alias_name = db.Column(db.String(255), index=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# URL Deduplication & Canonicalization
# ---------------------------------------------------------------------------


class CanonicalUrl(db.Model):
    __tablename__ = "canonical_url"
    id = db.Column(db.Integer, primary_key=True)
    canonical_url = db.Column(db.String(4096), unique=True, index=True)
    url_hash = db.Column(db.String(64), unique=True, index=True)
    domain = db.Column(db.String(255), index=True)
    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    submission_count = db.Column(db.Integer, default=0)
    global_score = db.Column(db.Float, default=0.0)

    posts = db.relationship("Post", backref="canonical", lazy="dynamic")
    topic_scores = db.relationship("UrlTopicScore", backref="canonical_url", lazy="dynamic")
    metadata_entries = db.relationship("UrlMetadata", backref="canonical_url", lazy="dynamic")
    propagations = db.relationship("UrlPropagation", backref="canonical_url", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "canonical_url": self.canonical_url,
            "url_hash": self.url_hash,
            "domain": self.domain,
            "first_seen": self.first_seen.isoformat() + "Z" if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() + "Z" if self.last_seen else None,
            "submission_count": self.submission_count,
            "global_score": self.global_score,
            "topic_scores": [
                s.to_dict() for s in self.topic_scores.order_by(UrlTopicScore.combined_score.desc()).limit(20).all()
            ],
        }


# ---------------------------------------------------------------------------
# Relevance Scoring
# ---------------------------------------------------------------------------


class UrlTopicScore(db.Model):
    __tablename__ = "url_topic_score"
    id = db.Column(db.Integer, primary_key=True)
    canonical_url_id = db.Column(db.Integer, db.ForeignKey("canonical_url.id"), index=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), index=True)
    relevance_score = db.Column(db.Float, default=0.0)
    quality_score = db.Column(db.Float, default=0.0)
    combined_score = db.Column(db.Float, default=0.0)
    vote_count = db.Column(db.Integer, default=0)
    first_tagged_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("canonical_url_id", "topic_id", name="uq_url_topic"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "canonical_url_id": self.canonical_url_id,
            "topic_id": self.topic_id,
            "topic_name": self.topic.name if self.topic else None,
            "relevance_score": self.relevance_score,
            "quality_score": self.quality_score,
            "combined_score": self.combined_score,
            "vote_count": self.vote_count,
            "first_tagged_at": self.first_tagged_at.isoformat() + "Z" if self.first_tagged_at else None,
        }


class PostTopicTag(db.Model):
    __tablename__ = "post_topic_tag"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), index=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), index=True)
    tagged_by = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    confidence = db.Column(db.Float, default=1.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("post_id", "topic_id", "tagged_by", name="uq_post_topic_tagger"),
    )

    topic = db.relationship("Topic", backref="post_tags")
    tagger = db.relationship("User", backref="topic_tags_made")

    def to_dict(self):
        return {
            "post_id": self.post_id,
            "topic_id": self.topic_id,
            "topic_name": self.topic.name if self.topic else None,
            "tagged_by": self.tagged_by,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Context Attachments (URL Metadata)
# ---------------------------------------------------------------------------


class UrlMetadata(db.Model):
    __tablename__ = "url_metadata"
    id = db.Column(db.Integer, primary_key=True)
    canonical_url_id = db.Column(db.Integer, db.ForeignKey("canonical_url.id"), index=True)
    submitted_by = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    summary = db.Column(db.Text, nullable=True)
    entities = db.Column(db.JSON, nullable=True)
    sentiment = db.Column(db.String(20), nullable=True)
    relevance_justification = db.Column(db.Text, nullable=True)
    source_credibility = db.Column(db.Float, nullable=True)
    language = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    submitter = db.relationship("User", backref="url_metadata_submitted")

    def to_dict(self):
        return {
            "id": self.id,
            "canonical_url_id": self.canonical_url_id,
            "submitted_by": self.submitted_by,
            "submitter_username": self.submitter.username if self.submitter else None,
            "summary": self.summary,
            "entities": self.entities if isinstance(self.entities, (list, dict)) else json.loads(self.entities) if self.entities else None,
            "sentiment": self.sentiment,
            "relevance_justification": self.relevance_justification,
            "source_credibility": self.source_credibility,
            "language": self.language,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Subscriptions & Webhooks
# ---------------------------------------------------------------------------


class TopicSubscription(db.Model):
    __tablename__ = "topic_subscription"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), index=True)
    min_score = db.Column(db.Float, default=0.5)
    webhook_url = db.Column(db.String(2048), nullable=True)
    webhook_secret = db.Column(db.String(128), nullable=True)
    delivery_method = db.Column(db.String(20), default="poll")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_delivered = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "topic_id", name="uq_user_topic_sub"),
    )

    user = db.relationship("User", backref="subscriptions")

    def generate_webhook_secret(self):
        self.webhook_secret = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
        return self.webhook_secret

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "topic_id": self.topic_id,
            "topic_name": self.topic.name if self.topic else None,
            "min_score": self.min_score,
            "webhook_url": self.webhook_url,
            "delivery_method": self.delivery_method,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "last_delivered": self.last_delivered.isoformat() + "Z" if self.last_delivered else None,
        }


class WebhookDelivery(db.Model):
    __tablename__ = "webhook_delivery"
    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("topic_subscription.id"), index=True
    )
    payload = db.Column(db.JSON)
    status_code = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text, nullable=True)
    delivered_at = db.Column(db.DateTime, default=datetime.utcnow)
    retry_count = db.Column(db.Integer, default=0)

    subscription = db.relationship("TopicSubscription", backref="deliveries")

    def to_dict(self):
        return {
            "id": self.id,
            "subscription_id": self.subscription_id,
            "status_code": self.status_code,
            "delivered_at": self.delivered_at.isoformat() + "Z" if self.delivered_at else None,
            "retry_count": self.retry_count,
        }


# ---------------------------------------------------------------------------
# Domain Credibility
# ---------------------------------------------------------------------------


class DomainCredibility(db.Model):
    __tablename__ = "domain_credibility"
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), index=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=True, index=True)
    credibility_score = db.Column(db.Float, default=0.5)
    submission_count = db.Column(db.Integer, default=0)
    avg_quality_score = db.Column(db.Float, default=0.0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("domain", "topic_id", name="uq_domain_topic"),
    )

    topic = db.relationship("Topic", backref="domain_scores")

    def to_dict(self):
        return {
            "id": self.id,
            "domain": self.domain,
            "topic_id": self.topic_id,
            "topic_name": self.topic.name if self.topic else "global",
            "credibility_score": self.credibility_score,
            "submission_count": self.submission_count,
            "avg_quality_score": self.avg_quality_score,
            "last_updated": self.last_updated.isoformat() + "Z" if self.last_updated else None,
        }


# ---------------------------------------------------------------------------
# Temporal Intelligence
# ---------------------------------------------------------------------------


class UrlPropagation(db.Model):
    __tablename__ = "url_propagation"
    id = db.Column(db.Integer, primary_key=True)
    canonical_url_id = db.Column(db.Integer, db.ForeignKey("canonical_url.id"), index=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), index=True)
    first_seen_in_topic = db.Column(db.DateTime, default=datetime.utcnow)
    submission_velocity = db.Column(db.Float, default=0.0)
    peak_velocity_at = db.Column(db.DateTime, nullable=True)
    first_submitted_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("canonical_url_id", "topic_id", name="uq_url_topic_prop"),
    )

    topic = db.relationship("Topic", backref="propagations")
    first_submitter = db.relationship("User", backref="first_discoveries")

    def to_dict(self):
        return {
            "canonical_url_id": self.canonical_url_id,
            "topic_id": self.topic_id,
            "topic_name": self.topic.name if self.topic else None,
            "first_seen_in_topic": self.first_seen_in_topic.isoformat() + "Z" if self.first_seen_in_topic else None,
            "submission_velocity": self.submission_velocity,
            "peak_velocity_at": self.peak_velocity_at.isoformat() + "Z" if self.peak_velocity_at else None,
            "first_submitted_by": self.first_submitted_by,
            "first_submitter_username": self.first_submitter.username if self.first_submitter else None,
        }


# ---------------------------------------------------------------------------
# Vector Embeddings for Semantic Search
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Agent Action Reporting & Query Sessions
# ---------------------------------------------------------------------------


class AgentAction(db.Model):
    __tablename__ = "agent_action"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    canonical_url_id = db.Column(db.Integer, db.ForeignKey("canonical_url.id"), nullable=True, index=True)
    action = db.Column(db.String(100))  # e.g. "extracted_table_data", "summarized", "cited", "shared", "ignored"
    result_summary = db.Column(db.Text, nullable=True)
    metadata_extra = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="actions")
    canonical = db.relationship("CanonicalUrl", backref="actions")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "canonical_url_id": self.canonical_url_id,
            "action": self.action,
            "result_summary": self.result_summary,
            "metadata": self.metadata_extra,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


class AgentQueryLog(db.Model):
    __tablename__ = "agent_query_log"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    endpoint = db.Column(db.String(255))
    query_text = db.Column(db.Text, nullable=True)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=True)
    canonical_url_id = db.Column(db.Integer, db.ForeignKey("canonical_url.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref="query_logs")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "endpoint": self.endpoint,
            "query_text": self.query_text,
            "topic_id": self.topic_id,
            "canonical_url_id": self.canonical_url_id,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }


class UrlEmbedding(db.Model):
    __tablename__ = "url_embedding"
    id = db.Column(db.Integer, primary_key=True)
    canonical_url_id = db.Column(
        db.Integer, db.ForeignKey("canonical_url.id"), unique=True, index=True
    )
    text_content = db.Column(db.Text)
    vector = db.Column(db.Text)  # JSON-serialized float array
    model = db.Column(db.String(50), default="text-embedding-3-small")
    dimensions = db.Column(db.Integer, default=1536)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    canonical = db.relationship("CanonicalUrl", backref=db.backref("embedding", uselist=False))

    def get_vector(self):
        if self.vector:
            return json.loads(self.vector)
        return None

    def set_vector(self, vec):
        self.vector = json.dumps(vec)
