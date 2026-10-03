"""
Smart Parking and Traffic Visualization — backend API.

Stores real parking/EV spot data and every booking in a SQLite database
(parking.db) so availability and admin revenue are shared across every
device that talks to this server — not per-browser localStorage.

Run locally:   pip install -r requirements.txt && python app.py
Then:          http://localhost:5000/api/spots?city=nagpur
"""
import os
import random
import string
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS

from models import db, ParkingSpot, Booking
from seed import seed_if_empty

BASE_DIR = os.path.dirname(__file__)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', f"sqlite:///{os.path.join(BASE_DIR, 'parking.db')}"
).replace('postgres://', 'postgresql://', 1)  # Render gives postgres:// ; SQLAlchemy wants postgresql://
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
CORS(app)

RATE_PER_HOUR = 20  # ₹ — paid spots only; matches the web app's assumed rate
DURATION_HOURS = {'1 hour': 1, '2 hours': 2, '4 hours': 4, 'Full day': 10}

with app.app_context():
    db.create_all()
    seed_if_empty()


# ---------- background occupancy drift (server-side, shared by everyone) ----------

def drift_loop():
    while True:
        time.sleep(5)
        with app.app_context():
            for spot in ParkingSpot.query.all():
                if spot.type == 'ev':
                    spot.in_use = max(0, min(spot.ports, spot.in_use + random.randint(-1, 1)))
                else:
                    booked = spot.active_bookings_count()
                    ceiling = max(0, spot.capacity - booked)
                    spot.occupied = max(0, min(ceiling, spot.occupied + random.randint(-2, 2)))
            db.session.commit()


def expiry_loop():
    """Flips ongoing bookings to awaiting_payment once their time is up."""
    while True:
        time.sleep(10)
        with app.app_context():
            now_ms = int(time.time() * 1000)
            due = Booking.query.filter(Booking.status == 'ongoing', Booking.expiry_time <= now_ms).all()
            for b in due:
                b.status = 'awaiting_payment'
            if due:
                db.session.commit()


threading.Thread(target=drift_loop, daemon=True).start()
threading.Thread(target=expiry_loop, daemon=True).start()


# ---------------------------------- routes ----------------------------------

@app.get('/api/health')
def health():
    return jsonify({'status': 'ok', 'spots': ParkingSpot.query.count(), 'bookings': Booking.query.count()})


@app.get('/api/cities')
def cities():
    rows = db.session.query(ParkingSpot.city).distinct().all()
    return jsonify(sorted(r[0] for r in rows))


@app.get('/api/spots')
def list_spots():
    city = request.args.get('city')
    q = ParkingSpot.query
    if city:
        q = q.filter_by(city=city)
    return jsonify([s.to_dict() for s in q.all()])


@app.get('/api/spots/<int:spot_id>')
def get_spot(spot_id):
    spot = ParkingSpot.query.get_or_404(spot_id)
    return jsonify(spot.to_dict())


@app.post('/api/bookings')
def create_booking():
    data = request.get_json(force=True)
    spot = ParkingSpot.query.get_or_404(data['spotId'])
    if spot.type == 'ev':
        return jsonify({'error': 'EV charging spots are not bookable in this demo'}), 400

    avail = spot.availability()
    if avail['free'] <= 0:
        return jsonify({'error': 'No free spots right now'}), 409

    vehicle = (data.get('vehicle') or '').strip()
    if not vehicle:
        return jsonify({'error': 'vehicle is required'}), 400
    user_id = (data.get('userId') or '').strip()
    if not user_id:
        return jsonify({'error': 'userId is required'}), 400
    duration_label = data.get('durationLabel', '1 hour')
    duration_hours = DURATION_HOURS.get(duration_label, 1)
    rate = 0 if spot.type == 'free' else RATE_PER_HOUR
    total_bill = rate * duration_hours
    start_time = int(time.time() * 1000)

    booking = Booking(
        id='SPT-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6)),
        spot_id=spot.id, user_id=user_id, vehicle=vehicle,
        duration_label=duration_label, duration_hours=duration_hours,
        rate=rate, total_bill=total_bill,
        start_time=start_time, expiry_time=start_time + int(duration_hours * 3600000),
        status='ongoing',
    )
    db.session.add(booking)
    db.session.commit()
    return jsonify(booking.to_dict()), 201


@app.get('/api/bookings')
def list_bookings():
    user_id = request.args.get('userId')
    if not user_id:
        return jsonify({'error': 'userId query param is required'}), 400
    rows = Booking.query.filter_by(user_id=user_id).order_by(Booking.start_time.desc()).all()
    return jsonify([b.to_dict() for b in rows])


@app.patch('/api/bookings/<booking_id>')
def update_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    action = request.get_json(force=True).get('action')
    if action == 'cancel':
        booking.status = 'cancelled'
    elif action == 'pay':
        if booking.status != 'awaiting_payment':
            return jsonify({'error': 'Booking is not awaiting payment'}), 409
        booking.status = 'paid'
        booking.paid_at = int(time.time() * 1000)
        booking.txn_id = 'TXN' + str(booking.paid_at)[-8:] + ''.join(random.choices(string.ascii_uppercase, k=3))
    else:
        return jsonify({'error': 'action must be "cancel" or "pay"'}), 400
    db.session.commit()
    return jsonify(booking.to_dict())


@app.get('/api/admin/revenue')
def admin_revenue():
    """Aggregated across every user and device — this is the whole point
    of moving off localStorage: one real, shared source of truth."""
    paid = Booking.query.filter_by(status='paid').all()
    due = Booking.query.filter_by(status='awaiting_payment').all()
    revenue_by_city = {}
    revenue_by_spot = {}
    for b in paid:
        city = b.spot.city if b.spot else 'unknown'
        revenue_by_city[city] = revenue_by_city.get(city, 0) + b.total_bill
        name = b.spot.name if b.spot else 'unknown'
        revenue_by_spot[name] = revenue_by_spot.get(name, 0) + b.total_bill
    top_spots = sorted(revenue_by_spot.items(), key=lambda x: -x[1])[:5]
    return jsonify({
        'totalCollected': sum(b.total_bill for b in paid),
        'pendingDues': sum(b.total_bill for b in due),
        'totalBookings': Booking.query.count(),
        'revenueByCity': revenue_by_city,
        'topSpots': [{'name': n, 'revenue': v} for n, v in top_spots],
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
