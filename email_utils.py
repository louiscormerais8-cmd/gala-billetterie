"""Envoi de l'email de confirmation avec billet PDF joint, via l'API Brevo."""
import base64
import os

import requests

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "no-reply@example.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "Gala")
BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def send_ticket_email(to_email, prenom, nom, categorie, pdf_bytes):
    if not BREVO_API_KEY:
        raise RuntimeError("BREVO_API_KEY manquant dans les variables d'environnement.")

    payload = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": to_email, "name": f"{prenom} {nom}"}],
        "subject": "Votre billet pour le gala",
        "htmlContent": f"""
            <p>Bonjour {prenom},</p>
            <p>Votre paiement a bien ete recu. Vous trouverez votre billet nominatif
            (categorie <strong>{categorie}</strong>) en piece jointe, avec son QR code
            d'entree.</p>
            <p>Presentez ce billet (imprime ou sur telephone) a l'entree du gala.</p>
        """,
        "attachment": [
            {
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
                "name": "billet.pdf",
            }
        ],
    }
    headers = {
        "api-key": BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.post(BREVO_ENDPOINT, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()
