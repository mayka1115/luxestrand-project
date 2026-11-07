import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, g, jsonify, make_response, request, send_from_directory
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge


ROOT = Path(__file__).resolve().parent
SESSION_COOKIE = "luxe_session"
SESSION_DURATION = 8 * 60 * 60
PASSWORD_ITERATIONS = 260_000
PUBLIC_FILES = {
    "index.html",
    "services.html",
    "appointment.html",
    "login.html",
    "dashboard.html",
    "user-dashboard.html",
    "style.css",
    "client.js",
}
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS, 32)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored_password):
    if not stored_password.startswith("pbkdf2_sha256$"):
        supplied = password.encode()
        stored = stored_password.encode()
        return hmac.compare_digest(supplied, stored), hmac.compare_digest(supplied, stored)

    try:
        algorithm, iteration_text, salt_hex, digest_hex = stored_password.split("$")
        iterations = int(iteration_text)
        if algorithm != "pbkdf2_sha256" or not 1 <= iterations <= 1_000_000:
            return False, False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if not expected:
            return False, False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, len(expected))
        return hmac.compare_digest(actual, expected), False
    except (ValueError, TypeError):
        return False, False


def connect_database(database_path):
    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_database(connection):
    app_environment = os.environ.get("APP_ENV", "development").lower()
    admin_username = os.environ.get("ADMIN_USERNAME")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if app_environment == "production" and (not admin_username or not admin_password):
        raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD before starting in production.")

    using_demo_credentials = not admin_username and not admin_password
    if using_demo_credentials:
        admin_username = "admin"
        admin_password = "admin123"
    elif not admin_username or not admin_password:
        raise RuntimeError("Set both ADMIN_USERNAME and ADMIN_PASSWORD.")
    if not USERNAME_PATTERN.fullmatch(admin_username):
        raise RuntimeError("ADMIN_USERNAME must be 3-32 letters, numbers, dots, underscores, or hyphens.")
    if (not using_demo_credentials and len(admin_password) < 12) or len(admin_password) > 128:
        raise RuntimeError("ADMIN_PASSWORD must be between 12 and 128 characters.")

    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "accounts" in tables and "users" not in tables:
        connection.execute("ALTER TABLE accounts RENAME TO users")
    if "sessions" in tables and "admin_sessions" not in tables:
        connection.execute("ALTER TABLE sessions RENAME TO admin_sessions")
    if "accounts" in tables and "users" in tables:
        connection.executescript("""
            INSERT OR IGNORE INTO users (username, password, email, role)
            SELECT username, password, email, role FROM accounts;
            DROP TABLE accounts;
        """)
    if "sessions" in tables and "admin_sessions" in tables:
        connection.executescript("""
            INSERT OR IGNORE INTO admin_sessions (token_hash, user_id, expires_at)
            SELECT token_hash, user_id, expires_at FROM sessions;
            DROP TABLE sessions;
        """)

    connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            email TEXT UNIQUE,
            role TEXT NOT NULL DEFAULT 'admin'
        );
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT NOT NULL,
            phone TEXT,
            service TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            user_id INTEGER REFERENCES users(id)
        );
    """)

    user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    if "email" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "role" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")

    appointment_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(appointments)")
    }
    if "email" in appointment_columns:
        connection.execute("ALTER TABLE appointments DROP COLUMN email")
    if "user_id" not in appointment_columns:
        connection.execute("ALTER TABLE appointments ADD COLUMN user_id INTEGER REFERENCES users(id)")

    connection.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL
        )
    """)

    appointment_foreign_keys = connection.execute("PRAGMA foreign_key_list(appointments)").fetchall()
    if any(row["table"] != "users" for row in appointment_foreign_keys):
        connection.executescript("""
            CREATE TABLE appointments_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT NOT NULL,
                phone TEXT,
                service TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                user_id INTEGER REFERENCES users(id)
            );
            INSERT INTO appointments_new (id, customer, phone, service, date, time, status, user_id)
            SELECT id, customer, phone, service, date, time, status,
                CASE WHEN user_id IN (SELECT id FROM users) THEN user_id ELSE NULL END
            FROM appointments;
            DROP TABLE appointments;
            ALTER TABLE appointments_new RENAME TO appointments;
        """)

    session_foreign_keys = connection.execute("PRAGMA foreign_key_list(admin_sessions)").fetchall()
    if any(row["table"] != "users" for row in session_foreign_keys):
        connection.executescript("""
            CREATE TABLE admin_sessions_new (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            INSERT INTO admin_sessions_new (token_hash, user_id, expires_at)
            SELECT token_hash, user_id, expires_at
            FROM admin_sessions
            WHERE user_id IN (SELECT id FROM users);
            DROP TABLE admin_sessions;
            ALTER TABLE admin_sessions_new RENAME TO admin_sessions;
        """)

    duplicate_names = connection.execute("""
        SELECT LOWER(username) AS username_key, MIN(id) AS keep_id
        FROM users
        WHERE role = 'customer'
        GROUP BY LOWER(username)
        HAVING COUNT(*) > 1
    """).fetchall()
    for duplicate in duplicate_names:
        duplicate_ids = connection.execute(
            "SELECT id FROM users WHERE role = 'customer' AND LOWER(username) = ? AND id != ?",
            (duplicate["username_key"], duplicate["keep_id"]),
        ).fetchall()
        for row in duplicate_ids:
            connection.execute(
                "UPDATE admin_sessions SET user_id = ? WHERE user_id = ?",
                (duplicate["keep_id"], row["id"]),
            )
            connection.execute(
                "UPDATE appointments SET user_id = ? WHERE user_id = ?",
                (duplicate["keep_id"], row["id"]),
            )
            connection.execute("DELETE FROM users WHERE id = ?", (row["id"],))

    connection.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email);
        CREATE UNIQUE INDEX IF NOT EXISTS users_customer_username_ci_unique
            ON users(LOWER(username)) WHERE role = 'customer';
    """)
    connection.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (int(time.time()),))

    admin = connection.execute(
        "SELECT id, role FROM users WHERE LOWER(username) = ? LIMIT 1",
        (admin_username.lower(),),
    ).fetchone()
    if admin and admin["role"] != "admin":
        raise RuntimeError("ADMIN_USERNAME is already assigned to a customer account.")
    if admin:
        connection.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(admin_password), admin["id"]),
        )
    else:
        connection.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, 'admin')",
            (admin_username, hash_password(admin_password)),
        )


def safe_integer(value):
    return type(value) is int and abs(value) <= 9_007_199_254_740_991


def create_app(database_path=None):
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    resolved_database_path = Path(
        database_path or os.environ.get("DATABASE_PATH", ROOT / "luxe_strand.db")
    ).resolve()
    resolved_database_path.parent.mkdir(parents=True, exist_ok=True)
    app.config["DATABASE_PATH"] = str(resolved_database_path)

    connection = connect_database(app.config["DATABASE_PATH"])
    try:
        initialize_database(connection)
    finally:
        connection.close()

    def get_db():
        if "database" not in g:
            g.database = connect_database(app.config["DATABASE_PATH"])
        return g.database

    @app.teardown_appcontext
    def close_db(_error=None):
        del _error
        database = g.pop("database", None)
        if database is not None:
            database.close()

    def send_error(message, status=400):
        return jsonify(success=False, message=message), status

    def read_json_body(invalid_message):
        if request.mimetype != "application/json":
            return None, send_error(invalid_message)
        if not request.get_data(cache=True):
            return None, send_error(invalid_message)
        try:
            data = request.get_json(silent=False)
        except BadRequest:
            return None, send_error("Invalid JSON request.")
        if not isinstance(data, dict):
            return None, send_error(invalid_message)
        return data, None

    def session_token():
        return request.cookies.get(SESSION_COOKIE)

    def get_session_user():
        token = session_token()
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = get_db().execute("""
            SELECT users.id, users.username, users.email, users.role
            FROM admin_sessions
            JOIN users ON users.id = admin_sessions.user_id
            WHERE admin_sessions.token_hash = ? AND admin_sessions.expires_at > ?
        """, (token_hash, int(time.time()))).fetchone()
        return dict(row) if row else None

    def set_session_cookie(response, token, max_age):
        secure = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=max_age,
            path="/",
            httponly=True,
            secure=secure,
            samesite="Lax",
        )
        return response

    def create_session(user_id):
        token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        get_db().execute(
            "INSERT INTO admin_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, int(time.time()) + SESSION_DURATION),
        )
        return token

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_request(error):
        return send_error("Request body is too large.", error.code or 413)

    @app.errorhandler(BadRequest)
    def handle_bad_request(error):
        return send_error("Invalid JSON request.", error.code or 400)

    @app.get("/")
    def index():
        return send_from_directory(ROOT, "index.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/api/session")
    def api_session():
        user = get_session_user()
        if not user:
            return jsonify(authenticated=False), 401
        return jsonify(
            authenticated=True,
            username=user["username"],
            email=user["email"],
            role=user["role"],
        )

    @app.post("/api/login")
    def api_login():
        data, error = read_json_body("Invalid login data.")
        if error:
            return error
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        user = get_db().execute(
            "SELECT id, username, password, role FROM users WHERE LOWER(username) = ?",
            (username.lower(),),
        ).fetchone()
        if not user:
            return send_error("Invalid username or password.", 401)

        valid, needs_rehash = verify_password(password, user["password"])
        if not valid:
            return send_error("Invalid username or password.", 401)
        if needs_rehash:
            get_db().execute(
                "UPDATE users SET password = ? WHERE id = ?",
                (hash_password(password), user["id"]),
            )

        token = create_session(user["id"])
        response = make_response(jsonify(success=True, message="Login successful.", role=user["role"]))
        return set_session_cookie(response, token, SESSION_DURATION)

    @app.post("/api/register")
    def api_register():
        data, error = read_json_body("Invalid account details.")
        if error:
            return error
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not USERNAME_PATTERN.fullmatch(username):
            return send_error("Use 3–32 letters, numbers, dots, underscores, or hyphens for the username.")
        if not 12 <= len(password) <= 128:
            return send_error("The password must be between 12 and 128 characters.")
        duplicate = get_db().execute(
            "SELECT id FROM users WHERE role = 'customer' AND LOWER(username) = ?",
            (username.lower(),),
        ).fetchone()
        if duplicate:
            return send_error("That username is already registered.", 409)

        try:
            cursor = get_db().execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, 'customer')",
                (username, hash_password(password)),
            )
            token = create_session(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return send_error("That username is already registered.", 409)
        response = make_response((jsonify(success=True, message="Account created.", role="customer"), 201))
        return set_session_cookie(response, token, SESSION_DURATION)

    @app.post("/api/logout")
    def api_logout():
        token = session_token()
        if token:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            get_db().execute("DELETE FROM admin_sessions WHERE token_hash = ?", (token_hash,))
        response = make_response(jsonify(success=True))
        return set_session_cookie(response, "", 0)

    @app.get("/api/appointments")
    def get_appointments():
        user = get_session_user()
        if not user:
            return jsonify(error="Sign in to view appointments."), 401
        if user["role"] == "admin":
            rows = get_db().execute(
                "SELECT id, customer, phone, service, date, time, status FROM appointments ORDER BY id DESC"
            ).fetchall()
        else:
            rows = get_db().execute(
                "SELECT id, customer, phone, service, date, time, status FROM appointments WHERE user_id = ? ORDER BY id DESC",
                (user["id"],),
            ).fetchall()
        return jsonify(appointments=[dict(row) for row in rows])

    @app.post("/api/appointments/status")
    def update_appointment_status():
        user = get_session_user()
        if not user:
            return send_error("Sign in as an admin to update appointments.", 401)
        if user["role"] != "admin":
            return send_error("Only admins can update appointment status.", 403)
        data, error = read_json_body("Invalid appointment update.")
        if error:
            return error
        if not safe_integer(data.get("id")):
            return send_error("Invalid appointment update.")
        status = data.get("status")
        if not isinstance(status, str) or status not in {"Pending", "Completed"}:
            return send_error("Choose Pending or Completed.")

        cursor = get_db().execute(
            "UPDATE appointments SET status = ? WHERE id = ?", (status, data["id"])
        )
        if not cursor.rowcount:
            return send_error("Appointment not found.", 404)
        return jsonify(success=True, status=status)

    @app.delete("/api/appointments")
    def delete_appointment():
        user = get_session_user()
        if not user:
            return send_error("Sign in as an admin to remove appointments.", 401)
        if user["role"] != "admin":
            return send_error("Only admins can remove appointments.", 403)
        data, error = read_json_body("Invalid appointment ID.")
        if error:
            return error
        if not safe_integer(data.get("id")):
            return send_error("Invalid appointment ID.")

        cursor = get_db().execute("DELETE FROM appointments WHERE id = ?", (data["id"],))
        if not cursor.rowcount:
            return send_error("Appointment not found.", 404)
        return jsonify(success=True, message="Appointment removed.")

    @app.post("/api/appointments")
    def create_appointment():
        data, error = read_json_body("Invalid appointment data.")
        if error:
            return error
        required = ("customer", "service", "date", "time")
        if any(not str(data.get(key) or "").strip() for key in required):
            return send_error("Missing required appointment information.")

        customer = str(data["customer"]).strip()
        if not USERNAME_PATTERN.fullmatch(customer):
            return send_error("Use a username-style name without spaces or last names.")
        service = str(data["service"]).strip()
        appointment_date = str(data["date"]).strip()
        appointment_time = str(data["time"]).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", appointment_date):
            return send_error("Enter a valid appointment date.")
        try:
            datetime.strptime(appointment_date, "%Y-%m-%d")
        except ValueError:
            return send_error("Enter a valid appointment date.")
        if int(appointment_date[:4]) != datetime.now().year:
            return send_error(f"Appointments must be scheduled within {datetime.now().year}.")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", appointment_time):
            return send_error("Enter a valid appointment time.")

        normalized_phone = None
        phone = str(data.get("phone") or "").strip()
        if phone:
            digits = re.sub(r"[^0-9]", "", phone)
            if digits.startswith("63"):
                digits = digits[2:]
            elif digits.startswith("0"):
                digits = digits[1:]
            if not re.fullmatch(r"9\d{9}", digits):
                return send_error("Enter a valid Philippine mobile number.")
            normalized_phone = f"+63 {digits[:3]} {digits[3:6]} {digits[6:]}"

        user = get_session_user()
        user_id = user["id"] if user and user["role"] == "customer" else None
        cursor = get_db().execute("""
            INSERT INTO appointments (customer, phone, service, date, time, status, user_id)
            VALUES (?, ?, ?, ?, ?, 'Pending', ?)
        """, (customer, normalized_phone, service, appointment_date, appointment_time, user_id))
        return jsonify(
            success=True,
            message="Your appointment request is saved and is pending salon confirmation.",
            id=cursor.lastrowid,
        ), 201

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(ROOT / "images", "favicon.jpg")

    @app.get("/images/<path:filename>")
    def image_file(filename):
        return send_from_directory(ROOT / "images", filename)

    @app.get("/<filename>")
    def public_file(filename):
        if filename not in PUBLIC_FILES:
            return "Not Found", 404
        return send_from_directory(ROOT, filename)

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "4000"))
    app = create_app()
    app.run(host="0.0.0.0", port=port)