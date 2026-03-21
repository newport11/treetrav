"""Create a demo account: username=demo, password=demo1111"""
from app import create_app, db
from app.models import User

app = create_app()

with app.app_context():
    if User.query.filter_by(username="demo").first():
        print("Demo user already exists.")
    else:
        user = User(username="demo", email="demo@treetrav.com")
        user.set_password("demo1111")
        db.session.add(user)
        db.session.commit()
        print(f"Demo user created (id={user.id}).")
