from PIL import Image, ImageDraw, ImageFont

THAI_FONT = "/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf"

# ============================================================
# ฟอนต์ภาษาไทย
# ============================================================

THAI_FONT_PATHS = [
    THAI_FONT,
]

LATIN_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def load_font(paths, size):
    """โหลดฟอนต์จากรายการที่กำหนด"""
    for path in paths:
        try:
            return ImageFont.truetype(
                path, size, layout_engine=ImageFont.Layout.RAQM
            )
        except OSError:
            continue

    return ImageFont.load_default()


def create_thai_text_image(
    text,
    font_size=18,
    text_color="white",
    background="#7f8c8d",
    padding_x=12,
    padding_y=16,
    fixed_width=None,
    fixed_height=None,
):
    """
    สร้างภาพข้อความภาษาไทยสำหรับใช้กับ Tkinter

    รองรับ:
    - ภาษาไทย
    - วรรณยุกต์ไทย
    - เครื่องหมาย /
    - RAQM
    """

    thai_font = load_font(THAI_FONT_PATHS, font_size)
    latin_font = load_font(LATIN_FONT_PATHS, font_size)
    runs = _split_font_runs(text, thai_font, latin_font)
    run_data = [
        (run, font, font.getbbox(run), font.getlength(run))
        for run, font in runs
    ]
    text_width = sum(width for _, _, _, width in run_data)
    visual_top = min(bbox[1] for _, _, bbox, _ in run_data)
    visual_bottom = max(bbox[3] for _, _, bbox, _ in run_data)
    text_height = visual_bottom - visual_top

    image_width = max(text_width + padding_x * 2, int(fixed_width or 0))
    image_height = max(text_height + padding_y * 2, int(fixed_height or 0))
    image = Image.new("RGB", (int(image_width), int(image_height)), background)
    draw = ImageDraw.Draw(image)

    text_y = (image_height - text_height) / 2 - visual_top
    text_x = (image_width - text_width) / 2
    for run, font, _, width in run_data:
        _draw_text_part(draw, run, text_x, text_y, font, text_color)
        text_x += width
    return image


def _split_font_runs(text, thai_font, latin_font):
    """แยกช่วงภาษาไทยออกจากอักษรละตินและเครื่องหมายเพื่อเลือกฟอนต์ที่มี glyph"""
    runs = []
    current_is_thai = None
    current_text = []

    for character in text:
        is_thai = 0x0E00 <= ord(character) <= 0x0E7F
        if current_text and is_thai != current_is_thai:
            runs.append(("".join(current_text), thai_font if current_is_thai else latin_font))
            current_text = []
        current_text.append(character)
        current_is_thai = is_thai

    if current_text:
        runs.append(("".join(current_text), thai_font if current_is_thai else latin_font))
    return runs


def _draw_text_part(draw, text, x, y, font, color):
    """วาดข้อความภาษาไทยด้วย RAQM"""
    try:
        draw.text(
            (x, y),
            text,
            font=font,
            fill=color,
            layout_engine=ImageFont.Layout.RAQM,
        )
    except (KeyError, AttributeError):
        draw.text(
            (x, y),
            text,
            font=font,
            fill=color,
        )


def _draw_text(
    text,
    font,
    background,
    text_color,
    padding_x,
    padding_y,
    fixed_width=None,
    fixed_height=None,
):
    """วาดข้อความปกติที่ไม่มี /"""

    bbox = font.getbbox(text)

    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]

    image_width = width + padding_x * 2
    image_height = height + padding_y * 2

    if fixed_width is not None:
        image_width = max(image_width, int(fixed_width))

    if fixed_height is not None:
        image_height = max(image_height, int(fixed_height))

    image = Image.new(
        "RGB",
        (
            int(image_width),
            int(image_height),
        ),
        background,
    )

    draw = ImageDraw.Draw(image)

    _draw_text_part(
        draw,
        text,
        padding_x - bbox[0],
        padding_y - bbox[1],
        font,
        text_color,
    )

    return image
