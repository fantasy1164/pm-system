# -*- coding: utf-8 -*-
"""產生 app.ico —— 甘特圖意象的應用程式圖示。

app.ico 已隨版本庫提供,平常不需要跑這支;想換配色或造型時才用:

    pip install pillow
    python make_icon.py

輸出多尺寸 (16-256px) 的單一 .ico,Windows 檔案總管/工作列/桌面各取所需。
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app.ico")

# 配色:深藍底 + 三條甘特 bar + 里程碑菱形
BG_TOP = (30, 58, 95)        # 深藍
BG_BOTTOM = (17, 34, 58)
BARS = [
    ((56,  84,  200, 132), (86, 197, 245)),   # 天藍
    ((104, 152, 300, 200), (255, 196, 87)),   # 琥珀
    ((56,  220, 248, 268), (120, 220, 160)),  # 綠
]
MILESTONE = (255, 255, 255)


def rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render(size=512):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 512.0

    # 背景:圓角方形 + 垂直漸層
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / size
        c = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t)
                  for i in range(3)) + (255,)
        gd.line([(0, y), (size, y)], fill=c)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [int(16 * s)] * 2 + [size - int(16 * s)] * 2,
        radius=int(96 * s), fill=255)
    img.paste(grad, (0, 0), mask)

    # 甘特 bar
    for (x0, y0, x1, y1), color in BARS:
        rounded(d, [x0 * s, y0 * s, x1 * s, y1 * s],
                radius=int(24 * s), fill=color + (255,))

    # 里程碑菱形 (第三條 bar 右側)
    cx, cy, r = 320 * s, 244 * s, 34 * s
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
              fill=MILESTONE + (255,))

    # 連接線:第三條 bar → 菱形
    d.line([(268 * s, 244 * s), (cx - r, cy)],
           fill=(255, 255, 255, 200), width=max(1, int(8 * s)))

    # 時間軸刻度 (底部三個小點)
    for i in range(3):
        x = (140 + i * 116) * s
        d.ellipse([x - 10 * s, 330 * s, x + 10 * s, 350 * s],
                  fill=(255, 255, 255, 90))
    return img


def main():
    base = render(512)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    base.save(OUT, format="ICO", sizes=sizes)
    print(f"已產生 {OUT} ({', '.join(str(w) for w, _ in sizes)} px)")


if __name__ == "__main__":
    main()
