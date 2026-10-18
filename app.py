from flask import Flask, render_template, request, jsonify
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "truedose.db"
app = Flask(__name__)

SAMPLE_MEDICINES = [
    ("TD-1001", "Paracetamol 500 mg", "Demo Pharma Ltd.", "BATCH-PARA-101", "2027-12-31", "registered"),
    ("TD-1002", "Cetirizine 10 mg", "Demo Pharma Ltd.", "BATCH-CET-204", "2027-06-30", "registered"),
    ("TD-EXPIRED-01", "Amoxicillin 250 mg", "Example Labs", "BATCH-AMOX-009", "2024-02-01", "registered"),
    ("TD-CLONE-1001", "Paracetamol 500 mg", "Unknown source", "BATCH-PARA-101", "2027-12-31", "clone_demo"),
]

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
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                scanned_at TEXT NOT NULL,
                result TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                reason TEXT NOT NULL
            )
        """)
        count = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO medicines (code,name,manufacturer,batch,expiry,status) VALUES (?,?,?,?,?,?)",
                SAMPLE_MEDICINES
            )
        conn.commit()

def iso_now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

@app.route("/")
def index():
    return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "app": "TrueDose AI", "database": "connected"})

@app.get("/api/stats")
def stats():
    with connect_db() as conn:
        total_medicines = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
        total_scans = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        flagged = conn.execute("SELECT COUNT(*) FROM scans WHERE result != 'VERIFIED RECORD'").fetchone()[0]
        recent = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 6").fetchall()
    return jsonify({
        "medicine_records": total_medicines,
        "total_scans": total_scans,
        "flagged_scans": flagged,
        "recent_scans": [dict(row) for row in recent]
    })

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

    with connect_db() as conn:
        medicine = conn.execute("SELECT * FROM medicines WHERE code = ?", (code,)).fetchone()
        # Use a consistently formatted local ISO timestamp for the demo window.
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc).astimezone() - timedelta(minutes=10)).isoformat(timespec="seconds")
        previous_count = conn.execute(
            "SELECT COUNT(*) FROM scans WHERE code = ? AND scanned_at >= ?",
            (code, cutoff)
        ).fetchone()[0]

        result, risk, reason = "UNKNOWN CODE", 85, "This code is not in the demo registry."
        record = None
        if medicine:
            record = dict(medicine)
            try:
                expiry_date = datetime.strptime(medicine["expiry"], "%Y-%m-%d").date()
            except ValueError:
                expiry_date = None

            if medicine["status"] == "clone_demo":
                result, risk, reason = "SUSPICIOUS", 95, "Demo scenario: this code represents a suspected cloned serial linked to a known batch."
            elif expiry_date and expiry_date < datetime.now().date():
                result, risk, reason = "EXPIRED BATCH", 90, "The recorded expiry date has passed. Do not use; ask a pharmacist."
            else:
                result, risk, reason = "REGISTERED RECORD", 5, "A matching record was found in the local demo registry. This does not prove the physical pack is genuine."

        if previous_count >= 2 and medicine and medicine["status"] != "clone_demo":
            result, risk, reason = "REVIEW REPEATED SCANS", max(risk, 65), f"This code has been scanned {previous_count + 1} times recently. Repeated scans can have legitimate causes; investigate before drawing conclusions."

        scanned_at = iso_now()
        conn.execute(
            "INSERT INTO scans (code,scanned_at,result,risk_score,reason) VALUES (?,?,?,?,?)",
            (code, scanned_at, result, risk, reason)
        )
        conn.commit()

    return jsonify({
        "code": code,
        "result": result,
        "risk_score": risk,
        "reason": reason,
        "scanned_at": scanned_at,
        "medicine": record,
        "disclaimer": "Prototype only. Data is simulated; this tool cannot certify medicine authenticity or replace a pharmacist or regulator."
    })

@app.post("/api/reset-demo")
def reset_demo():
    with connect_db() as conn:
        conn.execute("DELETE FROM scans")
        conn.commit()
    return jsonify({"ok": True, "message": "Demo scan history cleared. Medicine records were kept."})

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000)
