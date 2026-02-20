import qrcode
from PIL import Image

def generate_solana_qr(solana_url: str) -> Image.Image:
    """
    Generate a QR code image from a full Solana Pay URL.
    Returns a PIL Image object (does not save to disk).
    """
    img = qrcode.make(solana_url)
    return img

