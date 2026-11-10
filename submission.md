# Mixtape Bug Hunt: Submission

## AI usage

<!--
To fill in. The rubric asks for:
- At least 2 specific uses of AI during codebase navigation or debugging: what was asked, and what it helped explain or trace.
- At least one time you verified something the AI explained, or where its output was incomplete and you had to course-correct.
- Enough detail to tell real collaboration apart from "I used AI to help with code."
-->

_To be written._

---

# Codebase Map

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
| `services/search_service.py` | `search_songs` (case-insensitive `ilike` on title or artist, built with SQLAlchemy 2.0 `select()`) and `get_song`. |
| `services/streak_service.py` | `record_listening_event` (stores a `ListeningEvent`, then updates the streak), `update_listening_streak` (the day-comparison logic) and `get_streak`. |
| `services/feed_service.py` | `get_friends_listening_now` (friends' events newer than `RECENT_THRESHOLD`, one per friend) and `get_activity_feed` (the latest N friend events, with no time filter). |
| `services/notification_service.py` | `create_notification`, `get_notifications` and `mark_as_read`. It also holds `add_to_playlist` and `rate_song`, because both are actions that are supposed to notify the song's sharer. |
| `services/playlist_service.py` | `create_playlist`, `get_playlist`, `get_playlist_songs` (joins `playlist_entries` and orders by `position`) and `get_user_playlists` (no route calls it). |
| `seed_data.py` | Drops and recreates the DB, then inserts 5 users, symmetric friendships, 10 tags, 13 songs with 0, 1 or 3 tags, listening events, 3 playlists and 1 sample "song added to playlist" notification. Listening events are spread from a few minutes to several days before the seed time, and playlist entries are inserted with explicit `position` values. |
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

**Related flow for comparison:** the other action in the same file, `add_to_playlist` (`notification_service.py:35-70`), follows the same validate → write → commit shape, then calls `create_notification(user_id=song.shared_by, ...)` when the actor is not the song's sharer. `create_notification` adds a `Notification` row and commits it on its own. Its docstring lists `'song_added_to_playlist'` and `'song_rated'` as the notification types.

```
client ─POST /songs/<id>/rate─▶ routes/songs.rate
                                   │ validate body, int(score)
                                   ▼
                       notification_service.rate_song
                                   │ check range → get Song → get User
                                   │ query existing Rating → update or insert
                                   │ commit
                                   ▼
                     Rating ──to_dict()──▶ 201 JSON
```

---

## 3. Patterns in how the app is organized

**Three layers: thin routes, services, models.** Routes only parse input, check required fields, call one service function and `jsonify` the result. All business logic and queries live in `services/`. Serialization lives on the models (`to_dict`). To understand what an endpoint does, read the service it calls.

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

**Two query styles are mixed.** Most services use the legacy `db.session.query(...)` API. `search_service` uses SQLAlchemy 2.0 `select()` (introduced in commit `dc87e8c`). The two styles behave differently with joins: the legacy `Query` collapses repeated rows for a single ORM entity, while `select(...).scalars().all()` returns every row.

**Association tables are plain `db.Table`s, not association models.** `playlist_entries` has extra required columns (`position`, `added_by`), but `Playlist.songs` is a simple `secondary=` relationship. The seed data and tests insert entries directly with `playlist_entries.insert()` so they can set those columns explicitly. `add_to_playlist` goes through the relationship instead (`playlist.songs.append(song)`).

**Time is "UTC everywhere", but SQLite drops tzinfo.** Models default to `datetime.now(timezone.utc)`, but values come back from SQLite as naive datetimes. `streak_service` patches this with `.replace(tzinfo=timezone.utc)`. `feed_service` compares a timezone-aware `cutoff` against the stored column. Any date logic needs to account for this.

**No authentication.** The acting user is whatever `user_id` or `added_by` the client sends in the body or URL.

**Other structural details:**
- The feed services load each event's `User` and `Song` with a separate `db.session.get` call per event.
- `get_user_playlists` is imported in `routes/playlists.py`, but no route exposes it.
- The test files each define their own `app` fixture rather than sharing one through `conftest.py`.

---

## 4. Root cause analysis

I fixed all five issues. I started with **#1, #3 and #5**, because each one has a specific, repeatable trigger and a unit test that already fails because of it. Then I fixed **#2 and #4** as stretch work. Neither of those had a test, so I wrote regression tests for them (see section 5).

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

**Trace from symptom to cause:**
1. The symptom shows up in `GET /users/<id>/streak` → `users.streak` → `streak_service.get_streak`. That function only returns the stored `User.listening_streak` column, so the wrong value had already been written.
2. The streak is written by `POST /songs/<id>/listen` → `songs.listen` → `record_listening_event` → `update_listening_streak(user, now)`.
3. For a Saturday → Sunday listen, `days_since_last == 1`, so the "listened yesterday" branch should run. It didn't, and the `else` branch reset the streak instead.

**Root cause:** in `services/streak_service.py:73`, the "listened yesterday" branch was `elif days_since_last == 1 and today.weekday() != 6:`. `weekday() == 6` is Sunday. On a Sunday the extra condition is false even after a Saturday listen, so the code fell through to `else` and set `listening_streak = 1`. The docstring's streak rules (lines 46–50) say nothing about weekdays, so the condition contradicts the spec. Monday works again because `weekday()` is 0, which is why kenji saw the streak start counting again from 1.

**Fix:** I removed the weekday condition so the line reads `elif days_since_last == 1:`. This one-line change brings the code back in line with the documented rules.

**Checking related behavior:**
- `pytest tests/test_streaks.py`: 5/5 pass, including `test_streak_increments_on_sunday`. The full suite went from 4 failures to 3, and the remaining 3 belong to Issues #3 and #5.
- Reran the API reproduction: Sat → Sun now goes 12 → 13, and Sun → Mon goes 13 → 14.
- Checked the edge cases next to the removed condition, all with a starting streak of 5:
  - Sat → Mon (Sunday skipped) resets to 1.
  - Sun → Tue (Monday skipped) resets to 1.
  - Two listens on the same Sunday leave it at 5.
  - Sun → Mon increments to 6.

  So the "skipped day resets" and "same day doesn't double count" rules still hold.

**Commit:** `6f72599 fix: stop resetting listening streak on Sundays`

### Issue #2: Friends Listening Now shows people from yesterday

**How I reproduced it:**
- **Data condition:** a friend whose most recent listen was yesterday evening, checked the next morning. The seed timestamps are relative to the real clock, so I cleared `ListeningEvent` in a scratch copy of the seeded DB and inserted events at fixed times for nova's friends. Then I froze `datetime.now()` inside `services/feed_service.py`.
- **Steps:**
  1. Inserted three listens: darius on Monday 2024-06-10 at 23:00 (the "11pm last night" from the report), simone on Tuesday at 08:30, and kenji on Monday at 07:00 (26 hours earlier, as a control).
  2. Froze "now" at Tuesday 09:00 and called `GET /feed/<nova_id>/listening-now`.
  3. Repeated the call with "now" frozen at Tuesday 22:59.
- **Result:**

  | "Now" | Expected friends | Actual friends |
  |---|---|---|
  | Tue 09:00 | simone | simone, **darius** ❌ |
  | Tue 22:59 | simone | simone, **darius** ❌ |

  kenji, at 26 hours old, was correctly left out both times. darius only drops off after 23:00 on Tuesday, exactly 24 hours after his listen. That matches nova's description: "Stuff from yesterday evening keeps hanging around in the feed until the same time the next day."

**How I found the root cause:**
1. `GET /feed/<id>/listening-now` → `feed.listening_now` (`routes/feed.py:9`) → `feed_service.get_friends_listening_now(user_id)`. The route just wraps the list with a count, so the filtering happens in the service.
2. In the service, the only time filter is `ListeningEvent.listened_at >= cutoff`, with `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD` (line 32). `RECENT_THRESHOLD` is defined at line 13 as `timedelta(hours=24)`.
3. The kenji control told me the filter itself runs correctly: a 26-hour-old event really is excluded. The problem is how wide the window is. The moment I was sure was the 22:59 run. A 9pm listen still showing the next evening is only possible with a rolling 24-hour window, and the report's "until the same time the next day" describes exactly that.

**Root cause:** `get_friends_listening_now` treats "recent" as "within the last 24 hours" (`cutoff = now - timedelta(hours=24)`). The feature is supposed to show who has listened *today*, which is a calendar boundary that resets at midnight. A rolling 24-hour window doesn't reset at midnight. Each listen stays visible for a full day after it happened, so a listen at 23:00 Monday sits in the feed until 23:00 Tuesday and shows up as "listening now" all of Tuesday morning. The bug only shows for listens from the previous calendar day that are less than 24 hours old, which is why it looked like "yesterday's stuff hanging around."

**Fix:** I changed the cutoff to the start of the current day: `datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)`. I removed the now-unused `RECENT_THRESHOLD` constant and `timedelta` import, and updated the docstring to say "since midnight UTC." The day boundary is UTC because the whole app stores and compares time in UTC (`models.py` defaults, `streak_service`), and `User` has no time zone field to localize with.

**Side-effect check:**
- **Both sides of the boundary:** with "now" at Tuesday 00:00:30, a listen at Monday 23:59:59 is excluded and a listen at Tuesday 00:00:00 is included. This also confirms the aware `cutoff` compares correctly against the naive datetimes SQLite returns. That comparison was the riskiest part of the change, since SQLite drops `tzinfo`.
- **Late in the same day:** a listen at 08:30 still shows at 22:59. The fix doesn't shorten the window within a day.
- **One entry per friend:** the "most recent song per friend" dedup loop is unchanged. A new test with two listens by simone today returns one entry, the newer one.
- **Activity feed:** `get_activity_feed` never used the cutoff, and it still returns every friend event (count 2 in the reproduction).
- **Edge responses:** a user whose friends have no listens today gets `200` with `count: 0`, and an unknown user still gets `404`.
- **Known limitation:** "today" is a UTC day. For a user in New York, the feed resets at 8pm EDT, not at local midnight. Fixing that needs a per-user time zone, which the data model doesn't have, so I left it out of scope.
- `pytest`: the full suite passes.

**Commit:** see `git log`, `fix: limit Friends Listening Now to listens since midnight`

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

**Trace from symptom to cause:**
1. `GET /songs/search?q=Anthem` → `songs.search` → `search_service.search_songs(query)`. The route just returns the list and its length, so the duplicates had to come from the service.
2. I compiled the query and ran it for `%Anthem%`:
   ```sql
   SELECT song.title, song_tags.tag_id
   FROM song LEFT OUTER JOIN song_tags ON song.id = song_tags.song_id
   WHERE lower(song.title) LIKE lower('%Anthem%')
   ```
   It returned **3 rows** for "Crown Heights Anthem", one for each `tag_id`. `.scalars().all()` maps each row to the same `Song` object and does not collapse them, so `to_dict()` ran 3 times.

**Root cause:** `services/search_service.py:30` added `.outerjoin(song_tags, Song.id == song_tags.c.song_id)` to the search. A join against a many-to-many table multiplies rows: a song gets one row per tag. Because it's an *outer* join, a song with no tags still gets exactly one row. That explains why the duplicates looked inconsistent: songs with 0 or 1 tags appeared once, and songs with 3 tags appeared three times. The join served no purpose. The `WHERE` clause only uses `Song.title` and `Song.artist`, and tags reach the response through the `Song.tags` relationship inside `Song.to_dict()`.

The duplicates were hidden before commit `dc87e8c`, because the legacy `db.session.query(Song)` deduplicates single-entity results. Moving to SQLAlchemy 2.0's `select()` exposed the bug, but the join is the cause.

**Fix:** I removed the `.outerjoin(...)` line and the `song_tags` import, which nothing used after that. I chose this over adding `.distinct()`: `.distinct()` would have hidden the effect while leaving a pointless join in every search query.

**Checking related behavior:**
- `pytest tests/test_search.py`: 5/5 pass. The full suite is down to 2 failures, both from Issue #5.
- Reran the reproduction. "Anthem" and "After Hours" (3 tags each) now return 1 result each. The 0-tag and 1-tag songs are unchanged.
- **Tags are still in the response:** Crown Heights Anthem still returns `['rap', 'hip-hop', 'boom bap']`.
- **Matching still works:** a lowercase query (`anthem`) and an uppercase artist query (`BOROUGH`) still match, and a broad query (`q=e`) returns 12 results with 12 unique IDs.
- **Validation is unchanged:** an empty `q` still returns 400.
- **Process note:** a tooling hook blocked my first attempt at this edit while letting the import removal through. That left the file referencing `song_tags` without importing it, and the test run caught the resulting `NameError` before I committed. I reapplied the edit and reran all the checks above against the final code.

**Commit:** `2959ec0 fix: remove unused song_tags join that duplicated search results`

### Issue #4: No notification when a friend rates your song

**How I reproduced it:**
- **Data condition:** a song shared by one user and rated by a different user. Seed data covers this: "Midnight Drive" is shared by nova, and nova already has one working notification ("darius added your song 'Midnight Drive' to the playlist 'Late Night Vibes'"), which is the control from aaliya's report.
- **Steps:**
  1. `GET /users/<nova_id>/notifications`: count **1**.
  2. `POST /songs/<midnight_drive_id>/rate` with `{"user_id": kenji, "score": 5}` returned **201** with the saved rating.
  3. `GET /users/<nova_id>/notifications` again: still count **1**, holding only the playlist notification.
- **Result:** the rating is saved, just as the report says, but no notification of any kind is created. The notifications endpoint works, because it shows the seeded playlist notification, so the rating notification was never written in the first place.

**How I found the root cause:**
1. `POST /songs/<id>/rate` → `songs.rate` (`routes/songs.py:29`) → `notification_service.rate_song(user_id, song_id, score)`. The route doesn't touch notifications at all, so if one is created, `rate_song` has to do it.
2. I checked the read side first: `GET /users/<id>/notifications` → `get_notifications`, which just queries `Notification` by `user_id`. It returned the seeded row, so reading isn't the problem.
3. Following the hint about the working path, I put `add_to_playlist` (lines 35–70) and `rate_song` (lines 73–110) side by side. They have the same structure: validate inputs, look up `Song` and `User`, write, then `db.session.commit()`. After its commit, `add_to_playlist` has one more block: `if song.shared_by != added_by_user_id: create_notification(...)`. `rate_song` goes straight from `commit()` to `return rating`.
4. The moment I was sure was `git grep create_notification`. It has exactly **one** call site, in `add_to_playlist`, but its own docstring lists `'song_rated'` as a notification type. Nothing in the codebase ever produces that type.

**Root cause:** notifications in Mixtape are not automatic side effects of writing data. There is no model event, signal, or listener that reacts to a new `Rating` or playlist entry. Each action has to call `create_notification` explicitly, and `add_to_playlist` does. `rate_song` saves the `Rating` and returns without ever making that call, so the code path that would create a `'song_rated'` notification doesn't exist. That's why it fails for every user, every time, with no delay or partial result: the rating write succeeds and the notification step was never written.

**Fix:** after the rating is committed, `rate_song` now runs the same guarded block as `add_to_playlist`. If the rater is not the person who shared the song, it calls `create_notification(user_id=song.shared_by, notification_type="song_rated", body=f"{rater.username} rated your song '{song.title}' {score} out of 5.")`. I placed it after the commit and after all validation, so a rejected rating can never leave a notification behind. I followed the existing pattern rather than adding a general event system, which would be a much larger change than this bug needs.

**Side-effect check:**
- **Rating response unchanged:** `POST /songs/<id>/rate` still returns `201` with the rating, and the score is still saved.
- **Self-rating:** nova rating her own song creates no notification (count stays the same). This matches the "not the sharer" rule from `add_to_playlist`.
- **Invalid score:** a score of 9 still returns `400`, and no notification is created, because validation raises before the notify step runs.
- **Re-rating:** kenji changing his rating from 5 to 3 updates the existing `Rating` row (the upsert still works) and sends a new notification with the new score. I chose this on purpose: a changed rating is new information for the sharer.
- **Existing notifications:** the seeded playlist notification is still returned, newest first, after the new rating notification.
- **Transactions:** like `add_to_playlist`, the rating and the notification are two separate commits, because `create_notification` commits on its own. I kept that to match the existing pattern instead of changing `create_notification` for both callers.
- `pytest`: the full suite passes.

**Commit:** see `git log`, `fix: notify song sharer when a friend rates their song`

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
- **Note on step 3:** adding the song through the API (`POST /playlists/<id>/songs`) returns **500** with `IntegrityError: NOT NULL constraint failed: playlist_entries.position`. `add_to_playlist` uses `playlist.songs.append(song)`, which never sets `position` or `added_by`. That is a separate bug (see section 6). To finish the reproduction, I inserted the 8th entry directly with `playlist_entries.insert()`, the same way `seed_data.py` does.
- **Test confirmation:** `tests/test_playlists.py::test_playlist_returns_all_songs` (expects 5, gets 4) and `test_playlist_returns_songs_in_order` both fail.

**Trace from symptom to cause:**
1. `GET /playlists/<id>/songs` → `playlists.get_songs` → `playlist_service.get_playlist_songs(playlist_id)`. The route returns the list and `len(list)`, so the reported "count" is the length of whatever the service returns.
2. I ran the service's query on its own for "Friday Energy". It returned **7** songs in position order, ending with "Harlem Renaissance". The service function returned **6**, ending with "Crown Heights Anthem". The join, filter and ordering were all correct, so the song was lost after the query ran.

**Root cause:** `services/playlist_service.py:66` returned `[song.to_dict() for song in songs[:-1]]`. In Python, `[:-1]` means "everything except the last element". Because the list is sorted by `position` ascending, the last element is always the most recently added song. That explains darius's report exactly: adding a new song moves the "last" slot to the new song, which "frees" the previous one and hides the new one.

**Fix:** I changed `songs[:-1]` to `songs`, so the function returns every row.

**Checking related behavior:**
- `pytest tests/test_playlists.py`: 3/3 pass. That includes the all-songs and ordering tests, and the empty-playlist test still returns `[]`, so there's no indexing error on an empty list. The full suite now passes: **13/13**.
- Reran the reproduction. "Friday Energy" returns 7/7 songs, ending with "Harlem Renaissance". After the 8th song is added, it returns 8/8, ending with the new song "Frequencies".
- Compared the API count to the `playlist_entries` row count for every seeded playlist. Late Night Vibes is 7/7, Friday Energy 8/8 and Study Mode 7/7.
- `GET /playlists/<id>` (metadata) still returns 200, and an unknown playlist ID still returns 404.
- **Not fixed (out of scope):** adding a song through `POST /playlists/<id>/songs` still returns 500 because of the separate `playlist_entries` NOT NULL bug described in section 6. This fix only changes how songs are read back.

**Commit:** `d4d08e3 fix: return the last song in playlist results`

---

## 5. Regression tests

The starter repo already had failing tests for Issues #1, #3 and #5. Issues #2 and #4 had no tests at all: `tests/` had nothing for the feed or for notifications. I wrote two new test files for them. Both follow the existing pattern: an in-memory SQLite `app` fixture, with the service functions called directly.

**`tests/test_feed.py` (Issue #2).** The tests swap `feed_service.datetime` for a subclass whose `now()` is fixed (Tuesday 2024-06-11, 09:00 UTC), so results don't depend on when the suite runs.
- `test_listen_from_last_night_is_not_listening_now`: darius listened Monday at 23:00 and simone Tuesday at 08:30, so at 09:00 only simone appears. **Against the buggy code this failed** with `['simone', 'darius'] != ['simone']`, because darius's listen was still inside the rolling 24-hour window. This is the exact scenario from nova's report.
- `test_midnight_is_the_boundary`: at Tuesday 00:00:30, a listen at Monday 23:59:59 is excluded and one at Tuesday 00:00:00 is included. **This also failed against the buggy code**, because the 24-hour window included both.
- `test_listen_from_earlier_today_still_shows_late_in_the_day` and `test_only_latest_song_per_friend`: guard tests. They passed before and after the fix, and they make sure the fix doesn't over-correct by dropping same-day listens or the one-entry-per-friend behavior.

**`tests/test_notifications.py` (Issue #4).**
- `test_rating_notifies_the_sharer`: kenji rates aaliya's song, and aaliya ends up with exactly one `song_rated` notification that names kenji and the song. **Against the buggy code this failed** with `assert 0 == 1`, because `rate_song` never called `create_notification`.
- `test_rating_your_own_song_does_not_notify` and `test_invalid_rating_does_not_notify`: guard tests for the self-rating rule and for a rejected score of 9.

I wrote both files *before* changing either service and ran them first. The result was 3 failed and 4 passed, and the 3 failures were exactly the bug-capturing tests above. After the two fixes, the full suite passes: **20 passed**, including the original 13.

---

## 6. Other findings (not fixed)

**`POST /playlists/<id>/songs` returns 500.** `playlist_entries` declares `position` and `added_by` as `NOT NULL`, but `add_to_playlist` adds songs through the plain `secondary=` relationship (`playlist.songs.append(song)`), which can't fill those columns. Running it against an in-memory DB fails with `IntegrityError: NOT NULL constraint failed: playlist_entries.position`. This affects the Issue #5 reproduction (step 3). It isn't one of the five listed issues, so I left it alone. A fix would insert into `playlist_entries` directly with the next `position` and the `added_by` user, the way `seed_data.py` does.
