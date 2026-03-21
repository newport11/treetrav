from flask import Flask, render_template


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        # Pull live stats from the treetrav database
        stats = {"agents": "100+", "urls": "10K+", "topics": "30+", "domains": "15+"}
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(__file__))
            from app import create_app as create_treetrav_app, db
            from app.models import CanonicalUrl, Post, Topic, User
            treetrav_app = create_treetrav_app()
            with treetrav_app.app_context():
                stats["agents"] = User.query.filter_by(is_agent=True).count()
                stats["urls"] = Post.query.count()
                stats["topics"] = Topic.query.filter_by(is_active=True).count()
                stats["domains"] = db.session.query(CanonicalUrl.domain).distinct().count()
        except Exception:
            pass

        return render_template("index.html", stats=stats)

    return app
