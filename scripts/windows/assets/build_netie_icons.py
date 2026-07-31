"""Build Netie (sharp square brand) + rebuild Netie Space multi-size ICOs."""
from __future__ import annotations

import math
import struct
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_OV = Path(__file__).resolve().parent
SPACE_PNG = Path(r"D:\Netie Space\src\NetieSpace\Assets\netie-icon.png")
SPACE_ICO = Path(r"D:\Netie Space\src\NetieSpace\Assets\netie.ico")
DIST_ICO = Path(r"D:\Netie Space\dist\NetieSpace\Assets\netie.ico")

SIZES = [16, 24, 32, 48, 64, 128, 256]


def star_points(cx: float, cy: float, r: float, rotate_deg: float = 45.0) -> list[tuple[float, float]]:
    """Concave square mark from Brand_mark.svg (points toward corners at rotate=45)."""

    def bez(
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
        n: int = 24,
    ) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for i in range(n):
            t = i / n
            u = 1 - t
            x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
            y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
            pts.append((x, y))
        return pts

    segs = [
        ((0.0, -148.0), (26.0, -58.0), (58.0, -26.0), (148.0, 0.0)),
        ((148.0, 0.0), (58.0, 26.0), (26.0, 58.0), (0.0, 148.0)),
        ((0.0, 148.0), (-26.0, 58.0), (-58.0, 26.0), (-148.0, 0.0)),
        ((-148.0, 0.0), (-58.0, -26.0), (-26.0, -58.0), (0.0, -148.0)),
    ]
    local: list[tuple[float, float]] = []
    for s in segs:
        local.extend(bez(*s))

    rad = math.radians(rotate_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    scale = r / 148.0
    out: list[tuple[float, float]] = []
    for x, y in local:
        xr = x * cos_a - y * sin_a
        yr = x * sin_a + y * cos_a
        out.append((cx + xr * scale, cy + yr * scale))
    return out


def diamond(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [(cx, cy - r), (cx + r * 0.74, cy), (cx, cy + r), (cx - r * 0.74, cy)]


def render_netie(size: int, rounded: bool) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = (16, 20, 28, 255)  # #10141C
    if rounded:
        rad = int(size * 0.22)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=rad, fill=bg)
    else:
        draw.rectangle([0, 0, size - 1, size - 1], fill=bg)

    cx = cy = size / 2
    r = size * 0.36
    pts = star_points(cx, cy, r, 45)

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon(pts, fill=(200, 245, 238, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, size // 32)))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    draw.polygon(pts, fill=(111, 212, 200, 255))
    hx, hy = cx - r * 0.28, cy - r * 0.38
    draw.ellipse(
        [hx - r * 0.28, hy - r * 0.16, hx + r * 0.28, hy + r * 0.16],
        fill=(255, 255, 255, 70),
    )
    draw.ellipse(
        [hx - r * 0.12, hy - r * 0.08, hx + r * 0.08, hy + r * 0.04],
        fill=(255, 255, 255, 100),
    )
    draw.polygon(diamond(cx, cy, r * 0.30), fill=(242, 255, 252, 255))
    s = max(1, size // 48)
    draw.ellipse([cx - s, cy - s, cx + s, cy + s], fill=(255, 255, 255, 230))
    return img


def png_bytes(im: Image.Image) -> bytes:
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def write_ico(path: Path, images: list[Image.Image]) -> None:
    entries: list[tuple[int, int, int, int]] = []
    blobs: list[bytes] = []
    offset = 6 + 16 * len(images)
    for im in images:
        raw = png_bytes(im)
        w, h = im.size
        entries.append((0 if w >= 256 else w, 0 if h >= 256 else h, len(raw), offset))
        blobs.append(raw)
        offset += len(raw)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(images)))
        for w, h, nbytes, off in entries:
            f.write(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, nbytes, off))
        for b in blobs:
            f.write(b)


def sizes_from(master: Image.Image) -> list[Image.Image]:
    return [master.resize((s, s), Image.Resampling.LANCZOS) for s in SIZES]


def main() -> None:
    # Netie parent / stack launcher = sharp square tile + Brand_mark star (not Space rounded chrome).
    master_netie = render_netie(512, rounded=False).resize((256, 256), Image.Resampling.LANCZOS)

    netie_png = OUT_OV / "netie.png"
    netie_ico = OUT_OV / "netie.ico"
    master_netie.save(netie_png)
    write_ico(netie_ico, sizes_from(master_netie))
    print(f"wrote {netie_ico} ({netie_ico.stat().st_size} bytes)")

    # Netie Space keeps rounded tile PNG; rebuild ICO with clean sizes.
    if SPACE_PNG.exists():
        space = Image.open(SPACE_PNG).convert("RGBA")
        write_ico(SPACE_ICO, sizes_from(space))
        print(f"wrote {SPACE_ICO} ({SPACE_ICO.stat().st_size} bytes)")
        if DIST_ICO.parent.parent.exists():
            write_ico(DIST_ICO, sizes_from(space))
            print(f"wrote {DIST_ICO}")

    master_netie.resize((64, 64), Image.Resampling.LANCZOS).save(OUT_OV / "_peek-netie-64.png")
    print("done")


if __name__ == "__main__":
    main()
