"""
Genere des billets pour des invites hors paiement en ligne (staff, sponsors,
invitations offertes). Les billets achetes via le site sont geres automatiquement
par app.py (webhook Stripe).

Usage: py generate_tickets.py [chemin_vers_liste.csv]
Le CSV doit avoir les colonnes: prenom,nom,email,categorie
"""
import csv
import os
import secrets
import sys
import unicodedata

import db
from tickets import build_ticket_pdf_bytes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKETS_DIR = os.path.join(BASE_DIR, "tickets_generes")


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "guests_example.csv")
    if not os.path.exists(csv_path):
        print(f"Fichier introuvable : {csv_path}")
        sys.exit(1)

    os.makedirs(TICKETS_DIR, exist_ok=True)
    db.init_db()
    conn = db.get_db()

    created, reused = 0, 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            prenom = row["prenom"].strip()
            nom = row["nom"].strip()
            email = (row.get("email") or "").strip()
            categorie = (row.get("categorie") or "Standard").strip() or "Standard"
            if not prenom or not nom:
                continue

            existing = db.find_guest(conn, prenom, nom, categorie)
            if existing:
                token = existing["token"]
                reused += 1
            else:
                token = secrets.token_urlsafe(8)
                db.create_guest(conn, prenom, nom, email, categorie, token, payment_status="invite", type_="invite")
                created += 1

            pdf_bytes = build_ticket_pdf_bytes(prenom, nom, categorie, token)
            filename = f"{slugify(nom)}_{slugify(prenom)}.pdf"
            with open(os.path.join(TICKETS_DIR, filename), "wb") as out:
                out.write(pdf_bytes)

    conn.close()
    print(f"Termine. {created} nouveaux billets, {reused} billets deja existants reutilises.")
    print(f"PDF dans : {TICKETS_DIR}")


if __name__ == "__main__":
    main()
