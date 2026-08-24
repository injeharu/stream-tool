"""アプリアイコン(.ico)を生成する開発用スクリプト。

紫の角丸背景+白いギフトボックス+金のリボン、という
管理画面の配色(Twitch紫 #9146FF / アクセント金 #FFB84D)に合わせたデザイン。
実行すると installer/icon.ico と web/static/favicon.ico を生成する。
"""

import os

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PURPLE = (145, 70, 255, 255)        # #9146FF
PURPLE_DARK = (100, 40, 200, 255)
WHITE = (255, 255, 255, 255)
GOLD = (255, 184, 77, 255)          # #FFB84D
GOLD_DARK = (230, 150, 40, 255)


def draw_icon(size):
    """アイコンを描く。小さいサイズでは細部がつぶれるため、要素を減らして輪郭を強調する。"""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    simple = s <= 32  # トレイ・タブなど小サイズ向けの単純化デザイン

    def px(ratio):
        return int(round(s * ratio))

    # 背景: 紫の角丸スクエア(大サイズは下に濃い影を入れて立体感を出す)
    radius = px(0.22)
    if simple:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=PURPLE)
    else:
        d.rounded_rectangle([0, px(0.03), s - 1, s - 1], radius=radius, fill=PURPLE_DARK)
        d.rounded_rectangle([0, 0, s - 1, s - px(0.05)], radius=radius, fill=PURPLE)

    cx = s // 2

    if simple:
        # 小サイズ: 白い箱+太い金の十字リボンだけ。リボンの輪は描かない(つぶれるため)
        box = [px(0.16), px(0.30), px(0.84), px(0.84)]
        d.rectangle(box, fill=WHITE)
        rib_w = max(2, px(0.16))
        d.rectangle([cx - rib_w // 2, box[1], cx + rib_w // 2, box[3]], fill=GOLD)
        band_y = px(0.44)
        d.rectangle([box[0], band_y - rib_w // 2, box[2], band_y + rib_w // 2], fill=GOLD)
        return img

    # ギフト箱の本体(白)
    box_left, box_right = px(0.20), px(0.80)
    box_top, box_bottom = px(0.44), px(0.84)
    d.rounded_rectangle([box_left, box_top, box_right, box_bottom], radius=px(0.04), fill=WHITE)

    # フタ(本体より少し幅広の白)
    lid_left, lid_right = px(0.15), px(0.85)
    lid_top, lid_bottom = px(0.30), px(0.44)
    d.rounded_rectangle([lid_left, lid_top, lid_right, lid_bottom], radius=px(0.04), fill=WHITE)

    # 縦リボン(金)
    rib_w = px(0.10)
    d.rectangle([cx - rib_w // 2, lid_top, cx + rib_w // 2, box_bottom], fill=GOLD)

    # リボンの結び目(金の2つの輪)
    bow_r = px(0.11)
    bow_y = lid_top - px(0.02)
    d.ellipse([cx - bow_r * 2, bow_y - bow_r, cx, bow_y + bow_r], outline=GOLD, width=max(2, px(0.045)))
    d.ellipse([cx, bow_y - bow_r, cx + bow_r * 2, bow_y + bow_r], outline=GOLD, width=max(2, px(0.045)))
    d.ellipse(
        [cx - px(0.045), bow_y - px(0.045), cx + px(0.045), bow_y + px(0.045)],
        fill=GOLD_DARK,
    )

    return img


def main():
    sizes = [256, 128, 64, 48, 32, 16]
    images = {size: draw_icon(size) for size in sizes}

    ico_path = os.path.join(BASE_DIR, "installer", "icon.ico")
    images[256].save(
        ico_path,
        format="ICO",
        sizes=[(sz, sz) for sz in sizes],
        append_images=[images[sz] for sz in sizes[1:]],
    )
    print(f"生成: {ico_path}")

    favicon_path = os.path.join(BASE_DIR, "web", "static", "favicon.ico")
    images[64].save(
        favicon_path,
        format="ICO",
        sizes=[(64, 64), (32, 32), (16, 16)],
        append_images=[images[32], images[16]],
    )
    print(f"生成: {favicon_path}")

    preview_path = os.path.join(BASE_DIR, "installer", "icon_preview.png")
    images[256].save(preview_path, format="PNG")
    print(f"生成: {preview_path}")

    # 実寸での見え方を確認するための並べ画像(左から256/64/32/16)
    sample_sizes = [256, 64, 32, 16]
    gap = 16
    width = sum(sample_sizes) + gap * (len(sample_sizes) + 1)
    sheet = Image.new("RGBA", (width, 256 + gap * 2), (245, 245, 250, 255))
    x = gap
    for sz in sample_sizes:
        sheet.paste(images[sz], (x, gap + (256 - sz) // 2), images[sz])
        x += sz + gap
    sheet_path = os.path.join(BASE_DIR, "installer", "icon_sizes.png")
    sheet.save(sheet_path, format="PNG")
    print(f"生成: {sheet_path}")


if __name__ == "__main__":
    main()
