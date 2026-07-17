# -*- coding: utf-8 -*-
"""產生啟動畫面的逐格圖 splash_00.png ~ splash_07.png。

已隨版本庫提供,平常不需要跑這支;要調小人的姿勢、速度或文案時才用:

    pip install pillow
    python make_splash.py           # 產生 8 張逐格圖
    python make_splash.py --ascii   # 順便在終端機印出 ASCII 預覽 (檢查姿勢用)

## 為什麼是「整張畫面」的逐格圖,而不是小人一張、文字一張

第 0 格同時是 PyInstaller 啟動畫面的靜態圖 (見 pm.spec),第 0~7 格則是
serve.py 那個 Tk 視窗的動畫來源。兩者用同一批檔案,所以「解壓縮時的靜止
小人」與「Python 起來後開始跑的小人」在畫面上完全對得起來 —— 交棒的那
一瞬間不會位移或閃動。

## 為什麼邊緣不做抗鋸齒

Windows 的視窗透明只有色鍵 (magenta = 透明) 這條路,它是 1-bit 的:像素
不是全透明就是全不透明,沒有半透明。若讓邊緣做抗鋸齒,那些半透明像素會跟
洋紅混色,在桌面上顯示成一圈粉紅毛邊。

因此做法是:先做出一個「硬邊剪影」(二值化 + 外擴) 當外框,再把抗鋸齒過的
圖畫在剪影裡面 —— 外緣是硬的 (不與洋紅混色 → 沒有毛邊),內部仍然平滑。
文字的黑色描邊同時解決另一個問題:沒有背景卡片,白字浮在淺色桌面上會消失。
"""
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = 8
SS = 4                                  # 超取樣倍率:先畫大再縮,線條才平滑

W, H = 440, 250
MAGENTA = (255, 0, 255)                 # 色鍵:這個顏色 = 透明
INK = (24, 28, 34)                      # 小人與描邊的黑
SKIN = (252, 252, 252)                  # 頭與手的白
FAR = (96, 102, 112)                    # 遠側肢體:淺一點 = 景深
SHOE = (108, 68, 40)
SHOE_FAR = (86, 58, 38)
TEXT_MAIN = (255, 255, 255)
TEXT_SUB = (208, 220, 236)

STATUS = "系統啟動中..."
COPYRIGHT = "© 2026 John Wang 與貢獻者"
LICENSE = "MIT License · 開放原始碼"


# ---------------------------------------------------------------- 字型
def find_cjk_font():
    for p in (r"C:\Windows\Fonts\msjh.ttc",
              r"C:\Windows\Fonts\msyh.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
              "/System/Library/Fonts/PingFang.ttc"):
        if os.path.exists(p):
            return p
    raise SystemExit("找不到中文字型 (Linux 請安裝 fonts-noto-cjk)。")


def find_tc_face(path):
    """.ttc 裡有 JP/KR/SC/TC 多套字面。不寫死索引 —— 順序會隨字型改版變動,
    而猜錯是靜默的:畫面照常產生,只是繁體變成日文或簡體字形。"""
    for i in range(12):
        try:
            name = ImageFont.truetype(path, 12, index=i).getname()[0]
        except Exception:
            break
        if name.endswith(" TC"):
            return i
    return 0


FONT_FILE = find_cjk_font()
TC_INDEX = find_tc_face(FONT_FILE)


def font(px):
    return ImageFont.truetype(FONT_FILE, px, index=TC_INDEX)


# ---------------------------------------------------------------- 小人
def rot(x, y, deg):
    """把向量 (x, y) 轉 deg 度。畫面座標 y 向下,故正角度 = 向前 (+x) 擺。"""
    r = math.radians(deg)
    return (x * math.cos(r) - y * math.sin(r),
            x * math.sin(r) + y * math.cos(r))


def limb(origin, a1, l1, a2, l2):
    """兩節肢體:回傳 (關節, 末端)。角度都是「離鉛直向下」幾度。"""
    dx, dy = rot(0, l1, a1)
    joint = (origin[0] + dx, origin[1] + dy)
    dx2, dy2 = rot(0, l2, a2)
    return joint, (joint[0] + dx2, joint[1] + dy2)


# 跑步循環的四個關鍵姿勢 (側面、朝右)。角度都是「離鉛直向下」:
#   thigh 正 = 大腿向前抬;knee = 相對大腿再往後折 (腳跟往臀部方向)
#   upper 正 = 上臂向前;elbow = 相對上臂再往前折
# 8 格 = 這四個姿勢之間各插一格。
KEYS = [
    # (thighA, kneeA, thighB, kneeB, upperA, elbowA, upperB, elbowB)
    (50, 20, -40, 75, -45, 70, 50, 80),      # 接觸:A 腿在前
    (18, 55, -18, 110, -18, 85, 22, 70),     # 通過:A 腿收、B 腿高抬
    (-40, 75, 50, 20, 50, 80, -45, 70),      # 接觸:換 B 腿在前
    (-18, 110, 18, 55, 22, 70, -18, 85),     # 通過:鏡像
]


def pose(i):
    """第 i 格的關節角度 —— 在關鍵姿勢之間線性內插。"""
    t = i / FRAMES * len(KEYS)
    a, b = KEYS[int(t) % len(KEYS)], KEYS[(int(t) + 1) % len(KEYS)]
    f = t - int(t)
    return [x + (y - x) * f for x, y in zip(a, b)]


def draw_runner(d, cx, cy, i, s):
    """畫第 i 格的小人。s = 超取樣倍率;(cx, cy) 為臀部位置。"""
    th_a, kn_a, th_b, kn_b, up_a, el_a, up_b, el_b = pose(i)
    lw = int(7 * s)
    head_r = int(19 * s)

    # 跑步時身體上下起伏:一個步態週期起伏兩次
    bob = -3 * s * math.sin(4 * math.pi * i / FRAMES)
    hip = (cx, cy + bob)
    neck = (hip[0] + 5 * s, hip[1] - 46 * s)          # 身體微微前傾
    head_c = (neck[0] + 3 * s, neck[1] - head_r + 2 * s)
    shoulder = (neck[0] - 1 * s, neck[1] + 5 * s)

    def seg(p, q, width=None, fill=INK):
        w = width or lw
        d.line([p, q], fill=fill, width=w)
        r = w / 2
        for c in (p, q):            # 圓角關節:兩段之間不會有缺口
            d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=fill)

    def leg(thigh, knee_rel, back):
        j, e = limb(hip, thigh, 34 * s, thigh - knee_rel, 32 * s)
        col = FAR if back else INK
        seg(hip, j, fill=col)
        seg(j, e, fill=col)
        ang = math.degrees(math.atan2(e[0] - j[0], e[1] - j[1]))
        tx, ty = rot(0, 13 * s, ang - 75)             # 腳掌:與小腿約成直角
        seg(e, (e[0] + tx, e[1] + ty), width=int(9 * s),
            fill=SHOE_FAR if back else SHOE)

    def arm(upper, elbow_rel, back):
        j, e = limb(shoulder, upper, 26 * s, upper + elbow_rel, 22 * s)
        col = FAR if back else INK
        seg(shoulder, j, fill=col)
        seg(j, e, fill=col)
        r = 6 * s
        d.ellipse([e[0] - r, e[1] - r, e[0] + r, e[1] + r],
                  fill=SKIN, outline=col, width=int(3 * s))

    leg(th_b, kn_b, back=True)      # 先畫遠側,才會被近側蓋住
    arm(up_b, el_b, back=True)
    seg(hip, neck)                  # 身體
    leg(th_a, kn_a, back=False)
    arm(up_a, el_a, back=False)
    d.ellipse([head_c[0] - head_r, head_c[1] - head_r,
               head_c[0] + head_r, head_c[1] + head_r],
              fill=SKIN, outline=INK, width=int(5 * s))


# ---------------------------------------------------------------- 合成
def hard_edge(art):
    """把 RGBA 壓成「硬邊剪影 + 內部抗鋸齒」,貼到洋紅底上 (理由見檔頭)。

    剪影的門檻必須是「alpha > 0」而不是某個好看的中間值 —— 只要有一個
    半透明像素落在剪影之外,它就會直接跟洋紅混色,在桌面上變成粉紅雜點。
    門檻取 0 再外擴一圈,可保證每個帶 alpha 的像素都落在深色剪影上,
    抗鋸齒因此只發生在「圖 vs 剪影」之間,永遠不會碰到洋紅。
    """
    alpha = art.getchannel("A")
    sil = alpha.point(lambda v: 255 if v > 0 else 0)
    sil = sil.filter(ImageFilter.MaxFilter(3))          # 剪影外擴一圈
    out = Image.new("RGB", art.size, MAGENTA)
    out.paste(INK, (0, 0), sil)                          # 硬邊外框
    out.paste(art.convert("RGB"), (0, 0), alpha)         # 內部原圖
    return out


def make_frame(i):
    art = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    draw_runner(ImageDraw.Draw(art), W * SS // 2, 112 * SS, i, SS)
    art = art.resize((W, H), Image.LANCZOS)

    d = ImageDraw.Draw(art)
    for text, y, px, fill in ((STATUS, 158, 21, TEXT_MAIN),
                              (COPYRIGHT, 198, 13, TEXT_SUB),
                              (LICENSE, 219, 13, TEXT_SUB)):
        f = font(px)
        w = d.textbbox((0, 0), text, font=f)[2]
        d.text(((W - w) // 2, y), text, font=f, fill=fill,
               stroke_width=3, stroke_fill=INK)
    return hard_edge(art)


def ascii_preview(img, cols=56):
    """把圖降階成字元印出來 —— 確認姿勢用,不是拿來看細節的。"""
    g = img.convert("L").resize((cols, int(cols * H / W / 2.1)))
    px = g.load()
    ramp = " .:-=+*#%@"
    out = []
    for y in range(g.size[1]):
        row = ""
        for x in range(g.size[0]):
            v = px[x, y]
            # 洋紅底轉灰約 105;比它暗的才是圖
            row += ramp[min(9, (100 - v) // 11)] if v < 100 else " "
        out.append(row)
    return "\n".join(out)


def main():
    for i in range(FRAMES):
        img = make_frame(i)
        img.save(os.path.join(HERE, f"splash_{i:02d}.png"))
        if "--ascii" in sys.argv:
            print(f"\n=== 第 {i} 格 " + "=" * 42)
            print(ascii_preview(img))
    json.dump({"frames": FRAMES, "size": [W, H], "interval_ms": 110},
              open(os.path.join(HERE, "splash_layout.json"), "w"), indent=2)
    print(f"\n已產生 {FRAMES} 張 splash_NN.png ({W}x{H}) 與 splash_layout.json")


if __name__ == "__main__":
    main()
