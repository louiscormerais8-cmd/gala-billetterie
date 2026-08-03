"""
Site de billetterie du gala : page d'achat, paiement Stripe, envoi automatique
du billet par email, page d'administration et scan d'entree.

Variables d'environnement requises (voir .env.example) :
  STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, BREVO_API_KEY, SENDER_EMAIL,
  ADMIN_PASSWORD, BASE_URL
"""
import base64
import csv
import io
import os
from datetime import datetime
from functools import wraps

import stripe
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, request, send_file

load_dotenv()

import db
import email_utils
from tickets import build_ticket_pdf_bytes, make_qr_image  # noqa: F401 (make_qr_image used by generate_tickets.py callers)

try:
    import cv2
    import numpy as np
    SCAN_ENABLED = True
except ImportError:
    SCAN_ENABLED = False

app = Flask(__name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "gala")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
EVENT_NAME = os.environ.get("EVENT_NAME", "Gala 2026")

# Prix des categories de billet, en centimes d'euro.
TICKET_CATEGORIES = {
    "Standard": 2500,
    "VIP": 5000,
}

qr_detector = cv2.QRCodeDetector() if SCAN_ENABLED else None

db.init_db()


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.authorization
        if not ADMIN_PASSWORD or not auth or auth.username != ADMIN_USER or auth.password != ADMIN_PASSWORD:
            return Response(
                "Authentification requise", 401,
                {"WWW-Authenticate": 'Basic realm="Administration Gala"'},
            )
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Page publique d'achat
# ---------------------------------------------------------------------------

def purchase_page(error=None):
    options = "".join(
        f'<option value="{cat}">{cat} - {price / 100:.2f} EUR</option>'
        for cat, price in TICKET_CATEGORIES.items()
    )
    error_html = f'<p class="error">{error}</p>' if error else ""
    return f"""
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{EVENT_NAME} - Billetterie</title>
<style>
  body {{ font-family: -apple-system, Arial, sans-serif; background: #0d0d12; color: #f2f2f2;
         display: flex; justify-content: center; padding: 40px 16px; }}
  .card {{ background: #1a1a22; padding: 32px; border-radius: 12px; max-width: 420px; width: 100%; }}
  h1 {{ font-size: 1.5rem; margin-top: 0; }}
  label {{ display: block; margin: 14px 0 6px; font-size: 0.9rem; color: #ccc; }}
  input, select {{ width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #444;
                   background: #111; color: #fff; box-sizing: border-box; }}
  button {{ margin-top: 22px; width: 100%; padding: 12px; border: none; border-radius: 6px;
           background: #635bff; color: #fff; font-size: 1rem; cursor: pointer; }}
  button:hover {{ background: #5147e5; }}
  .error {{ color: #ff6b6b; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{EVENT_NAME}</h1>
    <p>Reservez votre billet nominatif. Vous recevrez votre billet (QR code) par email juste apres paiement.</p>
    {error_html}
    <form method="post" action="/acheter">
      <label>Prenom</label>
      <input type="text" name="prenom" required>
      <label>Nom</label>
      <input type="text" name="nom" required>
      <label>Email</label>
      <input type="email" name="email" required>
      <label>Categorie</label>
      <select name="categorie">{options}</select>
      <button type="submit">Payer et recevoir mon billet</button>
    </form>
  </div>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(purchase_page(), mimetype="text/html")


@app.route("/acheter", methods=["POST"])
def acheter():
    prenom = request.form.get("prenom", "").strip()
    nom = request.form.get("nom", "").strip()
    email = request.form.get("email", "").strip()
    categorie = request.form.get("categorie", "").strip()

    if not prenom or not nom or not email or categorie not in TICKET_CATEGORIES:
        return Response(purchase_page("Merci de remplir tous les champs correctement."), mimetype="text/html")

    price_cents = TICKET_CATEGORIES[categorie]

    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=email,
        line_items=[{
            "price_data": {
                "currency": "eur",
                "product_data": {"name": f"{EVENT_NAME} - Billet {categorie}"},
                "unit_amount": price_cents,
            },
            "quantity": 1,
        }],
        metadata={"prenom": prenom, "nom": nom, "categorie": categorie},
        success_url=f"{BASE_URL}/succes?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BASE_URL}/annule",
    )
    return redirect(session.url, code=303)


@app.route("/succes")
def succes():
    return Response(
        """
        <!doctype html><html lang="fr"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Paiement reussi</title>
        <style>body{font-family:Arial,sans-serif;background:#0d0d12;color:#f2f2f2;
        text-align:center;padding:60px 16px;}</style></head><body>
        <h1>Paiement recu, merci !</h1>
        <p>Votre billet avec QR code vous est envoye par email d'ici quelques instants.</p>
        <p>Pensez a verifier vos spams si vous ne le voyez pas.</p>
        </body></html>
        """,
        mimetype="text/html",
    )


@app.route("/annule")
def annule():
    return Response(
        """
        <!doctype html><html lang="fr"><head><meta charset="utf-8">
        <title>Paiement annule</title></head>
        <body style="font-family:Arial,sans-serif;text-align:center;padding:60px 16px;">
        <h1>Paiement annule</h1><p><a href="/">Retour a la billetterie</a></p>
        </body></html>
        """,
        mimetype="text/html",
    )


# ---------------------------------------------------------------------------
# Webhook Stripe : fulfillment (creation du billet + envoi email)
# ---------------------------------------------------------------------------

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return Response(status=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        fulfill_order(session)

    return jsonify({"received": True})


def sget(obj, key, default=None):
    """Acces type dict compatible avec les objets StripeObject (qui n'exposent pas .get())."""
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default


def fulfill_order(session):
    conn = db.get_db()
    if db.find_guest_by_session(conn, session["id"]):
        conn.close()
        return  # deja traite (Stripe peut renvoyer le meme evenement plusieurs fois)

    metadata = sget(session, "metadata", {}) or {}
    prenom = sget(metadata, "prenom", "Invite")
    nom = sget(metadata, "nom", "")
    categorie = sget(metadata, "categorie", "Standard")
    customer_details = sget(session, "customer_details", {}) or {}
    email = sget(customer_details, "email") or sget(session, "customer_email")
    amount_cents = sget(session, "amount_total", 0)

    token = os.urandom(8).hex()
    db.create_guest(
        conn, prenom, nom, email, categorie, token,
        amount_cents=amount_cents, payment_status="paye", stripe_session_id=session["id"],
    )
    conn.close()

    pdf_bytes = build_ticket_pdf_bytes(prenom, nom, categorie, token)
    if email:
        email_utils.send_ticket_email(email, prenom, nom, categorie, pdf_bytes)


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------

@app.route("/admin")
@require_admin
def admin():
    conn = db.get_db()
    guests = db.list_guests(conn)
    s = db.stats(conn)
    conn.close()

    rows = "".join(
        f"<tr><td>{g['prenom']} {g['nom']}</td><td>{g['email'] or ''}</td>"
        f"<td>{g['categorie']}</td><td>{g['payment_status']}</td>"
        f"<td>{g['amount_cents'] / 100:.2f} EUR</td>"
        f"<td>{'Oui - ' + g['checked_in_at'] if g['checked_in'] else 'Non'}</td></tr>"
        for g in guests
    )
    return Response(
        f"""
        <!doctype html><html lang="fr"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Administration - {EVENT_NAME}</title>
        <style>
          body {{ font-family: Arial, sans-serif; background:#0d0d12; color:#eee; padding:24px; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #333; padding: 8px; text-align: left; font-size: 0.9rem; }}
          th {{ background: #1a1a22; }}
          .stats {{ margin-bottom: 20px; }}
          a {{ color: #8cf; }}
        </style>
        </head><body>
        <h1>Administration - {EVENT_NAME}</h1>
        <div class="stats">
          <p>Billets vendus/crees : {s['total']} | Entrees enregistrees : {s['checked_in']} |
          Recette : {s['revenue_cents'] / 100:.2f} EUR</p>
          <p><a href="/export">Exporter la liste (CSV)</a> | <a href="/scan-app">Ouvrir le scan d'entree</a></p>
        </div>
        <table>
          <tr><th>Invite</th><th>Email</th><th>Categorie</th><th>Statut</th><th>Montant</th><th>Entree</th></tr>
          {rows}
        </table>
        </body></html>
        """,
        mimetype="text/html",
    )


@app.route("/export")
@require_admin
def export():
    conn = db.get_db()
    guests = db.list_guests(conn)
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["prenom", "nom", "email", "categorie", "statut_paiement", "montant_eur", "present", "heure_entree"])
    for g in guests:
        writer.writerow([
            g["prenom"], g["nom"], g["email"] or "", g["categorie"], g["payment_status"],
            f"{g['amount_cents'] / 100:.2f}", "oui" if g["checked_in"] else "non", g["checked_in_at"] or "",
        ])
    mem = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="presence_gala.csv")


# ---------------------------------------------------------------------------
# Scan d'entree (protege par mot de passe admin, utilisable depuis un mobile)
# ---------------------------------------------------------------------------

SCAN_PAGE = """
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Controle d'acces</title>
<style>
  body { font-family: -apple-system, Arial, sans-serif; background: #111; color: #fff; margin: 0; text-align: center; }
  #video { width: 100%; max-width: 480px; border-radius: 8px; }
  #result { font-size: 1.4rem; font-weight: bold; padding: 20px; margin: 10px auto; max-width: 480px; border-radius: 8px; min-height: 60px; }
  .ok { background: #1e7e34; } .dup { background: #b8860b; } .invalid { background: #b02a2a; } .idle { background: #333; }
  #stats { margin-top: 10px; font-size: 0.9rem; color: #aaa; }
</style>
</head>
<body>
  <h2>Controle d'acces</h2>
  <video id="video" autoplay playsinline muted></video>
  <canvas id="canvas" style="display:none;"></canvas>
  <div id="result" class="idle">En attente de scan...</div>
  <div id="stats">Chargement...</div>
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
    if (d.status === 'none') { busy = false; return; }
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


@app.route("/scan-app")
@require_admin
def scan_app_page():
    return Response(SCAN_PAGE, mimetype="text/html")


@app.route("/scan", methods=["POST"])
@require_admin
def scan():
    if not SCAN_ENABLED:
        return jsonify({"status": "invalid"})

    data = request.get_json(silent=True) or {}
    image_data = data.get("image", "")
    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    try:
        raw = base64.b64decode(image_data)
    except Exception:
        return jsonify({"status": "none"})

    img_array = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({"status": "none"})

    payload, _, _ = qr_detector.detectAndDecode(img)
    if not payload:
        return jsonify({"status": "none"})

    token = payload.split("GALA:", 1)[-1].strip()
    conn = db.get_db()
    guest = db.find_guest_by_token(conn, token)

    if not guest:
        conn.close()
        return jsonify({"status": "invalid"})

    if guest["checked_in"]:
        conn.close()
        return jsonify({
            "status": "duplicate", "prenom": guest["prenom"], "nom": guest["nom"],
            "checked_in_at": guest["checked_in_at"],
        })

    now = datetime.now().strftime("%H:%M:%S")
    db.mark_checked_in(conn, guest["id"], now)
    conn.close()
    return jsonify({
        "status": "ok", "prenom": guest["prenom"], "nom": guest["nom"], "categorie": guest["categorie"],
    })


@app.route("/stats")
@require_admin
def stats_route():
    conn = db.get_db()
    s = db.stats(conn)
    conn.close()
    return jsonify(s)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
