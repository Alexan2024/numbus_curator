"""One-time: icons, share picture and web manifest for the site, drawn from the AH sign (img/logo.png)."""
import json, sys
from pathlib import Path
from PIL import Image

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
mark = Image.open(src).convert('RGBA')          # 642 × 581, black sign on transparent


def on_white(size, share=0.62, pad_ratio=None, w=None, h=None):
    W, H = (w or size), (h or size)
    bg = Image.new('RGBA', (W, H), (255, 255, 255, 255))
    target_h = int(H * share)
    m = mark.resize((round(mark.width * target_h / mark.height), target_h), Image.LANCZOS)
    bg.alpha_composite(m, ((W - m.width) // 2, (H - m.height) // 2))
    return bg.convert('RGB')


on_white(180).save(out / 'apple-touch-icon.png', optimize=True)
on_white(192).save(out / 'icon-192.png', optimize=True)
on_white(512).save(out / 'icon-512.png', optimize=True)
ico = on_white(256, share=0.66)
ico.save(out / 'favicon.ico', sizes=[(16, 16), (32, 32), (48, 48)])
on_white(0, share=0.30, w=1200, h=630).save(out / 'share.jpg', quality=90, optimize=True, progressive=True)

svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-60 -80 762 741">'
       '<style>path{stroke:#141414}@media (prefers-color-scheme:dark){path{stroke:#fff}}</style>'
       '<g fill="none" stroke-width="40" stroke-linecap="round">'
       '<path d="M20.75 563.5V184.87C20.75 109.27 53.76 40.58 126.35 21.48C176.21 8.36 224.9 14.48 256.73 40.33"/>'
       '<path d="M323.75 563.5V13.75"/><path d="M627.5 563.5V13.75"/><path d="M78 288.5H569.5"/></g></svg>\n')
(out / 'icon.svg').write_text(svg)
(out / 'site.webmanifest').write_text(json.dumps({
    "name": "AH Magazine", "short_name": "AH Magazine", "lang": "ru", "start_url": "/", "display": "browser",
    "background_color": "#FFFFFF", "theme_color": "#FFFFFF",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]}, ensure_ascii=False, indent=1) + '\n')
print('ok')
