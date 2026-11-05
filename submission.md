# Mixtape: Codebase Map

## 1. Main files

Mixtape is a JSON-only Flask API backed by SQLAlchemy and SQLite. It has no templates or frontend. Every endpoint returns `jsonify(...)`.

| File | What it does |
|------|--------------|
| `app.py` | App factory `create_app(config=None)`. Creates the global `db = SQLAlchemy()`, sets config (`DATABASE_URL` defaults to `sqlite:///mixtape.db`), registers the four blueprints under `/songs`, `/playlists`, `/users` and `/feed`, then runs `db.create_all()`. The blueprint imports sit inside the function so that `models.py` can import `db` from `app` without a circular import. |
| `models.py` | Every table. Seven ORM models (`User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`) plus three plain association tables (`friendships`, `song_tags`, `playlist_entries`). Primary keys are UUID strings. Each model has a `to_dict()` that the API uses for serialization. |
| `routes/songs.py` | Search, song detail, rate a song, record a listen. |
| `routes/playlists.py` | Create a playlist, get playlist metadata, list a playlist's songs, add a song to a playlist. |
| `routes/users.py` | User profile, streak, notifications, mark a notification read. `GET /users/<id>` is the one route that queries the DB directly with no service. |
| `routes/feed.py` | "Friends listening now" and the activity feed. |
| `services/search_service.py` | `search_songs` (case-insensitive `ilike` on title/artist, with an outer join to `song_tags`) and `get_song`. |
| `services/streak_service.py` | `record_listening_event` (stores a `ListeningEvent`, then updates the streak), `update_listening_streak` (the day-comparison logic) and `get_streak`. |
| `services/feed_service.py` | `get_friends_listening_now` (friends' events newer than `RECENT_THRESHOLD`, one per friend) and `get_activity_feed` (the latest N friend events, with no time filter). |
| `services/notification_service.py` | `create_notification`, `get_notifications` and `mark_as_read`. It also holds `add_to_playlist` and `rate_song`, because both are actions that are supposed to notify the song's sharer. |
| `services/playlist_service.py` | `create_playlist`, `get_playlist`, `get_playlist_songs` (joins `playlist_entries` and orders by `position`) and `get_user_playlists` (no route calls it). |
| `seed_data.py` | Drops and recreates the DB, then inserts 5 users, symmetric friendships, 10 tags, 13 songs with 0, 1 or 3 tags, listening events, 3 playlists and 1 sample notification. The data is chosen to expose the open issues, for example the multi-tag songs for search duplicates and the "song added" notification as a working example for Issue #4. |
| `tests/` | One pytest file each for streaks, search and playlists. Each file defines its own `app` fixture on in-memory SQLite and calls service functions directly, not HTTP routes. Feed and notifications have no tests. |
| `requirements.txt` | Flask 3, Flask-SQLAlchemy 3.1, SQLAlchemy 2, python-dotenv, pytest. |

### Data model at a glance

```
User ──< Song (shared_by)          User >──< User      via friendships (stored in both directions)
User ──< Rating >── Song           Song >──< Tag       via song_tags
User ──< ListeningEvent >── Song   Playlist >──< Song  via playlist_entries (+ position, added_by, added_at)
User ──< Playlist (created_by)     User ──< Notification
```

`User` also stores `listening_streak` and `last_listened_at` directly on the row. The streak is a cached counter; it is not recomputed from `ListeningEvent`.

### Endpoint → service index

| Method and path | Route function | Service call |
|---|---|---|
| `GET /songs/search?q=` | `songs.search` | `search_service.search_songs` |
| `GET /songs/<id>` | `songs.get_song_detail` | `search_service.get_song` |
| `POST /songs/<id>/rate` | `songs.rate` | `notification_service.rate_song` |
| `POST /songs/<id>/listen` | `songs.listen` | `streak_service.record_listening_event` |
| `POST /playlists/` | `playlists.create` | `playlist_service.create_playlist` |
| `GET /playlists/<id>` | `playlists.get_detail` | `playlist_service.get_playlist` |
| `GET /playlists/<id>/songs` | `playlists.get_songs` | `playlist_service.get_playlist_songs` |
| `POST /playlists/<id>/songs` | `playlists.add_song` | `notification_service.add_to_playlist` |
| `GET /users/<id>` | `users.get_user` | none (direct `db.session.get`) |
| `GET /users/<id>/streak` | `users.streak` | `streak_service.get_streak` |
| `GET /users/<id>/notifications` | `users.notifications` | `notification_service.get_notifications` |
| `POST /users/notifications/<nid>/read` | `users.read_notification` | `notification_service.mark_as_read` |
| `GET /feed/<id>/listening-now` | `feed.listening_now` | `feed_service.get_friends_listening_now` |
| `GET /feed/<id>/activity` | `feed.activity` | `feed_service.get_activity_feed` |

---

## 2. Data flow: a user rates a song

**Request:** `POST /songs/<song_id>/rate` with body `{"user_id": "...", "score": 4}`

1. **Routing (`app.py:35`).** `songs_bp` is mounted at `/songs`, so Flask sends the request to `rate(song_id)` in `routes/songs.py:29`.
2. **Route validation (`routes/songs.py:31-35`).** The route reads `user_id` and `score` from the JSON body. If either is missing, it returns `400`. It converts `score` with `int(score)`. A non-numeric score raises `ValueError`, which the route's `except` turns into a `400`.
3. **Service call (`routes/songs.py:37`).** The route calls `rate_song(user_id, song_id, score)`. This function is in `services/notification_service.py`; there is no ratings service.
4. **Service validation (`notification_service.py:85-94`).**
   - A score outside 1–5 raises `ValueError("Score must be between 1 and 5")`.
   - `db.session.get(Song, song_id)` and `db.session.get(User, user_id)` look up both records. If either is missing, the function raises `ValueError("... not found")`. The route reports this as `400`, not `404`.
5. **Upsert (`notification_service.py:97-106`).** The service looks for an existing `Rating` with the same `(user_id, song_id)`. The `unique_user_song_rating` constraint in `models.py:128` guarantees there is at most one.
   - If one exists, the service overwrites `score`. `rated_at` is not refreshed.
   - Otherwise it creates and adds a new `Rating`.
6. **Persist (`notification_service.py:108`).** `db.session.commit()` runs inside the service.
7. **Response (`routes/songs.py:38`).** The service returns the `Rating` model instance, and the route serializes it with `rating.to_dict()` and returns `201`.

**What does not happen:** `rate_song` never calls `create_notification`. Its sibling `add_to_playlist` (`notification_service.py:65-70`) does notify `song.shared_by` when the actor is not the sharer, and `create_notification`'s docstring lists `'song_rated'` as an expected type. Because the rating path skips this step, the sharer never hears about the rating. This is Issue #4.

```
client ─POST /songs/<id>/rate─▶ routes/songs.rate
                                   │ validate body, int(score)
                                   ▼
                       notification_service.rate_song
                                   │ check range → get Song → get User
                                   │ query existing Rating → update or insert
                                   │ commit
                                   │ ✗ (no create_notification)
                                   ▼
                     Rating ──to_dict()──▶ 201 JSON
```

---

## 3. Patterns in how the app is organized

**Three layers: thin routes, services, models.** Routes only parse input, check required fields, call one service function and `jsonify` the result. All business logic and queries live in `services/`. Serialization lives on the models (`to_dict`). When an endpoint misbehaves, the cause is almost always in the service it calls, which is where the README says the bugs live.

**Errors go through `ValueError`.** Every service signals "not found" or "invalid input" with `raise ValueError(msg)`. Every route wraps its call in `try/except ValueError`. GET routes map that to `404`, and POST routes map it to `400`, even when the real problem is a missing record. No custom exception types or Flask error handlers exist.

**The same lookup appears in almost every service:**
```python
x = db.session.get(Model, x_id)
if not x:
    raise ValueError(f"Model {x_id} not found")
```

**Services own their transactions.** Each write function calls `db.session.commit()` itself. `create_notification` also commits, so `add_to_playlist` makes two separate commits: one for the playlist entry and one for the notification. The two writes are not atomic.

**Return types are inconsistent.** Read services return plain dicts (`get_song`, `get_playlist_songs` and similar). Write services return model instances (`rate_song`, `create_playlist`, `record_listening_event`), and the route calls `.to_dict()`.

**Services are split by side effect, not by domain.** Rating and adding to a playlist live in `notification_service` because they notify someone, not in a song or playlist service. To find a feature, follow the route's import; the file names can be misleading.

**Two query styles are mixed.** Most services use the legacy `db.session.query(...)` API. `search_service` uses SQLAlchemy 2.0 `select()` after commit `dc87e8c`. The legacy `Query` deduplicates single-entity ORM results, which hid the join duplicates in Issue #3.

**Association tables are plain `db.Table`s, not association models.** `playlist_entries` has extra required columns (`position`, `added_by`), but `Playlist.songs` is a simple `secondary=` relationship. The seed data and tests avoid this by inserting rows directly with `playlist_entries.insert()`. The API path (`add_to_playlist` → `playlist.songs.append(song)`) cannot fill those columns. I ran it against an in-memory DB and it fails with `IntegrityError: NOT NULL constraint failed: playlist_entries.position`. `POST /playlists/<id>/songs` is broken regardless of the five listed issues.

**Time is "UTC everywhere", but SQLite drops tzinfo.** Models default to `datetime.now(timezone.utc)`, but values come back from SQLite as naive datetimes. `streak_service` patches this with `.replace(tzinfo=timezone.utc)`. `feed_service` compares a timezone-aware `cutoff` against the stored column. Any date logic needs to account for this.

**No authentication.** The acting user is whatever `user_id` or `added_by` the client sends in the body or URL.

**Small inefficiencies and loose ends:**
- The feed services call `db.session.get(User)` and `db.session.get(Song)` once per event, an N+1 pattern.
- `get_user_playlists` is imported in `routes/playlists.py`, but no route exposes it.
- The test files each copy the same `app` fixture instead of sharing one through `conftest.py`.

### Where each open issue lives

| # | Issue | Function | Suspect code |
|---|-------|----------|--------------|
| 1 | Streak keeps resetting | `update_listening_streak` | `streak_service.py:73`, the extra `and today.weekday() != 6` resets the streak on Sundays |
| 2 | "Listening now" shows yesterday | `get_friends_listening_now` | `feed_service.py:13`, `RECENT_THRESHOLD = timedelta(hours=24)` is a full day, not "now" |
| 3 | Duplicate search results | `search_songs` | `search_service.py:30`, the outer join to `song_tags` yields one row per tag and nothing deduplicates |
| 4 | No notification on rating | `rate_song` | `notification_service.py:73-110`, the `create_notification` call is missing |
| 5 | Last playlist song missing | `get_playlist_songs` | `playlist_service.py:65`, `songs[:-1]` drops the last element |

---

## 4. Root cause analysis

I chose Issues **#1, #3 and #5**. All three have a specific, repeatable trigger in the seed data, and each one has a unit test that already fails because of it.

**Setup used for every reproduction:**
- I seeded a separate SQLite file by setting `DATABASE_URL` to a scratch path and running `seed_data.seed()`, so the real `instance/mixtape.db` was not touched.
- I sent the requests through Flask's test client (`app.test_client()`), so each one went through the real route → service → DB path.
- I also ran `pytest tests/`, which reported **4 failed, 9 passed**. All 4 failures belong to these three issues.

### Issue #1: Listening streak resets on Sunday

**How I reproduced it:**
- **Data condition:** the bug only shows up when the "today" of a listen falls on a Sunday and the previous listen was the day before (Saturday). The seed data alone can't trigger it, because the seed timestamps are relative to the real current time. I needed to control the date.
- **Steps:**
  1. Took seeded user `kenji` and set `listening_streak = 12` and `last_listened_at` to Saturday 2024-06-15 22:00.
  2. Patched `datetime` inside `services/streak_service.py` so that `now()` returns Sunday 2024-06-16 09:00 UTC.
  3. Called `POST /songs/<song_id>/listen` with `{"user_id": kenji}`, then `GET /users/<kenji>/streak`.
  4. As a control, ran the same steps for Friday 22:00 → Saturday 09:00.
  5. Then ran one more listen on Monday 2024-06-17 to match the second part of the report.
- **Result:**

  | Last listen → this listen | Expected streak | Actual streak |
  |---|---|---|
  | Fri → Sat (control) | 12 → 13 | 12 → 13 ✅ |
  | Sat → Sun | 12 → 13 | **12 → 1** ❌ |
  | Sun → Mon | 1 → 2 | 1 → 2 (counting resumes, as kenji described) |

- **Test confirmation:** `tests/test_streaks.py::test_streak_increments_on_sunday` fails with `assert 1 == 2`.

**Root cause:** in `services/streak_service.py:73`, the "listened yesterday" branch is `elif days_since_last == 1 and today.weekday() != 6:`. `weekday() == 6` is Sunday. On a Sunday the condition is false even after a Saturday listen, so the code falls through to the `else` branch and sets `listening_streak = 1`.

**Fix:** _Pending._

### Issue #3: The same song appears multiple times in search

**How I reproduced it:**
- **Data condition:** the number of copies depends on how many tags a song has. A song with 0 or 1 tags appears once, and a song with N ≥ 2 tags appears N times. The seed data has songs with 0, 1 and 3 tags, so the bug shows up as soon as the DB is seeded. That is also why the report calls the duplicates "inconsistent".
- **Steps:** called `GET /songs/search?q=<term>` with terms that match songs with different tag counts.
- **Result:**

  | Query | Matching song (tag count) | Times returned |
  |---|---|---|
  | `Anthem` | Crown Heights Anthem (3) | **3** ❌ |
  | `After Hours` | After Hours (3) | **3** ❌ |
  | `Block Party` | Block Party (1) | 1 ✅ |
  | `Elara` | Still Waters (0), Soft Landing (1) | 1 each ✅ |

- **Test confirmation:** `tests/test_search.py::test_search_no_duplicates_multi_tag_song` fails. The 0-tag and 1-tag tests pass, which matches the pattern above.

**Root cause:** `services/search_service.py:30` runs `select(Song).outerjoin(song_tags, ...)`. The join produces one row for each (song, tag) pair, and nothing removes the duplicates afterward. Nothing in the query uses the tags either: `Song.to_dict()` loads them separately through the `tags` relationship.

**Fix:** _Pending._

### Issue #5: The last song in a playlist never shows up

**How I reproduced it:**
- **Data condition:** any playlist that has at least one song. The seeded playlist "Friday Energy" has 7 rows in `playlist_entries`, at positions 1–7.
- **Steps:**
  1. Queried `playlist_entries` directly for "Friday Energy". It has 7 rows, and position 7 is "Harlem Renaissance".
  2. Called `GET /playlists/<friday_energy_id>/songs`.
  3. Added an 8th song ("Frequencies", added by simone) and fetched the playlist again.
- **Result:**
  - Before the add, the API returned `count: 6`, ending at "Crown Heights Anthem". "Harlem Renaissance" (position 7) was missing.
  - After the add, the API returned `count: 7`. "Harlem Renaissance" now appeared, and the new song "Frequencies" (position 8) was hidden. This is the "adding a song frees the previous one" behavior from the report.
- **Note on step 3:** adding the song through the API (`POST /playlists/<id>/songs`) returns **500** with `IntegrityError: NOT NULL constraint failed: playlist_entries.position`. `add_to_playlist` uses `playlist.songs.append(song)`, which never sets `position` or `added_by`. That is a separate bug (see section 3). To finish the reproduction, I inserted the 8th entry directly with `playlist_entries.insert()`, the same way `seed_data.py` does.
- **Test confirmation:** `tests/test_playlists.py::test_playlist_returns_all_songs` (expects 5, gets 4) and `test_playlist_returns_songs_in_order` both fail.

**Root cause:** `services/playlist_service.py:65` returns `[song.to_dict() for song in songs[:-1]]`. The query is correct and already sorted by `position` ascending, but the `[:-1]` slice always drops the last item, which is the most recently added song.

**Fix:** _Pending._
