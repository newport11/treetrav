import os

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


class Config(object):
    SECRET_KEY = os.environ.get("SECRET_KEY") or "you-will-never-guess"

    # Database — set DATABASE_URL in .env for Postgres:
    #   DATABASE_URL=postgresql://user:pass@localhost:5432/treetrav
    # Leave unset to use SQLite for local dev.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL"
    ) or "sqlite:///" + os.path.join(basedir, "app.db")

    # Fix Heroku/Render postgres:// vs postgresql:// issue
    if SQLALCHEMY_DATABASE_URI and SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql://", 1
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_PRE_PING = True

    # Connection pool settings — tuned for Postgres at scale
    # SQLite ignores these (single connection)
    SQLALCHEMY_POOL_RECYCLE = 300
    SQLALCHEMY_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
    SQLALCHEMY_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    MAIL_SERVER = os.environ.get("MAIL_SERVER")
    MAIL_PORT = int(os.environ.get("MAIL_PORT") or 25)
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS") is not None
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")
    PROD_DOMAIN = os.environ.get("PROD_DOMAIN", "https://treetrav.com")
    LOCAL_DOMAIN = os.environ.get("LOCAL_DOMAIN", "http://127.0.0.1:5000")
    IS_PROD = os.environ.get("IS_PROD")
    ADMINS = ["treetrav.info@gmail.com"]
    ELASTICSEARCH_URL = os.environ.get("ELASTICSEARCH_URL")
    USE_OPENAI_EMBEDDING = os.environ.get("USE_OPENAI_EMBEDDING", "False").lower() == "true"

    # Redis — used for caching and Celery broker when available
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    POSTS_PER_PAGE = 25
    USERS_PER_PAGE = 20
