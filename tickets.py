"""Generation du QR code et du PDF de billet (module partage)."""
import io
import os

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

EVENT_NAME = "Gala ISMIN"
EVENT_DATE = "28 novembre 2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "ticket_template.png")
TEMPLATE_PX_SIZE = (1240, 1748)  # dimensions du visuel Canva (A4 a 150dpi)

# Position du carre QR et de la zone nom, releves par echantillonnage de pixels
# sur le visuel (voir outils de mesure utilises lors de l'integration).
QR_SQUARE_PX = {"left": 317, "right": 923, "top": 650, "bottom": 1250}
NAME_Y_PX = 1340

SILVER = (190 / 255, 190 / 255, 196 / 255)  # utilise pour le texte du nom
FALLBACK_BG = (24 / 255, 23 / 255, 91 / 255)  # utilise seulement si le visuel est absent

_scale_x = A4[0] / TEMPLATE_PX_SIZE[0]
_scale_y = A4[1] / TEMPLATE_PX_SIZE[1]


def _px_to_pt(px, py):
    return px * _scale_x, A4[1] - py * _scale_y


def make_qr_image_transparent(payload, fill_color=(0, 0, 0)):
    """QR code avec modules colores et fond totalement transparent, pour se
    poser directement sur un visuel (degrade, photo...) sans carre visible."""
    qr = qrcode.QRCode(
        border=2, box_size=8, error_correction=qrcode.constants.ERROR_CORRECT_H
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill_color, back_color=(255, 255, 255)).convert("RGBA")
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            if (r, g, b) == (255, 255, 255):
                pixels[x, y] = (r, g, b, 0)
    return img


def draw_ticket(c, prenom, nom, categorie, qr_img):
    width, height = A4

    has_template = os.path.exists(TEMPLATE_PATH)
    if has_template:
        c.drawImage(TEMPLATE_PATH, 0, 0, width=width, height=height)
    else:
        # Repli simple si le visuel n'est pas encore fourni.
        c.setFillColorRGB(*FALLBACK_BG)
        c.rect(0, 0, width, height, fill=1, stroke=0)
        c.setFillColorRGB(*SILVER)
        c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(width / 2, height - 40 * 2.8346, EVENT_NAME)
        c.setFont("Helvetica", 12)
        c.drawCentredString(width / 2, height - 48 * 2.8346, EVENT_DATE)

    left_pt, top_pt = _px_to_pt(QR_SQUARE_PX["left"], QR_SQUARE_PX["top"])
    right_pt, bottom_pt = _px_to_pt(QR_SQUARE_PX["right"], QR_SQUARE_PX["bottom"])
    square_w = right_pt - left_pt
    square_h = top_pt - bottom_pt
    center_x = left_pt + square_w / 2
    center_y = bottom_pt + square_h / 2

    padding = 20
    qr_size = min(square_w, square_h) - 2 * padding

    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    c.drawImage(
        ImageReader(buf),
        center_x - qr_size / 2,
        center_y - qr_size / 2,
        width=qr_size,
        height=qr_size,
        mask="auto",
    )

    _, name_y = _px_to_pt(0, NAME_Y_PX)
    c.setFillColorRGB(*SILVER)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width / 2, name_y, f"{prenom} {nom}")

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica", 9)
    c.drawCentredString(
        width / 2, 18, "Billet personnel et non cessible - un seul scan valide a l'entree."
    )
    c.setFillColorRGB(0, 0, 0)


def build_ticket_pdf_bytes(prenom, nom, categorie, token):
    """Genere un PDF de billet (une page) en memoire, pret a etre joint a un email."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    qr_img = make_qr_image_transparent(f"GALA:{token}")
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
        qr_img = make_qr_image_transparent(f"GALA:{token}")
        draw_ticket(c, prenom, nom, categorie, qr_img)
        c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
