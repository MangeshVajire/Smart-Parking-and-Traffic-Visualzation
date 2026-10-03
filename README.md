# Smart Parking — Backend & Database

Flask + SQLite backend for the Smart Parking and Traffic Visualization app.
Replaces per-device localStorage with one shared database: availability,
bookings and admin revenue are now the same for every device that calls
this API, not just the phone that made the booking.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

The database file `parking.db` is created and seeded automatically on
first run, from the same 76 real Nagpur/Pune/Mumbai locations the web
app uses (`cities_seed.json`).

API base: `http://localhost:5000/api/...`

## How availability updates

- **Free/paid spots**: `occupied` drifts server-side every 5 seconds
  (a small random walk, bounded by capacity minus active bookings) —
  the same "simulated but disclosed" approach the frontend used,
  just centralized now so everyone sees the same number.
- **Booked** count is not simulated — it's a live COUNT of real
  `Booking` rows with status `ongoing` or `awaiting_payment` for that
  spot. Free = capacity − occupied − booked.
- **EV ports**: `in_use` drifts the same way, bounded by port count.
- A background thread flips a booking from `ongoing` to
  `awaiting_payment` once its `expiryTime` passes, every 10 seconds —
  so a client polling `GET /api/bookings?userId=...` sees the status
  change without needing its own timer.

## API reference

| Endpoint | Method | What it does |
|---|---|---|
| `/api/health` | GET | Status, spot count, booking count |
| `/api/cities` | GET | List of city keys |
| `/api/spots?city=nagpur` | GET | All spots in a city, with live availability |
| `/api/spots/<id>` | GET | One spot, with live availability |
| `/api/bookings` | POST | Create a booking `{spotId, userId, vehicle, durationLabel}` |
| `/api/bookings?userId=...` | GET | A user's bookings, newest first |
| `/api/bookings/<id>` | PATCH | `{"action":"cancel"}` or `{"action":"pay"}` |
| `/api/admin/revenue` | GET | Collected total, pending dues, revenue by city, top spots — **across every user** |

## Deploy to Render

1. Push this folder to a GitHub repo.
2. Render → **New → Blueprint** (uses `render.yaml`), or **New → Web Service** manually:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn --workers 1 --threads 4 app:app`
3. **Important — free-tier disk:** Render's free web services use an
   ephemeral filesystem, so the SQLite file is wiped on every restart
   or redeploy. Fine for a demo/presentation; for anything that must
   persist, add a Render **PostgreSQL** instance (free tier available)
   and set the `DATABASE_URL` environment variable it gives you —
   `app.py` already reads that variable and adapts automatically.
4. Keep exactly **one worker** (`--workers 1`) unless you move off
   SQLite — SQLite doesn't handle concurrent writers from multiple
   processes well. Postgres removes that limit.

## Next step: connect the web app

The published web app currently stores bookings and availability in
`localStorage` on each device. Pointing it at this API instead —
swapping `getBookings`/`setBookings`/the availability simulation for
`fetch()` calls to these endpoints — is what makes availability and
the admin dashboard genuinely shared across every visitor. That's a
frontend change, not included here; ask for it as a separate step
once this backend is deployed and you have its URL.
