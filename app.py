"""
Site de billetterie du gala : page d'achat, paiement Stripe, envoi automatique
du/des billet(s) par email, page d'administration et scan d'entree.

Tarification :
- Places individuelles : prix degressif par palier selon le nombre deja vendu
  (jusqu'a 5 places par commande, un seul nom acheteur pour toutes).
- Packs Alumni (3 ou 4 places) : prix fixe, nominatif (un nom par place),
  1 pack par commande.

Variables d'environnement requises (voir .env.example) :
  STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, BREVO_API_KEY, SENDER_EMAIL,
  ADMIN_PASSWORD, BASE_URL
"""
import base64
import csv
import io
import json
import os
from datetime import datetime
from functools import wraps
from pathlib import Path

import stripe
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, request, send_file

load_dotenv(Path(__file__).resolve().parent / ".env")

import db
import email_utils
from tickets import build_multi_ticket_pdf_bytes

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
EVENT_NAME = os.environ.get("EVENT_NAME", "Gala ISMIN")

# Prix degressif des places individuelles : (nombre de places, prix en centimes).
PRICING_TIERS = [
    (10, 6000),
    (20, 6500),
    (40, 7000),
    (70, 7500),
    (45, 8000),
    (45, 8500),
]
LAST_TIER_PRICE = PRICING_TIERS[-1][1]
MAX_INDIVIDUAL_QTY = 5

PACKS = {
    "pack3": {"label": "Pack Alumni 3 places", "size": 3, "price_cents": 21000},
    "pack4": {"label": "Pack Alumni 4 places", "size": 4, "price_cents": 26000},
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


def seat_price(seat_index):
    """Prix (en centimes) de la place a la position seat_index (0-based) parmi
    toutes les places individuelles vendues depuis le debut."""
    cumulative = 0
    for count, price in PRICING_TIERS:
        if seat_index < cumulative + count:
            return price
        cumulative += count
    return LAST_TIER_PRICE


def compute_individual_prices(already_sold, qty):
    return [seat_price(already_sold + i) for i in range(qty)]


# ---------------------------------------------------------------------------
# Page publique d'achat
# ---------------------------------------------------------------------------

def purchase_page(error=None):
    error_html = f'<p class="error">{error}</p>' if error else ""
    qty_options = "".join(f'<option value="{i}">{i}</option>' for i in range(1, MAX_INDIVIDUAL_QTY + 1))

    def pack_fields(key, size):
        fields = ""
        for i in range(1, size + 1):
            fields += f"""
              <div class="pair">
                <input type="text" name="{key}_prenom{i}" placeholder="Prenom invite {i}">
                <input type="text" name="{key}_nom{i}" placeholder="Nom invite {i}">
              </div>
            """
        return fields

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
  .card {{ background: #1a1a22; padding: 32px; border-radius: 12px; max-width: 460px; width: 100%; }}
  h1 {{ font-size: 1.5rem; margin-top: 0; }}
  label {{ display: block; margin: 14px 0 6px; font-size: 0.9rem; color: #ccc; }}
  input, select {{ width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #444;
                   background: #111; color: #fff; box-sizing: border-box; }}
  .pair {{ display: flex; gap: 8px; margin-top: 8px; }}
  .pair input {{ width: 50%; }}
  .choice {{ display: block; background: #111; border: 1px solid #444; border-radius: 6px;
             padding: 10px; margin-top: 10px; cursor: pointer; }}
  .choice input {{ width: auto; margin-right: 8px; }}
  .section {{ display: none; margin-top: 10px; padding: 12px; background: #14141c; border-radius: 8px; }}
  .section.active {{ display: block; }}
  #prix-estime {{ margin-top: 10px; font-size: 0.95rem; color: #9fe; }}
  button {{ margin-top: 22px; width: 100%; padding: 12px; border: none; border-radius: 6px;
           background: #635bff; color: #fff; font-size: 1rem; cursor: pointer; }}
  button:hover {{ background: #5147e5; }}
  .error {{ color: #ff6b6b; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{EVENT_NAME}</h1>
    <p>Reservez votre/vos billet(s) nominatif(s). Vous les recevrez par email (QR code) juste apres paiement.</p>
    {error_html}
    <form method="post" action="/acheter">

      <label class="choice"><input type="radio" name="type_billet" value="individuel" checked> Place(s) individuelle(s) - prix degressif selon disponibilite</label>
      <label class="choice"><input type="radio" name="type_billet" value="pack3"> Pack Alumni 3 places - 210,00 EUR</label>
      <label class="choice"><input type="radio" name="type_billet" value="pack4"> Pack Alumni 4 places - 260,00 EUR</label>

      <div id="section-individuel" class="section active">
        <label>Prenom (acheteur)</label>
        <input type="text" name="individuel_prenom">
        <label>Nom (acheteur)</label>
        <input type="text" name="individuel_nom">
        <label>Email</label>
        <input type="email" name="individuel_email">
        <label>Nombre de places</label>
        <select name="individuel_quantite" id="quantite">{qty_options}</select>
        <div id="prix-estime">Calcul du prix...</div>
      </div>

      <div id="section-pack3" class="section">
        <label>Email (recevra les billets)</label>
        <input type="email" name="pack3_email">
        <p>Nom de chaque invite du pack :</p>
        {pack_fields("pack3", 3)}
      </div>

      <div id="section-pack4" class="section">
        <label>Email (recevra les billets)</label>
        <input type="email" name="pack4_email">
        <p>Nom de chaque invite du pack :</p>
        {pack_fields("pack4", 4)}
      </div>

      <button type="submit">Payer et recevoir mon/mes billet(s)</button>
    </form>
  </div>

<script>
const radios = document.querySelectorAll('input[name="type_billet"]');
function updateSections() {{
  const value = document.querySelector('input[name="type_billet"]:checked').value;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.getElementById('section-' + value).classList.add('active');
}}
radios.forEach(r => r.addEventListener('change', updateSections));

function updatePrix() {{
  const qty = document.getElementById('quantite').value;
  fetch('/prix-actuel?qty=' + qty).then(r => r.json()).then(d => {{
    document.getElementById('prix-estime').textContent =
      'Total estime : ' + (d.total_cents / 100).toFixed(2) + ' EUR (' + qty + ' place(s))';
  }});
}}
document.getElementById('quantite').addEventListener('change', updatePrix);
updatePrix();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(purchase_page(), mimetype="text/html")


@app.route("/prix-actuel")
def prix_actuel():
    try:
        qty = int(request.args.get("qty", "1"))
    except ValueError:
        qty = 1
    qty = max(1, min(MAX_INDIVIDUAL_QTY, qty))
    conn = db.get_db()
    already_sold = db.count_individual_sold(conn)
    conn.close()
    total_cents = sum(compute_individual_prices(already_sold, qty))
    return jsonify({"total_cents": total_cents, "qty": qty})


@app.route("/acheter", methods=["POST"])
def acheter():
    type_billet = request.form.get("type_billet", "individuel")

    if type_billet == "individuel":
        prenom = request.form.get("individuel_prenom", "").strip()
        nom = request.form.get("individuel_nom", "").strip()
        email = request.form.get("individuel_email", "").strip()
        try:
            qty = int(request.form.get("individuel_quantite", "1"))
        except ValueError:
            qty = 1
        qty = max(1, min(MAX_INDIVIDUAL_QTY, qty))

        if not prenom or not nom or not email:
            return Response(purchase_page("Merci de remplir tous les champs correctement."), mimetype="text/html")

        conn = db.get_db()
        already_sold = db.count_individual_sold(conn)
        conn.close()
        prices = compute_individual_prices(already_sold, qty)
        total_cents = sum(prices)

        metadata = {
            "order_type": "individuel",
            "prenom": prenom,
            "nom": nom,
            "prices": ",".join(str(p) for p in prices),
        }
        product_name = f"{EVENT_NAME} - {qty} place(s) individuelle(s)"

    elif type_billet in PACKS:
        pack = PACKS[type_billet]
        email = request.form.get(f"{type_billet}_email", "").strip()
        names = []
        for i in range(1, pack["size"] + 1):
            p = request.form.get(f"{type_billet}_prenom{i}", "").strip()
            n = request.form.get(f"{type_billet}_nom{i}", "").strip()
            if not p or not n:
                return Response(
                    purchase_page(f"Merci de renseigner le nom des {pack['size']} invites du pack."),
                    mimetype="text/html",
                )
            names.append([p, n])

        if not email:
            return Response(purchase_page("Merci de renseigner un email."), mimetype="text/html")

        total_cents = pack["price_cents"]
        metadata = {"order_type": type_billet, "names": json.dumps(names)}
        product_name = f"{EVENT_NAME} - {pack['label']}"

    else:
        return Response(purchase_page("Type de billet invalide."), mimetype="text/html")

    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=email,
        line_items=[{
            "price_data": {
                "currency": "eur",
                "product_data": {"name": product_name},
                "unit_amount": total_cents,
            },
            "quantity": 1,
        }],
        metadata=metadata,
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
        <p>Votre/vos billet(s) avec QR code vous sont envoyes par email d'ici quelques instants.</p>
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
# Webhook Stripe : fulfillment (creation des billets + envoi email)
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
    if db.find_guests_by_session(conn, session["id"]):
        conn.close()
        return  # deja traite (Stripe peut renvoyer le meme evenement plusieurs fois)

    metadata = sget(session, "metadata", {}) or {}
    order_type = sget(metadata, "order_type", "individuel")
    customer_details = sget(session, "customer_details", {}) or {}
    email = sget(customer_details, "email") or sget(session, "customer_email")

    tickets_to_create = []  # (prenom, nom, categorie, amount_cents)

    if order_type == "individuel":
        prenom = sget(metadata, "prenom", "Invite")
        nom = sget(metadata, "nom", "")
        prices_raw = sget(metadata, "prices", "")
        prices = [int(x) for x in prices_raw.split(",") if x]
        for price in prices:
            tickets_to_create.append((prenom, nom, "Place individuelle", price, "individuel"))

    elif order_type in PACKS:
        pack = PACKS[order_type]
        names = json.loads(sget(metadata, "names", "[]"))
        share = pack["price_cents"] // pack["size"]
        remainder = pack["price_cents"] - share * pack["size"]
        for idx, (p, n) in enumerate(names):
            amount = share + (remainder if idx == 0 else 0)
            tickets_to_create.append((p, n, pack["label"], amount, order_type))

    created = []
    for prenom, nom, categorie, amount_cents, type_ in tickets_to_create:
        token = os.urandom(8).hex()
        db.create_guest(
            conn, prenom, nom, email, categorie, token,
            amount_cents=amount_cents, payment_status="paye",
            stripe_session_id=session["id"], type_=type_,
        )
        created.append((prenom, nom, categorie, token))
    conn.close()

    if email and created:
        pdf_bytes = build_multi_ticket_pdf_bytes(created)
        buyer_name = f"{created[0][0]} {created[0][1]}"
        email_utils.send_order_email(
            email, buyer_name,
            [(p, n, c) for p, n, c, _ in created],
            pdf_bytes,
        )


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
          <p>Billets vendus/crees : {s['total']} | Places individuelles vendues : {s['individual_sold']} |
          Entrees enregistrees : {s['checked_in']} | Recette : {s['revenue_cents'] / 100:.2f} EUR</p>
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
