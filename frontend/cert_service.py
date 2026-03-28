"""
Certificate Generation Service
Uses Pillow (PIL) + qrcode to generate certificate images.
"""
import uuid
import os
import io
from django.conf import settings


def generate_certificate(student_user, course_id, course_name):
    """
    Main entry point.
    Finds the active CertificateTemplate for this course, composites the
    background + text + signature + QR, saves as PNG in media/certificates/,
    creates and returns an IssuedCertificate instance.

    Returns:
        IssuedCertificate instance on success, None on failure.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import qrcode
    except ImportError:
        print("ERROR: Pillow or qrcode not installed. Run: pip install Pillow qrcode[pil]")
        return None

    from .models import CertificateTemplate, CertificateSigner, IssuedCertificate

    # 1. Find the teacher's latest active template for this course
    template = (
        CertificateTemplate.objects.filter(course_id=course_id).order_by('-created_at').first()
        or CertificateTemplate.objects.order_by('-created_at').first()
    )

    if not template:
        print(f"ERROR: No certificate template found for course {course_id}")
        return None

    teacher = template.teacher

    # 2. Get signer info (best effort)
    signer = None
    try:
        signer = CertificateSignerProxy(teacher)
    except Exception:
        pass

    # 3. Generate a unique verification code
    code = str(uuid.uuid4())

    # 4. Build the QR code image
    qr_img = _make_qr(code)

    # 5. Composite the certificate
    cert_image = _composite(
        template=template,
        student_name=student_user.get_full_name() or student_user.username,
        course_name=course_name,
        signer=signer,
        qr_img=qr_img,
    )

    if cert_image is None:
        return None

    # 6. Save the composite image to media/certificates/<code>.png
    rel_path = f"certificates/{code}.png"
    abs_dir = os.path.join(settings.MEDIA_ROOT, "certificates")
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(abs_dir, f"{code}.png")
    cert_image.save(abs_path, "PNG")

    # 7. Create the DB record
    issued = IssuedCertificate.objects.create(
        student=student_user,
        course_id=course_id,
        course_name=course_name,
        certificate_template=template,
        verification_code=code,
        certificate_file=rel_path,
    )
    print(f"Certificate issued: {issued}")
    return issued


# ─── Internal helpers ─────────────────────────────────────────────────────────

class CertificateSignerProxy:
    """Wraps CertificateSigner ORM object for safe access."""
    def __init__(self, teacher):
        from .models import CertificateSigner
        obj = CertificateSigner.objects.get(teacher=teacher)
        self.name = obj.signer_name
        self.designation = obj.designation
        self.image_path = obj.signature_image.path if obj.signature_image else None


def _make_qr(code: str) -> 'Image':
    """Generate a QR code image pointing to the verification URL."""
    import qrcode
    # Build the URL – the base URL is derived from settings; fall back to a relative path
    base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
    verify_url = f"{base_url}/verify-certificate/{code}/"
    qr = qrcode.QRCode(version=1, box_size=5, border=2)
    qr.add_data(verify_url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGBA")


def _composite(template, student_name, course_name, signer, qr_img) -> 'Image':
    """Composites all layers onto the background image."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    # Load background
    bg_path = template.background_image.path
    cert = Image.open(bg_path).convert("RGBA")
    draw = ImageDraw.Draw(cert)

    # Try to load a TTF font; fall back to default if unavailable
    def _load_font(size):
        font_dir = os.path.join(settings.BASE_DIR, "static", "fonts")
        candidates = [
            os.path.join(font_dir, f"{template.font_family}-Bold.ttf"),
            os.path.join(font_dir, f"{template.font_family}.ttf"),
            os.path.join(font_dir, "Poppins-Bold.ttf"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    # Draw student name
    font_large = _load_font(52)
    draw.text(
        (template.student_name_x, template.student_name_y),
        student_name,
        font=font_large,
        fill="black",
        anchor="mm",
    )

    # Draw course name
    font_medium = _load_font(36)
    draw.text(
        (template.course_name_x, template.course_name_y),
        course_name,
        font=font_medium,
        fill="#333333",
        anchor="mm",
    )

    # Paste signature image (if available)
    if signer and signer.image_path and os.path.exists(signer.image_path):
        try:
            sig_img = Image.open(signer.image_path).convert("RGBA")
            sig_img.thumbnail((200, 80))
            cert.paste(sig_img, (template.signature_x, template.signature_y), sig_img)

            # Draw signer name + designation below signature
            font_small = _load_font(20)
            draw.text(
                (template.signature_x + 100, template.signature_y + 90),
                f"{signer.name}\n{signer.designation}",
                font=font_small,
                fill="#555555",
                anchor="mm",
            )
        except Exception as e:
            print(f"WARNING: Could not composite signature: {e}")

    # Paste QR code
    try:
        qr_img.thumbnail((100, 100))
        cert.paste(qr_img, (template.qr_x, template.qr_y), qr_img)
    except Exception as e:
        print(f"WARNING: Could not paste QR: {e}")

    return cert.convert("RGB")
