"""Backfill topics from folder paths of existing posts."""
from app import create_app, db
from app.api.posts import _auto_tag_from_folder
from app.models import Post

app = create_app()

with app.app_context():
    posts = Post.query.filter(Post.folder_link != "/", Post.folder_link.isnot(None)).all()
    print(f"Posts with folders: {len(posts)}")

    count = 0
    for post in posts:
        _auto_tag_from_folder(post)
        count += 1
        if count % 100 == 0:
            print(f"  {count}/{len(posts)} processed...")

    db.session.commit()

    from app.models import Topic, PostTopicTag
    print(f"Done. Topics: {Topic.query.count()}, Tags: {PostTopicTag.query.count()}")
