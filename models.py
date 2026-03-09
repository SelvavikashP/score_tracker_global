from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    platform = db.Column(db.String(50), nullable=False)
    profile_url = db.Column(db.String(255), nullable=False)
    rating = db.Column(db.Integer, default=0)
    rank = db.Column(db.String(50), default="Unrated")  # Display rank (e.g. Master)
    global_rank = db.Column(db.Integer, default=0)
    country_rank = db.Column(db.Integer, default=0)
    recent_problems = db.Column(db.Integer, default=0)
    total_contests = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "profile_url": self.profile_url,
            "rating": self.rating,
            "rank": self.rank,
            "global_rank": self.global_rank,
            "country_rank": self.country_rank,
            "recent_problems": self.recent_problems,
            "total_contests": self.total_contests,
            "last_updated": self.last_updated.strftime("%Y-%m-%d %H:%M:%S")
        }

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    users = db.relationship('User', backref='account', lazy=True)
