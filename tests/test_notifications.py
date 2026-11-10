"""
tests/test_notifications.py — Mixtape

Regression tests for rating notifications (Issue #4).
"""

import pytest
from app import create_app, db
from models import User, Song, Notification
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def song_and_users(app):
    """aaliya shared a song; kenji is a friend who will rate it."""
    with app.app_context():
        aaliya = User(username="aaliya", email="aaliya@example.com")
        kenji = User(username="kenji", email="kenji@example.com")
        db.session.add_all([aaliya, kenji])
        db.session.flush()
        song = Song(title="Golden Hour", artist="Solange K", shared_by=aaliya.id)
        db.session.add(song)
        db.session.commit()
        yield {"sharer": aaliya.id, "rater": kenji.id, "song": song.id}


def test_rating_notifies_the_sharer(app, song_and_users):
    """When someone else rates a song, the person who shared it is notified."""
    with app.app_context():
        ids = song_and_users
        rate_song(ids["rater"], ids["song"], 5)

        notifications = get_notifications(ids["sharer"])
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"
        assert "kenji" in notifications[0]["body"]
        assert "Golden Hour" in notifications[0]["body"]


def test_rating_your_own_song_does_not_notify(app, song_and_users):
    """Same rule as playlist adds: no notification when the sharer is the actor."""
    with app.app_context():
        ids = song_and_users
        rate_song(ids["sharer"], ids["song"], 4)

        assert get_notifications(ids["sharer"]) == []


def test_invalid_rating_does_not_notify(app, song_and_users):
    """A rejected score must not leave a notification behind."""
    with app.app_context():
        ids = song_and_users
        with pytest.raises(ValueError):
            rate_song(ids["rater"], ids["song"], 9)

        assert db.session.query(Notification).count() == 0
