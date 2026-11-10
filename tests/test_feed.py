"""
tests/test_feed.py — Mixtape

Regression tests for the "Friends Listening Now" feed (Issue #2).
"""

import pytest
from datetime import datetime, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
import services.feed_service as feed_service
from services.feed_service import get_friends_listening_now


# Tuesday 2024-06-11, 9:00am UTC — "this morning" in the bug report.
NOW = datetime(2024, 6, 11, 9, 0, 0, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    """datetime whose now() is fixed, so the tests don't depend on the clock."""

    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz else cls.current.replace(tzinfo=None)


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def frozen_now(monkeypatch):
    FrozenDatetime.current = NOW
    monkeypatch.setattr(feed_service, "datetime", FrozenDatetime)
    return FrozenDatetime


@pytest.fixture
def people(app):
    """nova is friends with darius and simone. One shared song to listen to."""
    with app.app_context():
        nova = User(username="nova", email="nova@example.com")
        darius = User(username="darius", email="darius@example.com")
        simone = User(username="simone", email="simone@example.com")
        db.session.add_all([nova, darius, simone])
        db.session.flush()
        for friend in (darius, simone):
            db.session.execute(friendships.insert().values(user_id=nova.id, friend_id=friend.id))
            db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=nova.id))
        song = Song(title="Track", artist="Artist", shared_by=nova.id)
        db.session.add(song)
        db.session.commit()
        yield {"nova": nova.id, "darius": darius.id, "simone": simone.id, "song": song.id}


def listen(ids, who, when):
    db.session.add(ListeningEvent(user_id=ids[who], song_id=ids["song"], listened_at=when))
    db.session.commit()


def friend_names(user_id):
    return [entry["friend"]["username"] for entry in get_friends_listening_now(user_id)]


def test_listen_from_last_night_is_not_listening_now(app, people, frozen_now):
    """darius listened at 11pm yesterday; at 9am today he must not appear."""
    with app.app_context():
        listen(people, "darius", datetime(2024, 6, 10, 23, 0, tzinfo=timezone.utc))
        listen(people, "simone", datetime(2024, 6, 11, 8, 30, tzinfo=timezone.utc))

        assert friend_names(people["nova"]) == ["simone"]


def test_listen_from_earlier_today_still_shows_late_in_the_day(app, people, frozen_now):
    """A listen this morning still counts at 10:59pm the same day."""
    with app.app_context():
        frozen_now.current = datetime(2024, 6, 11, 22, 59, tzinfo=timezone.utc)
        listen(people, "simone", datetime(2024, 6, 11, 8, 30, tzinfo=timezone.utc))

        assert friend_names(people["nova"]) == ["simone"]


def test_midnight_is_the_boundary(app, people, frozen_now):
    """00:00:00 today counts; 23:59:59 yesterday does not."""
    with app.app_context():
        frozen_now.current = datetime(2024, 6, 11, 0, 0, 30, tzinfo=timezone.utc)
        listen(people, "darius", datetime(2024, 6, 10, 23, 59, 59, tzinfo=timezone.utc))
        listen(people, "simone", datetime(2024, 6, 11, 0, 0, 0, tzinfo=timezone.utc))

        assert friend_names(people["nova"]) == ["simone"]


def test_only_latest_song_per_friend(app, people, frozen_now):
    """Two listens today by the same friend produce one entry, the newer one."""
    with app.app_context():
        listen(people, "simone", datetime(2024, 6, 11, 7, 0, tzinfo=timezone.utc))
        listen(people, "simone", datetime(2024, 6, 11, 8, 45, tzinfo=timezone.utc))

        feed = get_friends_listening_now(people["nova"])
        assert len(feed) == 1
        assert feed[0]["listened_at"].startswith("2024-06-11T08:45")
