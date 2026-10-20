#!/usr/bin/env python3
"""Generate examples/cat.png (a cartoon cat face on a transparent background) with Pillow only,
so the sample recipes need no external assets. Run: python examples/make_cat.py"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 512
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
fur, dark, pink = (255, 170, 60, 255), (40, 20, 10, 255), (255, 120, 150, 255)
d.polygon([(90, 200), (110, 40), (230, 120)], fill=fur, outline=dark)       # ears
d.polygon([(422, 200), (402, 40), (282, 120)], fill=fur, outline=dark)
d.polygon([(125, 160), (130, 80), (190, 125)], fill=pink)
d.polygon([(387, 160), (382, 80), (322, 125)], fill=pink)
d.ellipse((60, 100, 452, 470), fill=fur, outline=dark, width=8)             # head
for x in (170, 342):                                                         # eyes
    d.ellipse((x - 42, 220, x + 42, 300), fill=(255, 255, 255, 255), outline=dark, width=5)
    d.ellipse((x - 14, 228, x + 14, 292), fill=dark)
d.polygon([(236, 320), (276, 320), (256, 346)], fill=pink)                   # nose
d.arc((206, 336, 256, 376), 0, 180, fill=dark, width=5)
d.arc((256, 336, 306, 376), 0, 180, fill=dark, width=5)
for sgn in (-1, 1):                                                          # whiskers
    for dy in (-18, 0, 18):
        d.line((256 + sgn * 60, 352 + dy // 2, 256 + sgn * 200, 340 + dy * 2), fill=dark, width=4)
im.save(Path(__file__).with_name("cat.png"))
print("wrote examples/cat.png")
