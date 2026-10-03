"""#55 — logo bytes become a PNG without a database."""
import base64
import io

import pytest

from app.services.logo_store import PNG_MAGIC, LogoError, rasterize_logo

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_png_passes_through():
    assert rasterize_logo(_PNG, "image/png", "logo.png") == _PNG


def test_jpeg_becomes_png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, format="JPEG")
    out = rasterize_logo(buf.getvalue(), "image/jpeg", "logo.jpg")
    assert out.startswith(PNG_MAGIC) and out != buf.getvalue()


def test_unknown_bytes_are_rejected():
    with pytest.raises(LogoError):
        rasterize_logo(b"not an image", "application/octet-stream", "x.bin")


def test_svg_converts_when_cairo_is_installed():
    try:
        import cairosvg
        cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>')
    except Exception:
        pytest.skip("cairosvg/libcairo not available")
    png = rasterize_logo(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>',
        "image/svg+xml",
        "mark.svg",
    )
    assert png.startswith(PNG_MAGIC)


def test_svg_without_cairo_is_a_logo_error(monkeypatch):
    import builtins

    real = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "cairosvg":
            raise ImportError("no cairo")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(LogoError):
        rasterize_logo(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml", "a.svg")
