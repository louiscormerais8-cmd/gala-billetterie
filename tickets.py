"""Generation du QR code et du PDF de billet (module partage)."""
import io

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

EVENT_NAME = "Gala ISMIN"
EVENT_DATE = "28 novembre 2026"


def make_qr_image(payload):
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def draw_ticket(c, prenom, nom, categorie, qr_img):
    width, height = A4
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width / 2, height - 40 * mm, EVENT_NAME)
    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, height - 48 * mm, EVENT_DATE)

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, height - 70 * mm, f"{prenom} {nom}")
    c.setFont("Helvetica", 14)
    c.drawCentredString(width / 2, height - 78 * mm, f"Categorie : {categorie}")

    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    qr_size = 70 * mm
    c.drawImage(
        ImageReader(buf),
        (width - qr_size) / 2,
        height - 78 * mm - qr_size - 15 * mm,
        width=qr_size,
        height=qr_size,
    )
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(
        width / 2, 20 * mm, "Billet personnel et non cessible - un seul scan valide a l'entree."
    )


def build_ticket_pdf_bytes(prenom, nom, categorie, token):
    """Genere un PDF de billet (une page) en memoire, pret a etre joint a un email."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    qr_img = make_qr_image(f"GALA:{token}")
    draw_ticket(c, prenom, nom, categorie, qr_img)
    c.save()
    buf.seek(0)
    return buf.read()


def build_multi_ticket_pdf_bytes(tickets):
    """Genere un seul PDF (une page par billet) pour une commande de plusieurs places.
    tickets: liste de tuples (prenom, nom, categorie, token)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for prenom, nom, categorie, token in tickets:
        qr_img = make_qr_image(f"GALA:{token}")
        draw_ticket(c, prenom, nom, categorie, qr_img)
        c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
