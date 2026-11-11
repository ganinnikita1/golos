"""Сборка графических и звуковых ресурсов «Голоса».

Скрипт идемпотентен: рисует иконки (трей, приложение, служебные) и
синтезирует короткие звуковые сигналы в ``golos/assets``. Запускается вручную
и при первом старте, если ресурсы отсутствуют::

    python -m golos.ui.assets_build

Пакеты не нужны — только Pillow из виртуального окружения проекта.
"""

from __future__ import annotations

import logging
import math
import struct
import wave
from pathlib import Path

from PIL import Image, ImageDraw

from .. import paths

logger = logging.getLogger(__name__)

#: Цвета состояний (совпадают с палитрой трея/оверлея).
STATE_COLORS = {
    "idle": ("#313844", "#232830", "#D3DCE6"),
    "recording": ("#3ED3C4", "#22A99D", "#06211E"),
    "busy": ("#5A9BF0", "#3B6FD4", "#F5F9FF"),
    "error": ("#EC6A66", "#C94743", "#FFF5F4"),
}


def _gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    """Вертикальный градиент RGBA."""
    width, height = size
    top_rgb = Image.new("RGBA", (1, 1), top).getpixel((0, 0))
    bottom_rgb = Image.new("RGBA", (1, 1), bottom).getpixel((0, 0))
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        fraction = y / max(1, height - 1)
        color = tuple(
            int(round(top_rgb[i] + (bottom_rgb[i] - top_rgb[i]) * fraction))
            for i in range(4)
        )
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _draw_mic(
    draw: ImageDraw.ImageDraw,
    size: float,
    color: str,
    *,
    cy_offset: float = 0.0,
    scale: float = 1.0,
) -> None:
    """Рисует микрофон по нормализованным координатам (0..size)."""
    cx = size / 2.0
    cap_w = 0.20 * size * scale
    cap_top = (0.22 + cy_offset) * size
    cap_bottom = (0.55 + cy_offset) * size
    draw.rounded_rectangle(
        [cx - cap_w / 2, cap_top, cx + cap_w / 2, cap_bottom],
        radius=cap_w / 2,
        fill=color,
    )
    # Дуга-держатель (нижняя половина окружности).
    arc_box = [
        cx - 0.17 * size * scale,
        (0.30 + cy_offset) * size,
        cx + 0.17 * size * scale,
        (0.64 + cy_offset) * size,
    ]
    stroke = max(1, int(round(0.045 * size * scale)))
    draw.arc(arc_box, start=0, end=180, fill=color, width=stroke)
    # Ножка и основание.
    draw.line(
        [(cx, (0.64 + cy_offset) * size), (cx, (0.75 + cy_offset) * size)],
        fill=color,
        width=stroke,
    )
    draw.line(
        [
            (cx - 0.10 * size * scale, (0.78 + cy_offset) * size),
            (cx + 0.10 * size * scale, (0.78 + cy_offset) * size),
        ],
        fill=color,
        width=stroke,
    )


def _rounded_mask(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    return mask


def _tray_icon(state: str, size: int) -> Image.Image:
    """Круглая иконка трея для состояния."""
    supersample = 6
    canvas = size * supersample
    top, bottom, glyph = STATE_COLORS[state]
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, int(round(canvas * 0.03)))
    disc = [margin, margin, canvas - margin, canvas - margin]
    disc_mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(disc_mask).ellipse(disc, fill=255)
    image.paste(_gradient((canvas, canvas), top, bottom), (0, 0), disc_mask)
    draw = ImageDraw.Draw(image)
    # Тонкое внутреннее кольцо придаёт объём.
    ring = max(1, int(round(canvas * 0.012)))
    inset = margin + ring * 2
    draw.ellipse(
        [inset, inset, canvas - inset, canvas - inset],
        outline=(255, 255, 255, 38),
        width=ring,
    )
    if state == "error":
        # Восклицательный знак вместо микрофона.
        bar_w = canvas * 0.085
        cx = canvas / 2
        draw.rounded_rectangle(
            [cx - bar_w / 2, canvas * 0.28, cx + bar_w / 2, canvas * 0.58],
            radius=bar_w / 2,
            fill=glyph,
        )
        dot_r = canvas * 0.055
        draw.ellipse(
            [cx - dot_r, canvas * 0.66, cx + dot_r, canvas * 0.66 + 2 * dot_r],
            fill=glyph,
        )
    else:
        _draw_mic(draw, float(canvas), glyph, cy_offset=0.0, scale=1.06)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _app_icon(size: int) -> Image.Image:
    """Иконка приложения: тёмный скруглённый квадрат с бирюзовым микрофоном."""
    supersample = 4 if size >= 64 else 8
    canvas = size * supersample
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    background = _gradient((canvas, canvas), "#24514E", "#10181C").convert("RGBA")
    mask = _rounded_mask(canvas, canvas * 0.23)
    image.paste(background, (0, 0), mask)
    draw = ImageDraw.Draw(image)
    _draw_mic(draw, float(canvas), "#EAFBF8", cy_offset=0.0, scale=1.18)
    # Три полоски-«волны» справа, как эквалайзер.
    accent = "#4FD1C5"
    for index, height in enumerate((0.10, 0.16, 0.08)):
        x = canvas * (0.76 + index * 0.055)
        draw.rounded_rectangle(
            [x, canvas * (0.50 - height / 2), x + canvas * 0.028,
             canvas * (0.50 + height / 2)],
            radius=canvas * 0.014,
            fill=accent,
        )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _chevron(size: int, color: str) -> Image.Image:
    supersample = 8
    canvas = size * supersample
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = max(2, int(round(canvas * 0.11)))
    draw.line(
        [
            (canvas * 0.26, canvas * 0.40),
            (canvas * 0.50, canvas * 0.62),
            (canvas * 0.74, canvas * 0.40),
        ],
        fill=color,
        width=width,
        joint="curve",
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _check(size: int, color: str) -> Image.Image:
    supersample = 8
    canvas = size * supersample
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = max(2, int(round(canvas * 0.13)))
    draw.line(
        [
            (canvas * 0.22, canvas * 0.53),
            (canvas * 0.42, canvas * 0.73),
            (canvas * 0.78, canvas * 0.28),
        ],
        fill=color,
        width=width,
        joint="curve",
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _menu_box(size: int, color: str) -> Image.Image:
    """Пустой чекбокс для пункта меню «Автозапуск»."""
    supersample = 8
    canvas = size * supersample
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = max(1, int(round(canvas * 0.08)))
    width = max(1, int(round(canvas * 0.07)))
    draw.rounded_rectangle(
        [inset, inset, canvas - inset, canvas - inset],
        radius=canvas * 0.22,
        outline=color,
        width=width,
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _write_wav(path: Path, tones: list[tuple[float, float, float]], sample_rate: int = 44100) -> None:
    """Пишет короткий сигнал: список (частота, длительность, громкость)."""
    frames: list[int] = []
    fade = 0.006
    for frequency, duration, volume in tones:
        samples = int(sample_rate * duration)
        for index in range(samples):
            t = index / sample_rate
            envelope = 1.0
            if t < fade:
                envelope = t / fade
            elif t > duration - fade:
                envelope = max(0.0, (duration - t) / fade)
            wave_value = math.sin(2.0 * math.pi * frequency * t)
            frames.append(int(32767 * volume * envelope * wave_value))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(struct.pack("<h", frame) for frame in frames))


# Состав ресурсов: относительный путь → (генератор, аргументы).
TRAY_SIZES = (16, 20, 24, 32, 40, 48, 64)
APP_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_assets(force: bool = False, target: Path | None = None) -> list[Path]:
    """Создаёт отсутствующие ресурсы; возвращает список записанных файлов."""
    root = target or paths.assets_dir()
    written: list[Path] = []

    def save(image: Image.Image, relative: str, **kwargs) -> None:
        path = root / relative
        if path.exists() and not force:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, **kwargs)
        written.append(path)

    for state in STATE_COLORS:
        for size in TRAY_SIZES:
            save(_tray_icon(state, size), f"tray/{state}_{size}.png")
        # Плоские имена — для ядра/exe и внешних интеграций.
        save(_tray_icon(state, 48), f"tray_{state}.png")

    app_images: dict[int, Image.Image] = {}
    for size in APP_SIZES:
        image = _app_icon(size)
        app_images[size] = image
        save(image, f"app/golos_{size}.png")

    ico_sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    for relative in ("app/golos.ico", "golos.ico"):
        ico_path = root / relative
        if force or not ico_path.exists():
            ico_path.parent.mkdir(parents=True, exist_ok=True)
            largest = app_images[max(APP_SIZES)]
            largest.save(ico_path, format="ICO", sizes=ico_sizes)
            written.append(ico_path)

    dark_muted = "#9CA6B4"
    light_muted = "#5E6875"
    dark_check = "#07211E"
    light_check = "#FFFFFF"
    save(_chevron(12, dark_muted), "ui/chevron_down_dark.png")
    save(_chevron(12, light_muted), "ui/chevron_down_light.png")
    save(_check(18, dark_check), "ui/check_dark.png")
    save(_check(18, light_check), "ui/check_light.png")
    save(_check(18, "#4FD1C5"), "ui/menu_check_dark.png")
    save(_check(18, "#0F9D92"), "ui/menu_check_light.png")
    save(_menu_box(16, "#5A6472"), "ui/menu_box_dark.png")
    save(_menu_box(16, "#A4AEBA"), "ui/menu_box_light.png")

    sounds = {
        "sounds/start.wav": [(660.0, 0.055, 0.16), (880.0, 0.07, 0.16)],
        "sounds/stop.wav": [(880.0, 0.055, 0.16), (660.0, 0.07, 0.16)],
        "sounds/error.wav": [(392.0, 0.09, 0.18), (0.0, 0.03, 0.0), (311.0, 0.12, 0.16)],
    }
    for relative, tones in sounds.items():
        path = root / relative
        if path.exists() and not force:
            continue
        _write_wav(path, tones)
        written.append(path)
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Сборка ресурсов интерфейса «Голос»")
    parser.add_argument("--force", action="store_true", help="перерисовать существующие файлы")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    written = build_assets(force=args.force)
    logger.info("Готово: записано файлов — %d", len(written))


if __name__ == "__main__":
    main()
