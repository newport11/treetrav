from flask_cors import CORS

from app import create_app, db
from app.models import Post, User

app = create_app()
CORS(app)

@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Post': Post}