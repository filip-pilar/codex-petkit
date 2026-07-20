from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from petkit.contract import Contract, State


def identity_image(path: Path, color: tuple[int, int, int] = (80, 110, 220)) -> Path:
    image = Image.new("RGB", (512, 512), (0, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((140, 90, 372, 400), fill=color, outline=(20, 20, 40), width=8)
    draw.ellipse((195, 180, 225, 210), fill="white")
    draw.ellipse((287, 180, 317, 210), fill="white")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path

def row_strip(path: Path, state: State, contract: Contract, color_seed: int = 0) -> Path:
    width = state.frame_count * contract.cell_width
    image = Image.new("RGB", (width, contract.cell_height), (0, 255, 0))
    draw = ImageDraw.Draw(image)
    for index in range(state.frame_count):
        slot_left = index * contract.cell_width
        body_width = 74 + ((index + color_seed) % 3) * 4
        body_height = 92 + ((index + state.row) % 2) * 5
        left = slot_left + (contract.cell_width - body_width) // 2
        top = 70 - ((index + state.row) % 3) * 3
        color = (
            40 + (state.row * 19) % 150,
            50 + (index * 17 + color_seed) % 150,
            110 + (state.row * 9) % 120,
        )
        draw.rounded_rectangle((left, top, left + body_width, top + body_height), radius=22, fill=color)
        draw.rectangle((left + 18, top + body_height - 2, left + 30, top + body_height + 23), fill=color)
        draw.rectangle((left + body_width - 30, top + body_height - 2, left + body_width - 18, top + body_height + 23), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def replacement_frame(path: Path, contract: Contract, color: tuple[int, int, int, int]) -> Path:
    image = Image.new("RGBA", (contract.cell_width, contract.cell_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((52, 50, 140, 174), radius=28, fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path
