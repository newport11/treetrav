import logging
import os
import urllib.parse
from logging.handlers import RotatingFileHandler, SMTPHandler

from elasticsearch import Elasticsearch
from flask import Flask, current_app
from flask_babel import Babel
from flask_babel import lazy_gettext as _l
from flask_bootstrap import Bootstrap
from flask_caching import Cache
from flask_htmx import HTMX
from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_moment import Moment
from flask_pagedown import PageDown
from flask_sqlalchemy import SQLAlchemy
from itertools import zip_longest

from app.constants import PROFILE_PICS_PATH
from config import Config

db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = "auth.login"
login.login_message = _l("Please log in to access this page.")
mail = Mail()
bootstrap = Bootstrap()
moment = Moment()
babel = Babel()
htmx = HTMX()
pagedown = PageDown()
cache = Cache()


def decode_url(url):
    if url is None:
        return ""
    try:
        return urllib.parse.unquote(url)
    except:
        return str(url)  # Convert to string if unquote fails


def shorten_folder_path(path):
    shortened_path = path[-30:]
    shortened_path_list = shortened_path.split("/", 1)
    if len(shortened_path_list) > 1:
        return shortened_path_list[1]
    else:
        return shortened_path


def set_mini_profile_pic_filename(filename):
    try:
        fn = filename.rstrip(".jpg")
    except:
        fn = ""
    return fn


def pic_exists(filename):
    file = os.path.join(PROFILE_PICS_PATH, f"{filename}")
    current_app.logger.info(file)
    if os.path.exists(file):
        return True
    return False

def chunked(iterable, n):
    "Collect data into fixed-length chunks or blocks"
    args = [iter(iterable)] * n
    return zip_longest(*args)
    

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config["SQLALCHEMY_POOL_PRE_PING"] = True
    app.config["SQLALCHEMY_POOL_RECYCLE"] = 20

    # Redis Cache Configuration
    USE_REDIS = os.getenv("USE_REDIS", "False").lower() == "true"

    if USE_REDIS:
        app.config["CACHE_TYPE"] = "redis"
        app.config["CACHE_REDIS_URL"] = "redis://localhost:6379/0"
        app.config["CACHE_DEFAULT_TIMEOUT"] = 60
    else:
        app.config["CACHE_TYPE"] = "simple"

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)
    mail.init_app(app)
    bootstrap.init_app(app)
    moment.init_app(app)
    htmx.init_app(app)
    babel.init_app(app)
    pagedown.init_app(app)
    cache.init_app(app)

    app.elasticsearch = (
        Elasticsearch([app.config["ELASTICSEARCH_URL"]], verify_certs=False)
        if app.config["ELASTICSEARCH_URL"]
        else None
    )

    from app.errors import bp as errors_bp

    app.register_blueprint(errors_bp)

    from app.auth import bp as auth_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")

    from app.main import bp as main_bp

    app.register_blueprint(main_bp)

    from app.api import bp as api_bp

    app.register_blueprint(api_bp, url_prefix="/api")

    app.jinja_env.globals.update(shorten_folder_path=shorten_folder_path)
    app.jinja_env.globals.update(pic_exists=pic_exists)
    app.jinja_env.globals.update(
        set_mini_profile_pic_filename=set_mini_profile_pic_filename
    )
    app.jinja_env.globals.update(decode_url=decode_url)

    app.jinja_env.filters['chunked'] = chunked

    if not app.debug and not app.testing:
        if app.config["MAIL_SERVER"]:
            auth = None
            if app.config["MAIL_USERNAME"] or app.config["MAIL_PASSWORD"]:
                auth = (app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"])
            secure = None
            if app.config["MAIL_USE_TLS"]:
                secure = ()
            mail_handler = SMTPHandler(
                mailhost=(app.config["MAIL_SERVER"], app.config["MAIL_PORT"]),
                fromaddr="no-reply@" + app.config["MAIL_SERVER"],
                toaddrs=app.config["ADMINS"],
                subject="Website Failure",
                credentials=auth,
                secure=secure,
            )
            mail_handler.setLevel(logging.ERROR)
            app.logger.addHandler(mail_handler)

        if not os.path.exists("logs"):
            os.mkdir("logs")
        file_handler = RotatingFileHandler(
            "logs/treetrav.log", maxBytes=10240, backupCount=10
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s: %(message)s " "[in %(pathname)s:%(lineno)d]"
            )
        )
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)

        app.logger.setLevel(logging.INFO)
        app.logger.info("Treetrav startup")

    return app
