import os
import sys
import sqlite3
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# Port configuration (default 5003)
PORT = int(os.environ.get("PORTAL_PORT", "5003"))

# Database path
DB_PATH = os.environ.get("PASTE_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pastes.db"))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pastes (
            id TEXT PRIMARY KEY,
            ciphertext TEXT,
            iv TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# --- BACKGROUND SWEEPER DAEMON ---

def expired_pastes_sweeper():
    """Background worker that removes expired secrets from database every 60 seconds."""
    while True:
        try:
            time.sleep(60)
            conn = get_db_connection()
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("DELETE FROM pastes WHERE expires_at < ?", (now,))
            deleted = cursor.rowcount
            if deleted > 0:
                print(f"[*] Background Sweeper: Purged {deleted} expired secret links.")
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ERROR] Sweeper thread exception: {e}", file=sys.stderr)

# --- WEB UI VIEWS ---

@app.route('/')
def index():
    """Main paste creation UI."""
    return render_template("index.html")

@app.route('/view/<paste_id>')
def view_paste_page(paste_id):
    """Secure paste decryption UI wrapper."""
    return render_template("index.html")

# --- SECURE API ENDPOINTS ---

@app.route('/api/pastes', methods=['POST'])
def store_paste():
    data = request.get_json() or {}
    ciphertext = data.get("ciphertext")
    iv = data.get("iv")
    ttl = data.get("ttl", 3600)  # Default to 1 hour
    
    if not ciphertext or not iv:
        return jsonify({"error": "Missing encrypted ciphertext or iv payload"}), 400
        
    # Limit TTL to 1 day maximum for security
    ttl = min(int(ttl), 86400)
    
    # Generate 16-character secure URL token
    paste_id = secrets.token_urlsafe(12)
    
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO pastes (id, ciphertext, iv, expires_at) VALUES (?, ?, ?, ?)",
            (paste_id, ciphertext, iv, expires_at)
        )
        conn.commit()
        print(f"[PASTE CREATED] Saved secret {paste_id} (Expires: {expires_at})")
        return jsonify({"status": "success", "paste_id": paste_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/pastes/<paste_id>', methods=['GET'])
def get_and_burn_paste(paste_id):
    """Retrieves ciphertext and IV, then deletes it from the database immediately (Burn on Read)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Fetch paste
    cursor.execute("SELECT * FROM pastes WHERE id = ?", (paste_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return jsonify({"error": "Secret not found or has already been burned."}), 404
        
    # Check expiration
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        # Delete expired paste
        cursor.execute("DELETE FROM pastes WHERE id = ?", (paste_id,))
        conn.commit()
        conn.close()
        return jsonify({"error": "Secret link has expired."}), 410
        
    # Delete immediately (One-Time View Policy)
    cursor.execute("DELETE FROM pastes WHERE id = ?", (paste_id,))
    conn.commit()
    conn.close()
    
    print(f"[PASTE BURNED] Decrypted and wiped secret {paste_id} from database")
    return jsonify({
        "ciphertext": row["ciphertext"],
        "iv": row["iv"]
    })

if __name__ == '__main__':
    # Initialize SQLite database
    init_db()
    
    # Start background cleanup thread as daemon
    sweeper_thread = threading.Thread(target=expired_pastes_sweeper, daemon=True)
    sweeper_thread.start()
    
    print(f"[*] PasteSafe Portal running on http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
