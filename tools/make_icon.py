"""Draws the Nightwave icon (synthwave sun + visualiser ring) → assets/icon.ico + assets/icon.png"""
import math, os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets")
S = 1024  # draw big, scale down


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def draw(detail=True):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # rounded-square background with vertical gradient
    bg = Image.new("RGBA", (S, S))
    px = bg.load()
    for y in range(S):
        c = lerp((10, 2, 22, 255), (58, 8, 64, 255), (y / S) ** 1.4)
        for x in range(S):
            px[x, y] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
    img.paste(bg, (0, 0), mask)

    cx, cy = S // 2, int(S * 0.47)
    hz = int(S * 0.70)                      # horizon
    d = ImageDraw.Draw(img)

    # grid floor
    if detail:
        grid = Image.new("RGBA", (S, S), (0, 0, 0, 0)); g = ImageDraw.Draw(grid)
        for k in range(1, 7):
            y = hz + (S - hz) * (k / 6) ** 1.8
            g.line([(0, y), (S, y)], fill=(255, 60, 200, 150), width=int(S * 0.008))
        for i in range(-6, 7):
            g.line([(cx + i * S * 0.16, S), (cx, hz)], fill=(255, 60, 200, 150), width=int(S * 0.008))
        floor_mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(floor_mask).rectangle([0, hz, S, S], fill=255)
        floor_mask = Image.composite(floor_mask, Image.new("L", (S, S), 0), mask)
        img.paste(grid, (0, 0), Image.composite(grid.split()[3], Image.new("L", (S, S), 0), floor_mask))

    # glow behind sun
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([cx - S * .3, cy - S * .3, cx + S * .3, cy + S * .3], fill=(255, 70, 160, 140))
    glow = glow.filter(ImageFilter.GaussianBlur(S * 0.06))
    img.alpha_composite(Image.composite(glow, Image.new("RGBA", (S, S)), mask))

    # sun with gradient + stripes, clipped at the horizon
    R = int(S * 0.22)
    sun = Image.new("RGBA", (S, S), (0, 0, 0, 0)); sp = ImageDraw.Draw(sun)
    for y in range(cy - R, cy + R + 1):
        t = (y - (cy - R)) / (2 * R)
        col = lerp((255, 225, 100), (255, 115, 70), t / 0.5) if t < 0.5 else lerp((255, 115, 70), (255, 35, 140), (t - .5) / .5)
        sp.line([(cx - R, y), (cx + R, y)], fill=col + (255,))
    smask = Image.new("L", (S, S), 0); sm = ImageDraw.Draw(smask)
    sm.ellipse([cx - R, cy - R, cx + R, cy + R], fill=255)
    stripes = [(0.18, 0.05), (0.40, 0.07), (0.62, 0.09)] if detail else [(0.30, 0.10), (0.62, 0.13)]
    for off, th in stripes:
        y = cy + off * R
        sm.rectangle([cx - R, y, cx + R, y + th * R], fill=0)
    sm.rectangle([0, hz, S, S], fill=0)
    img.paste(sun, (0, 0), smask)

    # horizon line
    d.line([(S * 0.06, hz), (S * 0.94, hz)], fill=(255, 120, 220, 255), width=int(S * (0.012 if detail else 0.02)))

    # visualiser ring of dots (teal → lime)
    RR = R * 1.42
    n = 36 if detail else 18
    for i in range(n):
        a = -math.pi / 2 + i / n * 2 * math.pi
        k = min(i, n - i) / (n / 2)
        bump = 1 + 0.10 * math.sin(i * 1.7) ** 2 if detail else 1
        x, y = cx + math.cos(a) * RR * bump, cy + math.sin(a) * RR * bump
        if y > hz - S * 0.01:
            continue
        r = S * (0.018 if detail else 0.034)
        d.ellipse([x - r, y - r, x + r, y + r], fill=lerp((40, 220, 235), (160, 245, 70), k) + (255,))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    big, small = draw(True), draw(False)
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    frames = [(small if s <= 32 else big).resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(os.path.join(OUT, "icon.ico"), format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    small.resize((64, 64), Image.LANCZOS).save(os.path.join(OUT, "tray.png"))
    print("icons written to", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
