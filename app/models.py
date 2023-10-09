import base64
from datetime import datetime, timedelta
import os
from hashlib import md5
from time import time
from flask import current_app, url_for
from flask_login import UserMixin
from sqlalchemy import PrimaryKeyConstraint
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from app import db, login
from app.search import add_to_index, remove_from_index, query_index


class SearchableMixin(object):
    @classmethod
    def search(cls, expression, page, per_page):
        ids, total = query_index(cls.__tablename__, expression, page, per_page)
        if total == 0:
            return cls.query.filter_by(id=0), 0
        when = {}
        for i in range(len(ids)):
            when[ids[i]] = i
        return cls.query.filter(cls.id.in_(ids)).order_by(
            db.case(when, value=cls.id)), total
  

    @classmethod
    def before_commit(cls, session):
        session._changes = {
            'add': list(session.new),
            'update': list(session.dirty),
            'delete': list(session.deleted)
        }

    @classmethod
    def after_commit(cls, session):
        for obj in session._changes['add']:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes['update']:
            if isinstance(obj, SearchableMixin):
                add_to_index(obj.__tablename__, obj)
        for obj in session._changes['delete']:
            if isinstance(obj, SearchableMixin):
                remove_from_index(obj.__tablename__, obj)
        session._changes = None

    @classmethod
    def reindex(cls):
        for obj in cls.query:
            add_to_index(cls.__tablename__, obj)


db.event.listen(db.session, 'before_commit', SearchableMixin.before_commit)
db.event.listen(db.session, 'after_commit', SearchableMixin.after_commit)


followers = db.Table(
    'followers',
    db.Column('follower_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('followed_id', db.Integer, db.ForeignKey('user.id'))
)

follower_requests = db.Table(
    'follower_requests',
    db.Column('requestor_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('requestee_id', db.Integer, db.ForeignKey('user.id'))
)

user_favorites = db.Table(
    'user_favorites',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('post_id', db.Integer, db.ForeignKey('post.id'))
)

class PaginatedAPIMixin(object):
    @staticmethod
    def to_collection_dict(query, page, per_page, endpoint, **kwargs):
        resources = query.paginate(page=page, per_page=per_page,
                                   error_out=False)
        data = {
            'items': [item.to_dict() for item in resources.items],
            '_meta': {
                'page': page,
                'per_page': per_page,
                'total_pages': resources.pages,
                'total_items': resources.total
            },
            '_links': {
                'self': url_for(endpoint, page=page, per_page=per_page,
                                **kwargs),
                'next': url_for(endpoint, page=page + 1, per_page=per_page,
                                **kwargs) if resources.has_next else None,
                'prev': url_for(endpoint, page=page - 1, per_page=per_page,
                                **kwargs) if resources.has_prev else None
            }
        }
        return data
    
class User(SearchableMixin, UserMixin, PaginatedAPIMixin, db.Model):
    __searchable__ = ['username']
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    email = db.Column(db.String(120), index=True, unique=True)
    password_hash = db.Column(db.String(128))
    posts = db.relationship('Post', backref='author', lazy='dynamic')
    about_me = db.Column(db.String(140))
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    verified = db.Column(db.Boolean, default=False)
    private_mode = db.Column(db.Boolean, default=False)
    dark_mode = db.Column(db.Boolean, default=False)
    token = db.Column(db.String(32), index=True, unique=True)
    token_expiration = db.Column(db.DateTime)
    followed = db.relationship(
        'User', secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref('followers', lazy='dynamic'), lazy='dynamic')
    follow_requests = db.relationship(
        'User', secondary=follower_requests,
        primaryjoin=(follower_requests.c.requestor_id == id),
        secondaryjoin=(follower_requests.c.requestee_id == id),
        backref=db.backref('follower_requests', lazy='dynamic'), lazy='dynamic')
    favorites = db.relationship(
        'Post', secondary='user_favorites',
        primaryjoin=(user_favorites.c.user_id == id),
        backref=db.backref('user_favorites', lazy='dynamic'), lazy='dynamic')
    share_requests_made = db.relationship(
        'ShareFolderRequest',
        primaryjoin=(
            "User.id == ShareFolderRequest.requestor_id"
        ),
        backref='requestor',
        lazy='dynamic'
    )
    share_requests_received = db.relationship(
        'ShareFolderRequest',
        primaryjoin=(
            "User.id == ShareFolderRequest.requestee_id"
        ),
        backref='requestee',
        lazy='dynamic'
    )
    outbound_shares = db.relationship(
        'ShareFolder',
        primaryjoin=(
            "User.id == ShareFolder.sharer_id"
        ),
        backref='sharer',
        lazy='dynamic'
    )
    inbound_shares = db.relationship(
        'ShareFolder',
        primaryjoin=(
            "User.id == ShareFolder.sharee_id"
        ),
        backref='sharee',
        lazy='dynamic'
    )
    leafs = db.relationship(
        'Leaf',
        primaryjoin=(
            "User.id == Leaf.user_id"
        ),
        backref='leaf',
        lazy='dynamic'
    )

    def to_dict(self, include_email=False):
        data = {
            'id': self.id,
            'username': self.username,
            'last_seen': self.last_seen.isoformat() + 'Z',
            'about_me': self.about_me,
            'post_count': self.posts.count(),
            'follower_count': self.followers.count(),
            'followed_count': self.followed.count(),
            'verified': self.verified,
            '_links': {
                'self': url_for('api.get_user', id=self.id),
                'followers': url_for('api.get_followers', id=self.id),
                'followed': url_for('api.get_followed', id=self.id),
                'avatar': self.avatar(128)
            }
        }
        if include_email:
            data['email'] = self.email
        return data

    def from_dict(self, data, new_user=False):
        for field in ['username', 'email', 'about_me']:
            if field in data:
                setattr(self, field, data[field])
        if new_user and 'password' in data:
            self.set_password(data['password'])

    def update_verification(self, verified):
        setattr(self, 'verified', verified)
 
    def get_token(self, expires_in=3600):
        now = datetime.utcnow()
        if self.token and self.token_expiration > now + timedelta(seconds=60):
            return self.token
        self.token = base64.b64encode(os.urandom(24)).decode('utf-8')
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
    
    def __repr__(self):
        return '<User {}>'.format(self.username)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def avatar(self, size):
        digest = md5(self.email.lower().encode('utf-8')).hexdigest()
        return 'https://www.gravatar.com/avatar/{}?d=identicon&s={}'.format(
            digest, size)

    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)
    
    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)

    def is_following(self, user):
        return self.followed.filter(
            followers.c.followed_id == user.id).count() > 0

    def request_follow(self, user):
        if not self.is_requested(user):
            self.follow_requests.append(user)

    def unrequest_follow(self, user):
        if  self.is_requested(user):
            self.follow_requests.remove(user)       

    def is_requested(self, user):
        return self.follow_requests.filter(
            follower_requests.c.requestee_id == user.id).count() > 0
    
    def get_follow_requestors(self):
        requests = User.query.join(
            follower_requests, (follower_requests.c.requestor_id == User.id)).filter(
                follower_requests.c.requestee_id == self.id)
        return requests.order_by(User.id.desc())

    # Share request function for pushing shared folders
    def is_share_requested(self, user, folder_path):
        existing_request = ShareFolderRequest.query.filter_by(
            requestor_id=self.id, requestee_id=user.id, shared_folder_path=folder_path).first()
        return existing_request
    
    # check if share already exists
    def is_share(self, sharer_id, sharer_folder_path, sharee_folder_path ):
        existing_share = ShareFolder.query.filter_by(
            sharer_id=sharer_id, sharee_id=self.id, sharer_folder_path=sharer_folder_path, sharee_folder_path=sharee_folder_path).first()
        return existing_share
    
    def followed_posts(self):
        followed = Post.query.join(
            followers, (followers.c.followed_id == Post.user_id)).filter(
                followers.c.follower_id == self.id)
        own = Post.query.filter_by(user_id=self.id)
        return followed.union(own).order_by(Post.timestamp.desc())

    def favorite(self, post):
        if not self.is_favorite(post):
            self.favorites.append(post)

    def unfavorite(self, post):
        if self.is_favorite(post):
            self.favorites.remove(post)

    def is_favorite(self, post):
        return self.favorites.filter(
            user_favorites.c.post_id == post.id).count() > 0
    
    def favorite_posts(self):
        favorites = Post.query.join(
            user_favorites, (user_favorites.c.post_id == Post.id)).filter(
                user_favorites.c.user_id == self.id)
        return favorites.order_by(Post.timestamp.desc())

    def get_reset_password_token(self, expires_in=600):
        return jwt.encode(
            {'reset_password': self.id, 'exp': time() + expires_in},
            current_app.config['SECRET_KEY'], algorithm='HS256')

    @staticmethod
    def verify_reset_password_token(token):
        try:
            id = jwt.decode(token, current_app.config['SECRET_KEY'],
                            algorithms=['HS256'])['reset_password']
        except:
            return
        return User.query.get(id)


@login.user_loader
def load_user(id):
    return User.query.get(int(id))


class Post(db.Model):
    #__searchable__ = ['body']
    id = db.Column(db.Integer, primary_key=True)
    link = db.Column(db.String(2048))
    body = db.Column(db.String(140))
    folder_name = db.Column(db.String(255))
    folder_link = db.Column(db.String(1000))
    favicon_file_name = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    def to_dict(self):
        data = {
            'id': self.id,
            'link': self.link,
            'body': self.body,
            'folder_name': self.folder_name,
            'folder_link': self.folder_link,
            'favicon_file_name': self.favicon_file_name,
            'timestamp': self.timestamp,
            'user_id': self.user_id,
            '_links': {
                'self': url_for('api.get_post', id=self.id)
            }
        }
       
        return data

    def __repr__(self):
        return '<Post {}>'.format(self.body)


class ShareFolderRequest(db.Model):
    __tablename__ = 'share_requests'

    requestor_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True, nullable=False)
    requestee_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True, nullable=False)
    shared_folder_path = db.Column(db.String(1000), primary_key=True, nullable=False)

    def __init__(self, requestor_id, requestee_id, shared_folder_path):
        self.requestor_id = requestor_id
        self.requestee_id = requestee_id
        self.shared_folder_path = shared_folder_path

class ShareFolder(db.Model):
    __tablename__ = 'shares'

    sharer_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True, nullable=False)
    sharee_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True, nullable=False)
    sharer_folder_path = db.Column(db.String(255), primary_key=True, nullable=False)
    sharee_folder_path = db.Column(db.String(255), primary_key=True, nullable=False)


    def __init__(self, sharer_id, sharee_id, sharer_folder_path, sharee_folder_path):
        self.sharer_id = sharer_id
        self.sharee_id = sharee_id
        self.sharer_folder_path = sharer_folder_path
        self.sharee_folder_path = sharee_folder_path


class Leaf(db.Model):
    __tablename__ = 'leaf'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True, nullable=False)
    file_name = db.Column(db.String(75), primary_key=True, nullable=False)
    folder_path = db.Column(db.String(255), primary_key=True, nullable=False)
    md_text = db.Column(db.String(8000), nullable=False)


    def __init__(self, user_id, file_name, folder_path, md_text):
        self.user_id = user_id
        self.file_name = file_name
        self.folder_path = folder_path
        self.md_text = md_text