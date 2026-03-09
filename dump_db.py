from app import app
from models import User
import json

with app.app_context():
    users = []
    for u in User.query.all():
        users.append({
            'platform': u.platform,
            'url': u.profile_url,
        })
    with open('users.json', 'w') as f:
        json.dump(users, f, indent=2)
