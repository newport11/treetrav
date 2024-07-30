import os

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


class Config(object):
    SECRET_KEY = os.environ.get("SECRET_KEY") or "you-will-never-guess"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL"
    ) or "sqlite:///" + os.path.join(basedir, "app.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
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
    POSTS_PER_PAGE = 25
    PIC_POSTS_PER_PAGE = 15
