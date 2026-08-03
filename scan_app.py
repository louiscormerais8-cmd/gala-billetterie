"""
Application locale de controle d'acces (scan des billets QR).
Usage: py scan_app.py
Puis ouvrir l'URL affichee dans la console depuis un telephone
connecte au meme reseau Wi-Fi que cet ordinateur.
"""
import base64
import csv
import io
import os
import socket
import sqlite3
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")

app = Flask(__name__)
qr_detector = cv2.QRCodeDetector()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


PAGE = """
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Controle d'acces - Gala</title>
<style>
  body { font-family: -apple-system, Arial, sans-serif; background: #111; color: #fff; margin: 0; text-align: center; }
  #video { width: 100%; max-width: 480px; border-radius: 8px; }
  #result { font-size: 1.4rem; font-weight: bold; padding: 20px; margin: 10px auto; max-width: 480px; border-radius: 8px; min-height: 60px; }
  .ok { background: #1e7e34; }
  .dup { background: #b8860b; }
  .invalid { background: #b02a2a; }
  .idle { background: #333; }
  #stats { margin-top: 10px; font-size: 0.9rem; color: #aaa; }
  a { color: #6cf; }
</style>
</head>
<body>
  <h2>Controle d'acces</h2>
  <video id="video" autoplay playsinline muted></video>
  <canvas id="canvas" style="display:none;"></canvas>
  <div id="result" class="idle">En attente de scan...</div>
  <div id="stats">Chargement...</div>
  <p><a href="/export">Telecharger la liste de presence (CSV)</a></p>

<script>
const video = document.getElementById('video');
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const resultDiv = document.getElementById('result');
const statsDiv = document.getElementById('stats');
let busy = false;

navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
  .then(stream => { video.srcObject = stream; })
  .catch(err => { resultDiv.textContent = "Erreur camera: " + err.message; });

function refreshStats() {
  fetch('/stats').then(r => r.json()).then(d => {
    statsDiv.textContent = `Entrees : ${d.checked_in} / ${d.total}`;
  });
}
refreshStats();
setInterval(refreshStats, 4000);

function captureAndSend() {
  if (busy || video.videoWidth === 0) return;
  busy = true;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const dataUrl = canvas.toDataURL('image/jpeg', 0.7);

  fetch('/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image: dataUrl })
  })
  .then(r => r.json())
  .then(d => {
    if (d.status === 'none') {
      busy = false;
      return;
    }
    if (d.status === 'ok') {
      resultDiv.className = 'ok';
      resultDiv.textContent = `Bienvenue ${d.prenom} ${d.nom} (${d.categorie})`;
      refreshStats();
    } else if (d.status === 'duplicate') {
      resultDiv.className = 'dup';
      resultDiv.textContent = `DEJA SCANNE : ${d.prenom} ${d.nom} a ${d.checked_in_at}`;
    } else {
      resultDiv.className = 'invalid';
      resultDiv.textContent = 'Billet invalide';
    }
    setTimeout(() => { resultDiv.className = 'idle'; resultDiv.textContent = 'En attente de scan...'; busy = false; }, 2500);
  })
  .catch(() => { busy = false; });
}

setInterval(captureAndSend, 800);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/scan", methods=["POST"])
def scan():
    data = request.get_json(silent=True) or {}
    image_data = data.get("image", "")
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    try:
        raw = base64.b64decode(image_data)
    except (ValueError, TypeError):
        return jsonify({"status": "none"})

    img_array = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"status": "none"})

    payload, _, _ = qr_detector.detectAndDecode(img)
    if not payload:
        return jsonify({"status": "none"})

    token = payload.split("GALA:", 1)[-1].strip()
    conn = get_db()
    guest = conn.execute("SELECT * FROM guests WHERE token = ?", (token,)).fetchone()

    if not guest:
        conn.close()
        return jsonify({"status": "invalid"})

    if guest["checked_in"]:
        conn.close()
        return jsonify({
            "status": "duplicate",
            "prenom": guest["prenom"],
            "nom": guest["nom"],
            "checked_in_at": guest["checked_in_at"],
        })

    now = datetime.now().strftime("%H:%M:%S")
    conn.execute(
        "UPDATE guests SET checked_in = 1, checked_in_at = ? WHERE id = ?",
        (now, guest["id"]),
    )
    conn.commit()
    conn.close()
    return jsonify({
        "status": "ok",
        "prenom": guest["prenom"],
        "nom": guest["nom"],
        "categorie": guest["categorie"],
    })


@app.route("/stats")
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM guests").fetchone()[0]
    checked_in = conn.execute("SELECT COUNT(*) FROM guests WHERE checked_in = 1").fetchone()[0]
    conn.close()
    return jsonify({"total": total, "checked_in": checked_in})


@app.route("/export")
def export():
    conn = get_db()
    rows = conn.execute(
        "SELECT prenom, nom, categorie, checked_in, checked_in_at FROM guests ORDER BY nom, prenom"
    ).fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["prenom", "nom", "categorie", "present", "heure_entree"])
    for r in rows:
        writer.writerow([r["prenom"], r["nom"], r["categorie"], "oui" if r["checked_in"] else "non", r["checked_in_at"] or ""])

    mem = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="presence_gala.csv")


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print("Base de donnees introuvable. Lancez d'abord : py generate_tickets.py")
    ip = local_ip()
    print("=" * 60)
    print(f"Ouvrez sur ce PC        : https://127.0.0.1:5000")
    print(f"Ouvrez depuis un mobile : https://{ip}:5000  (meme Wi-Fi)")
    print("Le certificat est auto-signe : acceptez l'avertissement du navigateur.")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, ssl_context="adhoc", debug=False)
