from waitress import serve
from app import app, init_db

with app.app_context():
    init_db()

serve(app, host='127.0.0.1', port=5000)
