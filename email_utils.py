"""Envoi de l'email de confirmation avec billet(s) PDF joint(s), via l'API Brevo."""
import base64
import os

import requests

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "no-reply@example.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "Gala")
BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def send_order_email(to_email, buyer_name, tickets, pdf_bytes):
    """Envoie un email avec un PDF contenant un ou plusieurs billets.
    tickets: liste de tuples (prenom, nom, categorie)."""
    if not BREVO_API_KEY:
        raise RuntimeError("BREVO_API_KEY manquant dans les variables d'environnement.")

    if len(tickets) == 1:
        prenom, nom, categorie = tickets[0]
        intro = f"""
            <p>Votre paiement a bien ete recu. Vous trouverez votre billet nominatif
            (categorie <strong>{categorie}</strong>) en piece jointe, avec son QR code
            d'entree.</p>
        """
    else:
        liste = "".join(f"<li>{p} {n} - {c}</li>" for p, n, c in tickets)
        intro = f"""
            <p>Votre paiement a bien ete recu. Vous trouverez vos {len(tickets)} billets
            nominatifs en piece jointe (un par page), chacun avec son propre QR code
            d'entree :</p>
            <ul>{liste}</ul>
        """

    payload = {
        "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
        "to": [{"email": to_email, "name": buyer_name}],
        "subject": "Votre/vos billet(s) pour le gala",
        "htmlContent": f"""
            <p>Bonjour {buyer_name},</p>
            {intro}
            <p>Presentez chaque billet (imprime ou sur telephone) a l'entree du gala.
            Un billet ne peut etre scanne qu'une seule fois.</p>
        """,
        "attachment": [
            {
                "content": base64.b64encode(pdf_bytes).decode("ascii"),
                "name": "billets.pdf",
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
