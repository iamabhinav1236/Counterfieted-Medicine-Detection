from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, send_file
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
import qrcode
from flask import Response
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import ipaddress
import os, secrets, csv, re, json, hashlib
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
load_dotenv()

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TRUEDOSE_DB_PATH", str(APP_DIR / "truedose.db")))
app = Flask(__name__)
# Never ship a known default secret or administrator password. If local setup is incomplete,
# generate temporary credentials and print them once at startup; configure .env for persistence.
_secret = os.environ.get("TRUEDOSE_SECRET_KEY", "").strip()
IS_PRODUCTION = os.environ.get("ENVIRONMENT", "development").strip().lower() == "production"
if not _secret or _secret.startswith("replace-with-"):
    if IS_PRODUCTION:
        raise RuntimeError("Set a unique TRUEDOSE_SECRET_KEY in .env before starting production mode.")
    _secret = secrets.token_urlsafe(48)
    print("[TrueDose AI] No persistent TRUEDOSE_SECRET_KEY configured; generated a temporary session key.")
app.secret_key = _secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("TRUEDOSE_COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    PERMANENT_SESSION_LIFETIME=3600,
)
ADMIN_PASSWORD = os.environ.get("TRUEDOSE_ADMIN_PASSWORD", "").strip()
if not ADMIN_PASSWORD or ADMIN_PASSWORD in ("replace-with-a-strong-unique-password", "change-this-before-deployment"):
    if IS_PRODUCTION:
        raise RuntimeError("Set a unique TRUEDOSE_ADMIN_PASSWORD in .env before starting production mode.")
    ADMIN_PASSWORD = secrets.token_urlsafe(20)
    print("[TrueDose AI] Temporary administrator password (copy it now): " + ADMIN_PASSWORD)
    print("[TrueDose AI] Set TRUEDOSE_ADMIN_PASSWORD in .env to keep a stable password across restarts.")


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS medicines (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                manufacturer TEXT NOT NULL,
                batch TEXT NOT NULL,
                expiry TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_code TEXT,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('manufacturer','distributor','pharmacist','customer')),
                organization TEXT NOT NULL DEFAULT '',
                approved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                entity_code TEXT,
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                block_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                scanned_at TEXT NOT NULL,
                result TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                reason TEXT NOT NULL
            )
        """)
        # Safe, forward-only migration for databases created by earlier project versions.
        user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "approved" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scans_code_time ON scans(code, scanned_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_entity_id ON ledger(entity_code, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role, approved)")
        conn.execute("UPDATE medicines SET status='suspended' WHERE status NOT IN ('registered','suspected_clone','suspended','recalled')")
        conn.commit()
        ledger_count = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    if ledger_count == 0:
        append_ledger_event("ledger_initialized", "system", None, {"ledger_type": "local_hash_linked_audit_chain", "note": "Not a distributed blockchain"})

def append_ledger_event(event_type, actor, entity_code, payload):
    """Append a tamper-evident hash-linked event to the local ledger.

    This is a single-server audit chain, not a distributed public blockchain.
    """
    created_at = iso_now()
    payload_text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with connect_db() as conn:
        # Acquire the SQLite write lock before reading the tip to avoid concurrent forks.
        conn.execute("BEGIN IMMEDIATE")
        last = conn.execute("SELECT block_hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
        previous_hash = last["block_hash"] if last else "0" * 64
        material = "|".join((previous_hash, event_type, actor, entity_code or "", payload_text, created_at))
        block_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
        conn.execute("INSERT INTO ledger(event_type,actor,entity_code,payload,previous_hash,block_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                     (event_type, actor, entity_code, payload_text, previous_hash, block_hash, created_at))
        conn.commit()
    return block_hash

def verify_ledger_chain():
    with connect_db() as conn:
        rows = conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
    previous = "0" * 64
    for row in rows:
        material = "|".join((previous, row["event_type"], row["actor"], row["entity_code"] or "", row["payload"], row["created_at"]))
        expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
        if row["previous_hash"] != previous or row["block_hash"] != expected:
            return {"valid": False, "checked_blocks": row["id"], "invalid_block": row["id"]}
        previous = row["block_hash"]
    return {"valid": True, "checked_blocks": len(rows), "latest_hash": previous if rows else None}

def iso_now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

@app.before_request
def ensure_csrf_token():
    # Create a per-session token for HTML forms, including login and registration.
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)


def csrf_is_valid():
    supplied = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    return bool(supplied and expected and secrets.compare_digest(supplied, expected))


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
    if request.path.startswith(("/admin", "/portal")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

@app.route("/")
def index():
    return render_template("index.html")

@app.get("/api/health")
def health():
    try:
        with connect_db() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({"ok": True, "app": "TrueDose AI", "database": "connected", "ledger_integrity": verify_ledger_chain()["valid"]})
    except sqlite3.Error:
        return jsonify({"ok": False, "app": "TrueDose AI", "database": "unavailable"}), 503

@app.get("/api/stats")
def stats():
    with connect_db() as conn:
        total_medicines = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
        total_scans = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        flagged = conn.execute("SELECT COUNT(*) FROM scans WHERE result != 'REGISTERED RECORD'").fetchone()[0]
        recent = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 6").fetchall()
    return jsonify({
        "medicine_records": total_medicines,
        "total_scans": total_scans,
        "flagged_scans": flagged,
        "recent_scans": [dict(row) for row in recent]
    })

@app.get("/api/qr")
def generate_qr():
    """Generate a QR image for a registry code. The QR contains only the code."""
    code = str(request.args.get("code", "")).strip().upper()
    if not code:
        return jsonify({"error": "Choose a medicine code first."}), 400
    with connect_db() as conn:
        medicine = conn.execute("SELECT code FROM medicines WHERE code = ?", (code,)).fetchone()
    if not medicine:
        return jsonify({"error": "That code is not in the configured registry."}), 404
    image = qrcode.make(code)
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", download_name=f"truedose-{code.lower()}.png")

@app.get("/api/ipcam/stream")
def ipcam_stream():
    """Proxy a private-LAN IP Webcam MJPEG stream so browser canvas QR scanning can read frames."""
    stream_url = str(request.args.get("url", "")).strip()
    parsed = urlparse(stream_url)
    try:
        host_ip = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        return jsonify({"error": "Enter a valid phone IPv4 address, e.g. http://192.168.1.25:8080/video"}), 400
    if (host_ip.version != 4 or not host_ip.is_private or host_ip.is_loopback or host_ip.is_link_local or host_ip.is_multicast or
            host_ip.is_reserved or parsed.scheme != "http" or parsed.username or parsed.password):
        return jsonify({"error": "Only a private-network phone IP Webcam URL is allowed; loopback, link-local, and reserved addresses are blocked."}), 400
    if parsed.path.rstrip("/") not in ("/video", "/videofeed"):
        return jsonify({"error": "Use the IP Webcam video stream path, usually /video."}), 400
    try:
        port = parsed.port or 8080
    except ValueError:
        return jsonify({"error": "Invalid stream port."}), 400
    if not (1 <= port <= 65535):
        return jsonify({"error": "Invalid stream port."}), 400
    safe_url = f"http://{host_ip.compressed}:{port}{parsed.path or '/video'}"

    # Validate reachability before returning the streaming response, so connection errors are visible.
    try:
        req = Request(safe_url, headers={"User-Agent": "TrueDose-AI/1.0"})
        upstream = urlopen(req, timeout=4)
    except Exception as exc:
        return jsonify({"error": f"Could not connect to the phone stream ({type(exc).__name__}). Check the IP, port, Wi-Fi, and IP Webcam app."}), 502

    content_type = upstream.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=--ipcamera")
    def relay():
        try:
            while True:
                chunk = upstream.read(4096)
                if not chunk:
                    break
                yield chunk
        except Exception:
            pass
        finally:
            upstream.close()
    return Response(relay(), content_type=content_type, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

@app.get("/api/medicines")
def medicines():
    with connect_db() as conn:
        rows = conn.execute("SELECT code,name,manufacturer,batch,expiry,status FROM medicines ORDER BY name").fetchall()
    return jsonify([dict(row) for row in rows])

@app.post("/api/verify")
def verify():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).strip().upper()
    if not code:
        return jsonify({"error": "Enter a medicine code first."}), 400
    if len(code) > 80 or not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,79}", code):
        return jsonify({"error": "Use a valid medicine code (3–80 letters, numbers, dots, underscores, or hyphens)."}), 400

    with connect_db() as conn:
        medicine = conn.execute("SELECT * FROM medicines WHERE code = ?", (code,)).fetchone()
        # Use a consistently formatted local ISO timestamp for the rolling time window.
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc).astimezone() - timedelta(minutes=10)).isoformat(timespec="seconds")
        previous_count = conn.execute(
            "SELECT COUNT(*) FROM scans WHERE code = ? AND scanned_at >= ?",
            (code, cutoff)
        ).fetchone()[0]

        result, risk, reason = "UNKNOWN CODE", 85, "This code is not in the configured registry."
        record = None
        signals = []
        if medicine:
            record = dict(medicine)
            try:
                expiry_date = datetime.strptime(medicine["expiry"], "%Y-%m-%d").date()
            except ValueError:
                expiry_date = None

            if medicine["status"] == "suspected_clone":
                result, risk, reason = "SUSPICIOUS", 95, "This record is marked as a suspected clone. Investigate the source before relying on it."
                signals.append({"level":"high", "title":"Suspected clone status", "detail":"The registry record is explicitly marked for investigation; this is a registry flag, not an independent laboratory finding."})
            elif medicine["status"] == "recalled":
                result, risk, reason = "RECALLED PRODUCT", 98, "This registry record is marked recalled. Do not use it; contact a pharmacist or relevant authority."
                signals.append({"level":"high", "title":"Recall status recorded", "detail":"The registry marks this record as recalled. Confirm recall details with an authoritative source."})
            elif medicine["status"] == "suspended":
                result, risk, reason = "REGISTRY SUSPENDED", 80, "This registry record is suspended and requires investigation before reliance."
                signals.append({"level":"high", "title":"Registry record suspended", "detail":"The record has been administratively suspended; check the issuing source for the reason."})
            elif expiry_date and expiry_date < datetime.now().date():
                result, risk, reason = "EXPIRED BATCH", 90, "The recorded expiry date has passed. Do not use; ask a pharmacist."
                signals.append({"level":"high", "title":"Recorded expiry has passed", "detail":f"Registry expiry date: {medicine['expiry']}. Do not use this medicine; consult a pharmacist."})
            else:
                result, risk, reason = "REGISTERED RECORD", 5, "A matching record was found in the local configured registry. This does not prove the physical pack is genuine."
                signals.append({"level":"info", "title":"Registry record matched", "detail":"The code exists in the configured registry; this is not proof that the physical pack is genuine."})
        else:
            signals.append({"level":"high", "title":"Code not found", "detail":"No matching code exists in the local configured registry. This may be a typo or an unregistered code; investigate."})

        if previous_count >= 2 and medicine and result == "REGISTERED RECORD":
            result, risk, reason = "REVIEW REPEATED SCANS", max(risk, 65), f"This code has been scanned {previous_count + 1} times recently. Repeated scans can have legitimate causes; investigate before drawing conclusions."
            signals.append({"level":"medium", "title":"Repeated scans detected", "detail":f"{previous_count + 1} scans for this code occurred within the rolling 10-minute window. Legitimate causes are possible."})

        risk_level = "HIGH" if risk >= 80 else "MEDIUM" if risk >= 40 else "LOW"

        scanned_at = iso_now()
        conn.execute(
            "INSERT INTO scans (code,scanned_at,result,risk_score,reason) VALUES (?,?,?,?,?)",
            (code, scanned_at, result, risk, reason)
        )
        conn.commit()

    append_ledger_event("medicine_verification", session.get("portal_username", "public"), code,
                        {"result": result, "risk_score": risk, "scanned_at": scanned_at})
    return jsonify({
        "code": code,
        "result": result,
        "risk_score": risk,
        "risk_level": risk_level,
        "signals": signals,
        "reason": reason,
        "scanned_at": scanned_at,
        "medicine": record,
        "disclaimer": "A registry match does not independently prove that the physical package is genuine. Confirm product status with the manufacturer or relevant regulator; do not rely on this result alone for medication decisions."
    })

@app.post("/api/reset-history")
def reset_history():
    # Destructive maintenance action: never expose without an authenticated admin session.
    if not session.get("admin_authenticated"):
        return jsonify({"error": "admin_authentication_required"}), 401
    if not csrf_is_valid():
        return jsonify({"error": "invalid_csrf_token"}), 400
    with connect_db() as conn:
        deleted_count = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        conn.execute("DELETE FROM scans")
        conn.commit()
    # Preserve a permanent trace that history was cleared; the ledger itself is never cleared here.
    append_ledger_event("scan_history_cleared", session.get("admin_user", "admin"), None,
                        {"deleted_scan_records": deleted_count, "note": "Scan rows cleared by administrator; ledger retained."})
    audit(session.get("admin_user", "admin"), "clear_scan_history", None,
          f"Cleared {deleted_count} scan history records; ledger retained")
    return jsonify({"ok": True, "message": f"Cleared {deleted_count} scan history records. Ledger and medicine records were retained."})


# --- Operational registry management (admin session required for changes) ---
def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped

def audit(actor, action, code, details):
    with connect_db() as conn:
        conn.execute("INSERT INTO audit_log(actor,action,entity_code,details,created_at) VALUES(?,?,?,?,?)",
                     (actor, action, code, details[:1000], iso_now()))
        conn.commit()
    append_ledger_event(action, actor, code, {"details": details[:1000]})

def valid_medicine_payload(data):
    fields = {k: str(data.get(k, "")).strip() for k in ("code", "name", "manufacturer", "batch", "expiry", "status")}
    fields["code"] = fields["code"].upper()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", fields["code"]):
        return None, "Code must be 3–80 characters and contain only letters, numbers, dots, underscores, or hyphens."
    for key in ("name", "manufacturer", "batch", "expiry", "status"):
        if not fields[key]: return None, f"{key.title()} is required."
    try: datetime.strptime(fields["expiry"], "%Y-%m-%d")
    except ValueError: return None, "Expiry must use YYYY-MM-DD format."
    if fields["status"] not in ("registered", "suspected_clone", "suspended", "recalled"):
        return None, "Status must be registered, suspended, recalled, or suspected_clone."
    return fields, None

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if not csrf_is_valid():
            return "Invalid or missing CSRF token", 400
        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear(); session["admin_authenticated"] = True; session["admin_user"] = "admin"
            session["permanent"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            audit("admin", "login", None, "Administrator signed in")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid password. Please try again.", "error")
    return render_template("admin_login.html", csrf_token=session.get("csrf_token", ""))

@app.post("/admin/logout")
@admin_required
def admin_logout():
    if not csrf_is_valid():
        return "Invalid CSRF token", 400
    audit("admin", "logout", None, "Administrator signed out")
    session.clear()
    return redirect(url_for("admin_login"))

@app.get("/admin")
@admin_required
def admin_dashboard():
    with connect_db() as conn:
        rows = conn.execute("SELECT code,name,manufacturer,batch,expiry,status FROM medicines ORDER BY name").fetchall()
        logs = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 15").fetchall()
        users = conn.execute("SELECT id,username,role,organization,approved,created_at FROM users ORDER BY approved, created_at DESC LIMIT 200").fetchall()
    return render_template("admin.html", medicines=[dict(r) for r in rows], logs=[dict(r) for r in logs], users=[dict(r) for r in users], csrf_token=session.get("csrf_token", ""))

@app.post("/admin/medicines")
@admin_required
def admin_create_medicine():
    if not csrf_is_valid():
        return "Invalid CSRF token", 400
    fields, error = valid_medicine_payload(request.form)
    if error:
        flash(error, "error"); return redirect(url_for("admin_dashboard"))
    try:
        with connect_db() as conn:
            conn.execute("INSERT INTO medicines(code,name,manufacturer,batch,expiry,status) VALUES(?,?,?,?,?,?)", tuple(fields.values()))
            conn.commit()
    except sqlite3.IntegrityError:
        flash("That medicine code already exists. Use a unique code.", "error"); return redirect(url_for("admin_dashboard"))
    audit("admin", "create_medicine", fields["code"], f"Created registry record for {fields['name']}")
    flash("Medicine record added.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/medicines/<code>/update")
@admin_required
def admin_update_medicine(code):
    if not csrf_is_valid():
        return "Invalid CSRF token", 400
    fields, error = valid_medicine_payload({**request.form, "code": code})
    if error:
        flash(error, "error"); return redirect(url_for("admin_dashboard"))
    with connect_db() as conn:
        cur = conn.execute("UPDATE medicines SET name=?,manufacturer=?,batch=?,expiry=?,status=? WHERE code=?",
                           (fields["name"], fields["manufacturer"], fields["batch"], fields["expiry"], fields["status"], code))
        conn.commit()
    if not cur.rowcount:
        flash("Medicine code not found.", "error")
    else:
        audit("admin", "update_medicine", code, f"Updated registry record for {fields['name']}")
        flash("Medicine record updated.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/medicines/<code>/delete")
@admin_required
def admin_delete_medicine(code):
    if not csrf_is_valid():
        return "Invalid CSRF token", 400
    with connect_db() as conn:
        cur = conn.execute("DELETE FROM medicines WHERE code=?", (code,)); conn.commit()
    if cur.rowcount:
        audit("admin", "delete_medicine", code, "Deleted registry record")
        flash("Medicine record deleted.", "success")
    else: flash("Medicine code not found.", "error")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/<int:user_id>/approval")
@admin_required
def admin_user_approval(user_id):
    if not csrf_is_valid():
        return "Invalid or missing CSRF token", 400
    action = request.form.get("action", "")
    if action not in ("approve", "revoke"):
        return "Invalid account action", 400
    approved = 1 if action == "approve" else 0
    with connect_db() as conn:
        user = conn.execute("SELECT username, role FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            flash("Account not found.", "error")
            return redirect(url_for("admin_dashboard"))
        if user["role"] == "customer" and action == "revoke":
            # Customer access can still be revoked by an administrator when necessary.
            pass
        conn.execute("UPDATE users SET approved=? WHERE id=?", (approved, user_id))
        conn.commit()
    audit("admin", "approve_user" if approved else "revoke_user", None, f"{action.title()}d account {user['username']} ({user['role']})")
    flash(f"Account {user['username']} { 'approved' if approved else 'access revoked' }.", "success")
    return redirect(url_for("admin_dashboard"))

@app.get("/admin/export.csv")
@admin_required
def admin_export_csv():
    with connect_db() as conn:
        rows = conn.execute("SELECT code,name,manufacturer,batch,expiry,status FROM medicines ORDER BY code").fetchall()
    import io
    text_stream = io.StringIO(newline="")
    writer = csv.writer(text_stream); writer.writerow(["code","name","manufacturer","batch","expiry","status"])
    for row in rows: writer.writerow([row[k] for k in ("code","name","manufacturer","batch","expiry","status")])
    audit("admin", "export_csv", None, f"Exported {len(rows)} registry records")
    return Response(text_stream.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=truedose-registry.csv"})

@app.post("/admin/import.csv")
@admin_required
def admin_import_csv():
    if not csrf_is_valid():
        return "Invalid CSRF token", 400
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename.lower().endswith(".csv"):
        flash("Choose a CSV file.", "error"); return redirect(url_for("admin_dashboard"))
    try:
        text_data = uploaded.stream.read().decode("utf-8-sig")
        import io
        reader = csv.DictReader(io.StringIO(text_data, newline=""))
        required = {"code","name","manufacturer","batch","expiry","status"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("CSV must contain: code, name, manufacturer, batch, expiry, status.")
        rows = []; errors = []
        for line_no, row in enumerate(reader, start=2):
            fields, error = valid_medicine_payload(row)
            if error: errors.append(f"Line {line_no}: {error}")
            else: rows.append(fields)
        if errors: raise ValueError(" ".join(errors[:4]))
        with connect_db() as conn:
            for fields in rows:
                conn.execute("INSERT INTO medicines(code,name,manufacturer,batch,expiry,status) VALUES(?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,manufacturer=excluded.manufacturer,batch=excluded.batch,expiry=excluded.expiry,status=excluded.status", tuple(fields.values()))
            conn.commit()
        audit("admin", "import_csv", None, f"Imported/upserted {len(rows)} registry records")
        flash(f"Imported or updated {len(rows)} registry records.", "success")
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        flash(f"CSV import failed: {exc}", "error")
    return redirect(url_for("admin_dashboard"))

@app.get("/api/audit")
def public_audit():
    # Expose scan history only; administrative audit details remain private.
    with connect_db() as conn:
        rows = conn.execute("SELECT id,code,scanned_at,result,risk_score,reason FROM scans ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/history")
def verification_history_page():
    """Searchable verification history; limited fields, paginated, and read-only."""
    query = request.args.get("q", "").strip()[:80]
    result_filter = request.args.get("result", "").strip()[:50]
    try:
        page = max(1, min(int(request.args.get("page", "1")), 100000))
    except ValueError:
        page = 1
    page_size = 50
    where = []
    params = []
    if query:
        where.append("(code LIKE ? OR reason LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    if result_filter:
        where.append("result = ?")
        params.append(result_filter)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with connect_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM scans" + clause, params).fetchone()[0]
        rows = conn.execute("SELECT id,code,scanned_at,result,risk_score,reason FROM scans" + clause +
                            " ORDER BY id DESC LIMIT ? OFFSET ?", params + [page_size, (page-1)*page_size]).fetchall()
        result_values = conn.execute("SELECT DISTINCT result FROM scans ORDER BY result").fetchall()
    return render_template("history.html", rows=[dict(r) for r in rows], query=query, result_filter=result_filter,
                           results=[r[0] for r in result_values], total=total, page=page, page_size=page_size,
                           pages=max(1, (total + page_size - 1)//page_size))

@app.get("/history/export.csv")
def export_verification_history():
    """Export the same public-safe scan fields available in the history view."""
    query = request.args.get("q", "").strip()[:80]
    result_filter = request.args.get("result", "").strip()[:50]
    where = []
    params = []
    if query:
        where.append("(code LIKE ? OR reason LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    if result_filter:
        where.append("result = ?")
        params.append(result_filter)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with connect_db() as conn:
        rows = conn.execute("SELECT id,code,scanned_at,result,risk_score,reason FROM scans" + clause + " ORDER BY id DESC", params).fetchall()
    output = __import__("io").StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["id", "code", "scanned_at", "result", "risk_score", "reason"])
    for row in rows:
        writer.writerow([row[k] for k in ("id", "code", "scanned_at", "result", "risk_score", "reason")])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=truedose-verification-history.csv"})

@app.get("/ledger")
def ledger_page():
    """Human-readable, public-safe view of the append-only hash-linked ledger."""
    try:
        page = max(1, min(int(request.args.get("page", "1")), 100000))
    except ValueError:
        page = 1
    page_size = 50
    with connect_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        rows = conn.execute("SELECT id,event_type,entity_code,previous_hash,block_hash,created_at FROM ledger ORDER BY id DESC LIMIT ? OFFSET ?",
                            (page_size, (page-1)*page_size)).fetchall()
    chain = verify_ledger_chain()
    return render_template("ledger.html", events=[dict(r) for r in rows], chain=chain, total=total, page=page,
                           pages=max(1, (total+page_size-1)//page_size), page_size=page_size)

@app.get("/ledger/export.json")
@admin_required
def export_ledger_json():
    chain = verify_ledger_chain()
    with connect_db() as conn:
        rows = conn.execute("SELECT id,event_type,entity_code,payload,previous_hash,block_hash,created_at FROM ledger ORDER BY id").fetchall()
    return jsonify({"ledger_type": "single_server_hash_linked_ledger", "distributed_blockchain": False,
                    "integrity": chain, "events": [dict(r) for r in rows]})

@app.get("/admin/audit-history")
@admin_required
def admin_audit_history():
    query = request.args.get("q", "").strip()[:100]
    try:
        page = max(1, min(int(request.args.get("page", "1")), 100000))
    except ValueError:
        page = 1
    page_size = 50
    where = " WHERE actor LIKE ? OR action LIKE ? OR COALESCE(entity_code,'') LIKE ? OR details LIKE ?" if query else ""
    params = [f"%{query}%"]*4 if query else []
    with connect_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log" + where, params).fetchone()[0]
        rows = conn.execute("SELECT * FROM audit_log" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", params+[page_size,(page-1)*page_size]).fetchall()
    return render_template("audit_history.html", rows=[dict(r) for r in rows], query=query, total=total, page=page,
                           pages=max(1,(total+page_size-1)//page_size), csrf_token=session.get("csrf_token", ""))

@app.get("/admin/audit-history/export.csv")
@admin_required
def export_admin_audit_history():
    with connect_db() as conn:
        rows = conn.execute("SELECT id,actor,action,entity_code,details,created_at FROM audit_log ORDER BY id DESC").fetchall()
    output = __import__("io").StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["id","actor","action","entity_code","details","created_at"])
    for row in rows:
        writer.writerow([row[k] for k in ("id","actor","action","entity_code","details","created_at")])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=truedose-admin-audit.csv"})

# --- Role-based portals and tamper-evident ledger ---
PORTAL_ROLES = {
    "manufacturer": {"title": "Manufacturer workspace", "description": "Manage product and batch records and review their traceability events."},
    "distributor": {"title": "Distributor workspace", "description": "Review registered batches and record custody/dispatch events."},
    "pharmacist": {"title": "Pharmacist workspace", "description": "Verify product codes and review safety flags before dispensing."},
    "customer": {"title": "Customer verification", "description": "Check a medicine code and review its recorded traceability history."},
}

def portal_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("portal_user"):
            return redirect(url_for("portal_login", next=request.path))
        with connect_db() as conn:
            current_user = conn.execute("SELECT approved, role FROM users WHERE username=?", (session["portal_user"],)).fetchone()
        if not current_user or not current_user["approved"] or current_user["role"] != session.get("portal_role"):
            session.clear()
            flash("Your account is not active. Please sign in again or contact the administrator.", "error")
            return redirect(url_for("portal_login"))
        return fn(*args, **kwargs)
    return wrapped

@app.route("/register", methods=["GET", "POST"])
def portal_register():
    if request.method == "POST":
        if not csrf_is_valid():
            return "Invalid or missing CSRF token", 400
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "customer")
        organization = request.form.get("organization", "").strip()[:120]
        if not re.fullmatch(r"[a-z0-9_.-]{3,40}", username):
            flash("Username must be 3–40 characters: lowercase letters, numbers, dot, underscore or hyphen.", "error")
        elif len(password) < 12:
            flash("Use a password with at least 12 characters.", "error")
        elif role not in PORTAL_ROLES:
            flash("Choose a valid account type.", "error")
        else:
            try:
                with connect_db() as conn:
                    approved = 1 if role == "customer" else 0
                    conn.execute("INSERT INTO users(username,password_hash,role,organization,approved,created_at) VALUES(?,?,?,?,?,?)",
                                 (username, generate_password_hash(password), role, organization, approved, iso_now()))
                    conn.commit()
                append_ledger_event("account_registered", username, None, {"role": role, "organization": organization})
                flash("Customer account created. Your account is ready." if role == "customer" else "Account created. An administrator must approve this organization role before sign-in.", "success")
                return redirect(url_for("portal_login"))
            except sqlite3.IntegrityError:
                flash("That username is already in use.", "error")
    return render_template("portal_auth.html", mode="register", csrf_token=session.get("csrf_token", ""))

@app.route("/login", methods=["GET", "POST"])
def portal_login():
    if request.method == "POST":
        if not csrf_is_valid():
            return "Invalid or missing CSRF token", 400
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        with connect_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            if not user["approved"]:
                flash("This account is awaiting administrator approval. Please contact the TrueDose administrator.", "error")
                return render_template("portal_auth.html", mode="login", csrf_token=session.get("csrf_token", ""))
            session.clear()
            session["portal_user"] = user["username"]
            session["permanent"] = True
            session["portal_role"] = user["role"]
            session["portal_organization"] = user["organization"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            append_ledger_event("account_login", user["username"], None, {"role": user["role"]})
            return redirect(url_for("portal_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("portal_auth.html", mode="login", csrf_token=session.get("csrf_token", ""))

@app.post("/logout")
@portal_required
def portal_logout():
    if not csrf_is_valid():
        return "Invalid CSRF token", 400
    append_ledger_event("account_logout", session["portal_user"], None, {"role": session["portal_role"]})
    session.clear()
    return redirect(url_for("portal_login"))

@app.get("/portal")
@portal_required
def portal_dashboard():
    role = session["portal_role"]
    with connect_db() as conn:
        if role == "manufacturer":
            medicines = conn.execute("SELECT code,name,manufacturer,batch,expiry,status FROM medicines WHERE lower(manufacturer)=lower(?) ORDER BY name LIMIT 100", (session.get("portal_organization", ""),)).fetchall()
        else:
            medicines = conn.execute("SELECT code,name,manufacturer,batch,expiry,status FROM medicines ORDER BY name LIMIT 100").fetchall()
        scans = conn.execute("SELECT code,scanned_at,result,risk_score,reason FROM scans ORDER BY id DESC LIMIT 12").fetchall()
        events = conn.execute("SELECT id,event_type,actor,entity_code,previous_hash,block_hash,created_at FROM ledger ORDER BY id DESC LIMIT 15").fetchall()
    return render_template("portal_dashboard.html", role=role, role_info=PORTAL_ROLES[role], username=session["portal_user"], organization=session.get("portal_organization", ""), medicines=[dict(x) for x in medicines], scans=[dict(x) for x in scans], events=[dict(x) for x in events], chain=verify_ledger_chain(), csrf_token=session.get("csrf_token", ""))

@app.post("/portal/trace-event")
@portal_required
def portal_trace_event():
    role = session["portal_role"]
    if role not in ("manufacturer", "distributor"):
        return jsonify({"error": "This role cannot record supply-chain custody events."}), 403
    if not csrf_is_valid():
        return "Invalid CSRF token", 400
    code = request.form.get("code", "").strip().upper()
    event_type = request.form.get("event_type", "").strip()
    details = request.form.get("details", "").strip()[:500]
    allowed_events = {
        "manufacturer": {"batch_released", "custody_transfer"},
        "distributor": {"shipment_dispatched", "shipment_received", "custody_transfer"},
    }
    if event_type not in allowed_events[role] or not details or not code:
        flash("Choose an event permitted for your role and provide event details.", "error")
        return redirect(url_for("portal_dashboard"))
    with connect_db() as conn:
        medicine = conn.execute("SELECT code, manufacturer FROM medicines WHERE code=?", (code,)).fetchone()
    if not medicine:
        flash("That code is not in the medicine registry. Ask an administrator to register it first.", "error")
        return redirect(url_for("portal_dashboard"))
    if role == "manufacturer":
        org = session.get("portal_organization", "").strip()
        if not org or medicine["manufacturer"].strip().casefold() != org.casefold():
            flash("Manufacturers may only release or transfer records assigned to their exact registered organization name.", "error")
            return redirect(url_for("portal_dashboard"))
    append_ledger_event(event_type, session["portal_user"], code, {"role": role, "organization": session.get("portal_organization", ""), "details": details})
    flash("Traceability event added to the hash-linked ledger.", "success")
    return redirect(url_for("portal_dashboard"))

@app.get("/api/ledger/status")
def ledger_status():
    return jsonify(verify_ledger_chain())

@app.get("/api/ledger")
def ledger_events():
    with connect_db() as conn:
        rows = conn.execute("SELECT id,event_type,entity_code,previous_hash,block_hash,created_at FROM ledger ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"chain": verify_ledger_chain(), "events": [dict(r) for r in rows]})

# Initialize the database for both direct Flask execution and WSGI servers.
init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
