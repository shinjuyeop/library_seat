"""Render the geometric seat mark to SVG and iPhone/PWA PNG sizes (Pillow dev tool)."""
from pathlib import Path
from PIL import Image, ImageDraw

assets = Path(__file__).resolve().parents[1] / 'public' / 'assets'
background, foreground = '#173e32', '#ffffff'
# Rounded back, seat, and two legs. All artwork stays inside the maskable safe area.
shapes = [(178, 120, 334, 244, 25), (146, 262, 366, 300, 14),
          (164, 292, 194, 394, 10), (318, 292, 348, 394, 10)]
svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
svg += f'<rect width="512" height="512" rx="112" fill="{background}"/>'
svg += ''.join(f'<rect x="{x}" y="{y}" width="{right-x}" height="{bottom-y}" rx="{radius}" fill="{foreground}"/>'
               for x, y, right, bottom, radius in shapes) + '</svg>\n'
(assets / 'icon.svg').write_text(svg, encoding='utf-8')
scale = 4
canvas = Image.new('RGB', (512 * scale, 512 * scale), background)
draw = ImageDraw.Draw(canvas)
for x, y, right, bottom, radius in shapes:
    draw.rounded_rectangle((x*scale, y*scale, right*scale, bottom*scale), radius=radius*scale, fill=foreground)
for size, name in [(32, 'favicon-32.png'), (180, 'apple-touch-icon.png'), (192, 'icon-192.png'), (512, 'icon-512.png')]:
    canvas.resize((size, size), Image.Resampling.LANCZOS).save(assets / name, optimize=True)
