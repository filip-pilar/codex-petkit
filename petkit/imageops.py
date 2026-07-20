from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from petkit.contract import Contract, State


IMAGE_SUFFIXES = {".png", ".webp"}


def image_files(path: Path) -> list[Path]:
    return sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES)


def clear_transparent_rgb(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0 and (red or green or blue):
                pixels[x, y] = (0, 0, 0, 0)
    return rgba


def normalize_frame(image: Image.Image, contract: Contract, preserve_viewport: bool = False) -> Image.Image:
    rgba = clear_transparent_rgb(image)
    if preserve_viewport and rgba.size == (contract.cell_width, contract.cell_height):
        return rgba
    target = Image.new("RGBA", (contract.cell_width, contract.cell_height), (0, 0, 0, 0))
    if rgba.getbbox() is None:
        return target
    sprite = rgba if preserve_viewport else rgba.crop(rgba.getbbox())
    max_width = contract.cell_width - 10
    max_height = contract.cell_height - 10
    scale = min(max_width / sprite.width, max_height / sprite.height, 1.0)
    if scale < 1.0:
        sprite = sprite.resize(
            (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
            Image.Resampling.LANCZOS,
        )
    left = (contract.cell_width - sprite.width) // 2
    top = (contract.cell_height - sprite.height) // 2
    target.alpha_composite(sprite, (left, top))
    return clear_transparent_rgb(target)


def load_state_frames(frames_root: Path, state: State, contract: Contract) -> list[Image.Image]:
    state_dir = frames_root / state.id
    if not state_dir.is_dir():
        raise ValueError(f"missing frame directory for {state.id}: {state_dir}")
    files = image_files(state_dir)
    if len(files) != state.frame_count:
        raise ValueError(f"{state.id} requires {state.frame_count} frames; found {len(files)}")
    frames: list[Image.Image] = []
    for path in files:
        with Image.open(path) as opened:
            frames.append(normalize_frame(opened, contract, preserve_viewport=opened.size == (contract.cell_width, contract.cell_height)))
    return frames


def compose_atlas(
    frames_root: Path,
    output: Path,
    contract: Contract,
    png_copy: Path | None = None,
    *,
    standard_only: bool = False,
) -> dict[str, Any]:
    states = contract.standard_states if standard_only else contract.states
    height = contract.standard_rows * contract.cell_height if standard_only else contract.height
    atlas = Image.new("RGBA", (contract.width, height), (0, 0, 0, 0))
    used: dict[str, list[str]] = {}
    for state in states:
        files = image_files(frames_root / state.id) if (frames_root / state.id).is_dir() else []
        frames = load_state_frames(frames_root, state, contract)
        used[state.id] = [str(path) for path in files]
        for column, frame in enumerate(frames):
            atlas.alpha_composite(frame, (column * contract.cell_width, state.row * contract.cell_height))
    if not standard_only:
        neutral_row, neutral_column = contract.neutral_look_frame
        source = contract.raw["atlas"]["neutral_look_frame"]
        source_state = contract.state(str(source["source_state"]))
        neutral = load_state_frames(frames_root, source_state, contract)[int(source["source_column"])]
        atlas.alpha_composite(neutral, (neutral_column * contract.cell_width, neutral_row * contract.cell_height))
    atlas = clear_transparent_rgb(atlas)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_save(atlas, output, "WEBP" if output.suffix.lower() == ".webp" else "PNG")
    if png_copy is not None:
        png_copy.parent.mkdir(parents=True, exist_ok=True)
        _atomic_save(atlas, png_copy, "PNG")
    return {"ok": True, "atlas": str(output), "width": atlas.width, "height": atlas.height, "frames": used}


def _atomic_save(image: Image.Image, output: Path, image_format: str) -> None:
    suffix = ".webp" if image_format == "WEBP" else ".png"
    fd, temporary = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=suffix, dir=output.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        save_args: dict[str, Any] = {}
        if image_format == "WEBP":
            save_args.update(lossless=True, exact=True, method=6)
        image.save(temporary_path, format=image_format, **save_args)
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)


def _alpha_nonzero(image: Image.Image) -> int:
    return sum(1 for value in image.getchannel("A").get_flattened_data() if value > 0)


def _transparent_rgb_residue(image: Image.Image) -> int:
    return sum(
        1
        for red, green, blue, alpha in image.get_flattened_data()
        if alpha == 0 and (red or green or blue)
    )


def _edge_alpha(image: Image.Image, margin: int = 2) -> int:
    alpha = image.getchannel("A")
    width, height = image.size
    count = 0
    for y in range(height):
        for x in range(width):
            if x < margin or x >= width - margin or y < margin or y >= height - margin:
                if alpha.getpixel((x, y)) > 0:
                    count += 1
    return count


def validate_atlas(path: Path, contract: Contract, min_used_pixels: int = 64) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not path.is_file():
        return {"ok": False, "path": str(path), "errors": [{"code": "missing-atlas", "message": "atlas file does not exist"}], "warnings": []}
    byte_size = path.stat().st_size
    if byte_size > contract.max_bytes:
        errors.append({"code": "file-too-large", "message": f"atlas is {byte_size} bytes; limit is {contract.max_bytes}"})
    with Image.open(path) as opened:
        source_format = opened.format
        source_mode = opened.mode
        atlas = opened.convert("RGBA")
    if source_format not in {"PNG", "WEBP"}:
        errors.append({"code": "unsupported-format", "message": f"expected PNG or WebP, got {source_format}"})
    if atlas.size != (contract.width, contract.height):
        errors.append(
            {
                "code": "wrong-dimensions",
                "message": f"expected {contract.width}x{contract.height}, got {atlas.width}x{atlas.height}",
            }
        )
        return {
            "ok": False,
            "path": str(path),
            "format": source_format,
            "mode": source_mode,
            "byte_size": byte_size,
            "errors": errors,
            "warnings": warnings,
        }

    residue = _transparent_rgb_residue(atlas)
    if residue:
        errors.append({"code": "transparent-rgb-residue", "message": f"{residue} transparent pixels retain RGB data"})

    cells: list[dict[str, Any]] = []
    for state in contract.states:
        state_areas: list[int] = []
        for column in range(contract.columns):
            box = (
                column * contract.cell_width,
                state.row * contract.cell_height,
                (column + 1) * contract.cell_width,
                (state.row + 1) * contract.cell_height,
            )
            cell = atlas.crop(box)
            alpha_count = _alpha_nonzero(cell)
            used = contract.cell_is_used(state, column)
            record: dict[str, Any] = {
                "state": state.id,
                "row": state.row,
                "column": column,
                "used": used,
                "alpha_pixels": alpha_count,
            }
            if used:
                state_areas.append(alpha_count)
                bbox = cell.getbbox()
                record["bbox"] = list(bbox) if bbox else None
                edge_pixels = _edge_alpha(cell)
                record["edge_pixels"] = edge_pixels
                if alpha_count < min_used_pixels:
                    errors.append(
                        {
                            "code": "empty-used-cell",
                            "state": state.id,
                            "column": column,
                            "message": f"used cell has only {alpha_count} visible pixels",
                        }
                    )
                if edge_pixels:
                    warnings.append(
                        {
                            "code": "edge-contact",
                            "state": state.id,
                            "column": column,
                            "message": f"{edge_pixels} visible pixels touch the two-pixel cell margin",
                        }
                    )
            elif alpha_count:
                errors.append(
                    {
                        "code": "nonempty-unused-cell",
                        "state": state.id,
                        "column": column,
                        "message": f"unused cell contains {alpha_count} visible pixels",
                    }
                )
            cells.append(record)
        nonzero = [area for area in state_areas if area]
        if len(nonzero) > 1:
            median = sorted(nonzero)[len(nonzero) // 2]
            for column, area in enumerate(state_areas):
                if area and median and (area < median * 0.45 or area > median * 1.8):
                    warnings.append(
                        {
                            "code": "size-outlier",
                            "state": state.id,
                            "column": column,
                            "message": f"visible area {area} differs substantially from row median {median}",
                        }
                    )
    return {
        "ok": not errors,
        "path": str(path),
        "format": source_format,
        "mode": source_mode,
        "width": atlas.width,
        "height": atlas.height,
        "byte_size": byte_size,
        "transparent_rgb_residue": residue,
        "errors": errors,
        "warnings": warnings,
        "cells": cells,
    }


def inspect_frames(
    frames_root: Path,
    contract: Contract,
    min_used_pixels: int = 64,
    *,
    standard_only: bool = False,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    rows: dict[str, Any] = {}
    states = contract.standard_states if standard_only else contract.states
    for state in states:
        state_dir = frames_root / state.id
        files = image_files(state_dir) if state_dir.is_dir() else []
        if len(files) != state.frame_count:
            errors.append(
                {
                    "code": "wrong-frame-count",
                    "state": state.id,
                    "message": f"expected {state.frame_count} frames; found {len(files)}",
                }
            )
        records: list[dict[str, Any]] = []
        hashes: list[str] = []
        for index, path in enumerate(files):
            with Image.open(path) as opened:
                frame = opened.convert("RGBA")
            pixel_hash = hashlib.sha256(frame.tobytes()).hexdigest()
            hashes.append(pixel_hash)
            alpha_pixels = _alpha_nonzero(frame)
            residue = _transparent_rgb_residue(frame)
            bbox = frame.getbbox()
            edge_pixels = _edge_alpha(frame)
            record = {
                "index": index,
                "path": str(path),
                "sha256_pixels": pixel_hash,
                "width": frame.width,
                "height": frame.height,
                "alpha_pixels": alpha_pixels,
                "bbox": list(bbox) if bbox else None,
                "baseline": bbox[3] if bbox else None,
                "edge_pixels": edge_pixels,
                "transparent_rgb_residue": residue,
            }
            records.append(record)
            if frame.size != (contract.cell_width, contract.cell_height):
                errors.append(
                    {
                        "code": "wrong-frame-dimensions",
                        "state": state.id,
                        "index": index,
                        "message": f"expected {contract.cell_width}x{contract.cell_height}; got {frame.width}x{frame.height}",
                    }
                )
            if alpha_pixels < min_used_pixels:
                errors.append(
                    {
                        "code": "empty-frame",
                        "state": state.id,
                        "index": index,
                        "message": f"frame has only {alpha_pixels} visible pixels",
                    }
                )
            if residue:
                errors.append(
                    {
                        "code": "transparent-rgb-residue",
                        "state": state.id,
                        "index": index,
                        "message": f"{residue} transparent pixels retain RGB data",
                    }
                )
            if edge_pixels:
                entry = {
                    "code": "edge-contact",
                    "state": state.id,
                    "index": index,
                    "message": f"{edge_pixels} visible pixels touch the two-pixel margin",
                }
                if state.row < contract.standard_rows:
                    errors.append(entry)
                else:
                    warnings.append(entry)
        unique_count = len(set(hashes))
        if len(hashes) == state.frame_count and unique_count == 1:
            errors.append(
                {
                    "code": "static-row",
                    "state": state.id,
                    "message": "every frame is pixel-identical",
                }
            )
        elif len(hashes) == state.frame_count and unique_count < len(hashes):
            errors.append(
                {
                    "code": "duplicate-frame",
                    "state": state.id,
                    "message": f"{len(hashes) - unique_count} frame(s) are exact duplicates; every beat must contribute",
                }
            )
        elif hashes and unique_count < max(2, math.ceil(len(hashes) * 0.6)):
            warnings.append(
                {
                    "code": "duplicate-frames",
                    "state": state.id,
                    "message": f"only {unique_count} of {len(hashes)} frames are pixel-distinct",
                }
            )
        if len(hashes) > 1 and hashes[0] == hashes[-1]:
            errors.append(
                {
                    "code": "loop-duplicate-frame",
                    "state": state.id,
                    "message": "the final frame duplicates the first frame instead of providing a distinct loop return",
                }
            )
        visible = [record for record in records if record["bbox"]]
        if visible:
            areas = sorted(record["alpha_pixels"] for record in visible)
            median_area = areas[len(areas) // 2]
            baselines = [record["baseline"] for record in visible]
            baseline_span = max(baselines) - min(baselines)
            if baseline_span > 28 and state.id != "jumping":
                warnings.append(
                    {
                        "code": "baseline-variation",
                        "state": state.id,
                        "message": f"baseline spans {baseline_span} pixels",
                    }
                )
            for record in visible:
                area = record["alpha_pixels"]
                if median_area and (area < median_area * 0.45 or area > median_area * 1.8):
                    warnings.append(
                        {
                            "code": "size-outlier",
                            "state": state.id,
                            "index": record["index"],
                            "message": f"visible area {area} differs substantially from row median {median_area}",
                        }
                    )
        else:
            median_area = 0
            baseline_span = 0
        rows[state.id] = {
            "expected_count": state.frame_count,
            "actual_count": len(files),
            "unique_frame_count": unique_count,
            "median_alpha_pixels": median_area,
            "baseline_span": baseline_span,
            "frames": records,
        }
    return {"ok": not errors, "frames_root": str(frames_root), "errors": errors, "warnings": warnings, "rows": rows}


def checkerboard(size: tuple[int, int], square: int = 12) -> Image.Image:
    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)
    colors = ((238, 238, 238), (208, 208, 208))
    for y in range(0, size[1], square):
        for x in range(0, size[0], square):
            color = colors[((x // square) + (y // square)) % 2]
            draw.rectangle((x, y, min(x + square - 1, size[0] - 1), min(y + square - 1, size[1] - 1)), fill=color)
    return canvas


def make_contact_sheet(atlas_path: Path, output: Path, contract: Contract, scale: float = 0.5) -> dict[str, Any]:
    with Image.open(atlas_path) as opened:
        atlas = opened.convert("RGBA")
    if atlas.size != (contract.width, contract.height):
        raise ValueError("cannot create contact sheet from an atlas with wrong dimensions")
    cell_width = max(1, round(contract.cell_width * scale))
    cell_height = max(1, round(contract.cell_height * scale))
    label_width = 132
    header_height = 28
    sheet = Image.new("RGB", (label_width + contract.columns * cell_width, header_height + contract.rows * cell_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for column in range(contract.columns):
        draw.text((label_width + column * cell_width + 4, 8), str(column), fill="black", font=font)
    for state in contract.states:
        top = header_height + state.row * cell_height
        draw.text((6, top + 6), f"{state.row}  {state.id}", fill="black", font=font)
        for column in range(contract.columns):
            source = atlas.crop(
                (
                    column * contract.cell_width,
                    state.row * contract.cell_height,
                    (column + 1) * contract.cell_width,
                    (state.row + 1) * contract.cell_height,
                )
            ).resize((cell_width, cell_height), Image.Resampling.LANCZOS)
            background = checkerboard((cell_width, cell_height), max(4, round(12 * scale)))
            background.paste(source, (0, 0), source)
            left = label_width + column * cell_width
            sheet.paste(background, (left, top))
            draw.rectangle((left, top, left + cell_width - 1, top + cell_height - 1), outline=(160, 160, 160))
            if not contract.cell_is_used(state, column):
                draw.line((left, top, left + cell_width, top + cell_height), fill=(205, 80, 80), width=1)
                draw.line((left + cell_width, top, left, top + cell_height), fill=(205, 80, 80), width=1)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_save(sheet.convert("RGBA"), output, "PNG")
    return {"ok": True, "contact_sheet": str(output)}


def make_standard_filmstrips(frames_root: Path, output_dir: Path, contract: Contract) -> dict[str, Any]:
    """Render one lossless, normal-cell-size labeled strip per standard state."""
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    header_height = 30
    font = ImageFont.load_default()
    for state in contract.standard_states:
        frames = load_state_frames(frames_root, state, contract)
        width = state.frame_count * contract.cell_width
        strip = Image.new("RGB", (width, header_height + contract.cell_height), "white")
        draw = ImageDraw.Draw(strip)
        draw.text((6, 8), f"{state.id} — inspect every frame and {state.frame_count - 1}→0 loop wrap", fill="black", font=font)
        for index, frame in enumerate(frames):
            background = checkerboard(frame.size)
            background.paste(frame, (0, 0), frame)
            left = index * contract.cell_width
            strip.paste(background, (left, header_height))
            draw.rectangle(
                (left, header_height, left + contract.cell_width - 1, header_height + contract.cell_height - 1),
                outline=(120, 120, 120),
            )
            draw.rectangle((left + 4, header_height + 4, left + 23, header_height + 20), fill="white", outline=(80, 80, 80))
            draw.text((left + 10, header_height + 7), str(index), fill="black", font=font)
        output = output_dir / f"{state.id}.png"
        _atomic_save(strip.convert("RGBA"), output, "PNG")
        outputs[state.id] = str(output)
    return {"ok": True, "filmstrips": outputs}


def make_before_after_sheet(before: Path, after: Path, output: Path) -> dict[str, Any]:
    with Image.open(before) as opened:
        left = opened.convert("RGB")
    with Image.open(after) as opened:
        right = opened.convert("RGB")
    label_height = 30
    gap = 12
    width = left.width + right.width + gap
    height = label_height + max(left.height, right.height)
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    draw.text((8, 9), "BEFORE", fill="black", font=font)
    draw.text((left.width + gap + 8, 9), "AFTER", fill="black", font=font)
    sheet.paste(left, (0, label_height))
    sheet.paste(right, (left.width + gap, label_height))
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_save(sheet.convert("RGBA"), output, "PNG")
    return {"ok": True, "before": str(before), "after": str(after), "comparison_sheet": str(output)}


def render_previews(
    frames_root: Path,
    output_dir: Path,
    contract: Contract,
    scale: int = 2,
    *,
    standard_only: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    states = contract.standard_states if standard_only else contract.states
    for state in states:
        frames = load_state_frames(frames_root, state, contract)
        rendered: list[Image.Image] = []
        for frame in frames:
            background = checkerboard(frame.size)
            background.paste(frame, (0, 0), frame)
            if scale != 1:
                background = background.resize(
                    (background.width * scale, background.height * scale),
                    Image.Resampling.NEAREST,
                )
            rendered.append(background)
        output = output_dir / f"{state.id}.gif"
        rendered[0].save(
            output,
            save_all=True,
            append_images=rendered[1:],
            duration=list(state.durations_ms),
            loop=0,
            disposal=2,
            optimize=False,
        )
        outputs[state.id] = str(output)
    return {"ok": True, "previews": outputs}


def render_state_preview(
    frames_root: Path,
    output: Path,
    state: State,
    contract: Contract,
    scale: int = 2,
) -> dict[str, Any]:
    frames = load_state_frames(frames_root, state, contract)
    rendered: list[Image.Image] = []
    for frame in frames:
        background = checkerboard(frame.size)
        background.paste(frame, (0, 0), frame)
        if scale != 1:
            background = background.resize(
                (background.width * scale, background.height * scale),
                Image.Resampling.NEAREST,
            )
        rendered.append(background)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered[0].save(
        output,
        save_all=True,
        append_images=rendered[1:],
        duration=list(state.durations_ms),
        loop=0,
        disposal=2,
        optimize=False,
    )
    return {"ok": True, "state": state.id, "preview": str(output)}


def parse_hex_color(value: str) -> tuple[int, int, int]:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6 or any(character not in "0123456789abcdefABCDEF" for character in cleaned):
        raise ValueError(f"invalid chroma key {value!r}; expected #RRGGBB")
    return tuple(int(cleaned[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def remove_chroma_background(image: Image.Image, key: tuple[int, int, int], threshold: float) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    threshold_squared = threshold * threshold
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            distance = (red - key[0]) ** 2 + (green - key[1]) ** 2 + (blue - key[2]) ** 2
            if distance <= threshold_squared:
                pixels[x, y] = (0, 0, 0, 0)
            elif alpha == 0:
                pixels[x, y] = (0, 0, 0, 0)
    return rgba


def connected_components(image: Image.Image, alpha_threshold: int = 16) -> list[dict[str, Any]]:
    alpha = image.getchannel("A")
    width, height = image.size
    data = alpha.tobytes()
    visited = bytearray(width * height)
    components: list[dict[str, Any]] = []
    for start, value in enumerate(data):
        if value <= alpha_threshold or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        pixels: list[int] = []
        min_x = max_x = start % width
        min_y = max_y = start // width
        while stack:
            current = stack.pop()
            pixels.append(current)
            x = current % width
            y = current // width
            min_x, min_y = min(min_x, x), min(min_y, y)
            max_x, max_y = max(max_x, x), max(max_y, y)
            neighbors = []
            if x:
                neighbors.append(current - 1)
            if x + 1 < width:
                neighbors.append(current + 1)
            if y:
                neighbors.append(current - width)
            if y + 1 < height:
                neighbors.append(current + width)
            for neighbor in neighbors:
                if not visited[neighbor] and data[neighbor] > alpha_threshold:
                    visited[neighbor] = 1
                    stack.append(neighbor)
        components.append(
            {
                "pixels": pixels,
                "area": len(pixels),
                "bbox": (min_x, min_y, max_x + 1, max_y + 1),
                "center_x": (min_x + max_x + 1) / 2,
            }
        )
    return components


def component_groups(strip: Image.Image, frame_count: int) -> list[list[dict[str, Any]]] | None:
    components = connected_components(strip)
    if not components:
        return None
    largest = max(component["area"] for component in components)
    seeds = [component for component in components if component["area"] >= max(120, largest * 0.20)]
    if len(seeds) < frame_count:
        seeds = sorted(components, key=lambda item: item["area"], reverse=True)[:frame_count]
    if len(seeds) < frame_count:
        return None
    seeds = sorted(sorted(seeds, key=lambda item: item["area"], reverse=True)[:frame_count], key=lambda item: item["center_x"])
    seed_ids = {id(seed) for seed in seeds}
    groups: list[list[dict[str, Any]]] = [[seed] for seed in seeds]
    noise_threshold = max(12, largest * 0.002)
    for component in components:
        if id(component) in seed_ids or component["area"] < noise_threshold:
            continue
        target = min(range(len(seeds)), key=lambda index: abs(seeds[index]["center_x"] - component["center_x"]))
        groups[target].append(component)
    return groups


def component_group_image(source: Image.Image, components: list[dict[str, Any]], padding: int = 4) -> Image.Image:
    width, height = source.size
    left = max(0, min(item["bbox"][0] for item in components) - padding)
    top = max(0, min(item["bbox"][1] for item in components) - padding)
    right = min(width, max(item["bbox"][2] for item in components) + padding)
    bottom = min(height, max(item["bbox"][3] for item in components) + padding)
    output = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    source_pixels = source.load()
    output_pixels = output.load()
    for component in components:
        for pixel_index in component["pixels"]:
            x = pixel_index % width
            y = pixel_index // width
            output_pixels[x - left, y - top] = source_pixels[x, y]
    return output


def extract_row_strip(
    strip_path: Path,
    output_dir: Path,
    state: State,
    contract: Contract,
    chroma_key: str,
    threshold: float,
    method: str = "auto",
) -> dict[str, Any]:
    requested_method = method
    if method == "auto" and state.id == "jumping":
        method = "stable-slots"
    with Image.open(strip_path) as opened:
        strip = remove_chroma_background(opened, parse_hex_color(chroma_key), threshold)
    frames: list[Image.Image] | None = None
    used_method = method
    groups = component_groups(strip, state.frame_count) if method in {"auto", "components", "stable-slots", "motion-components"} else None
    if method in {"auto", "components"} and groups is not None:
        frames = [normalize_frame(component_group_image(strip, group), contract) for group in groups]
        used_method = "components"
    elif method == "components":
        raise ValueError(f"could not isolate {state.frame_count} sprite components in {strip_path}")
    if frames is None and method == "stable-slots" and groups is not None:
        bboxes = [
            (
                min(item["bbox"][0] for item in group),
                min(item["bbox"][1] for item in group),
                max(item["bbox"][2] for item in group),
                max(item["bbox"][3] for item in group),
            )
            for group in groups
        ]
        shared_top = max(0, min(box[1] for box in bboxes) - 4)
        shared_bottom = min(strip.height, max(box[3] for box in bboxes) + 4)
        viewport_width = max(box[2] - box[0] for box in bboxes) + 8
        frames = []
        for group, box in zip(groups, bboxes):
            grouped = component_group_image(strip, group, 4)
            viewport = Image.new("RGBA", (viewport_width, shared_bottom - shared_top), (0, 0, 0, 0))
            viewport.alpha_composite(grouped, ((viewport_width - grouped.width) // 2, max(0, box[1] - 4) - shared_top))
            frames.append(normalize_frame(viewport, contract, preserve_viewport=True))
        used_method = "stable-slots"
    if frames is None and method == "motion-components" and groups is not None:
        bboxes = [
            (
                min(item["bbox"][0] for item in group),
                min(item["bbox"][1] for item in group),
                max(item["bbox"][2] for item in group),
                max(item["bbox"][3] for item in group),
            )
            for group in groups
        ]
        sprites = [component_group_image(strip, group, 4) for group in groups]
        max_sprite_width = max(sprite.width for sprite in sprites)
        max_sprite_height = max(sprite.height for sprite in sprites)
        travel_budget = 20
        horizontal_margin = 5
        vertical_margin = 5
        scale = min(
            (contract.cell_width - horizontal_margin * 2) / max_sprite_width,
            (contract.cell_height - vertical_margin * 2 - travel_budget) / max_sprite_height,
            1.0,
        )
        bottoms = [box[3] for box in bboxes]
        bottom_span = max(bottoms) - min(bottoms)
        frames = []
        for sprite, bottom in zip(sprites, bottoms):
            if scale < 1.0:
                sprite = sprite.resize(
                    (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            lift = 0 if bottom_span == 0 else round(travel_budget * (max(bottoms) - bottom) / bottom_span)
            frame = Image.new("RGBA", (contract.cell_width, contract.cell_height), (0, 0, 0, 0))
            left = (contract.cell_width - sprite.width) // 2
            top = contract.cell_height - vertical_margin - sprite.height - lift
            frame.alpha_composite(sprite, (left, top))
            frames.append(clear_transparent_rgb(frame))
        used_method = "motion-components"
    elif frames is None and method == "motion-components":
        raise ValueError(f"could not isolate {state.frame_count} motion components in {strip_path}")
    if frames is None:
        frames = []
        slot_width = strip.width / state.frame_count
        for index in range(state.frame_count):
            left = round(index * slot_width)
            right = round((index + 1) * slot_width)
            frames.append(normalize_frame(strip.crop((left, 0, right, strip.height)), contract))
        used_method = "slots"

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    for index, frame in enumerate(frames):
        output = output_dir / f"{index:02d}.png"
        _atomic_save(frame, output, "PNG")
        outputs.append(str(output))
    manifest = {
        "state": state.id,
        "source": str(strip_path),
        "method": used_method,
        "requested_method": requested_method,
        "chroma_key": chroma_key.upper(),
        "chroma_threshold": threshold,
        "frames": outputs,
    }
    (output_dir / "row-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, **manifest}


def mirror_state(source_dir: Path, output_dir: Path, expected_count: int) -> dict[str, Any]:
    files = image_files(source_dir)
    if len(files) != expected_count:
        raise ValueError(f"expected {expected_count} source frames; found {len(files)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for index, path in enumerate(files):
        with Image.open(path) as opened:
            mirrored = opened.convert("RGBA").transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        output = output_dir / f"{index:02d}.png"
        _atomic_save(clear_transparent_rgb(mirrored), output, "PNG")
        outputs.append(str(output))
    return {"ok": True, "frames": outputs, "preserved_temporal_order": True}


def frame_hashes(atlas_path: Path, contract: Contract) -> dict[str, list[str]]:
    with Image.open(atlas_path) as opened:
        atlas = opened.convert("RGBA")
    if atlas.width != contract.width or atlas.height not in {
        contract.standard_rows * contract.cell_height,
        contract.height,
    }:
        raise ValueError(f"atlas has unsupported dimensions: {atlas.size}")
    available_rows = atlas.height // contract.cell_height
    result: dict[str, list[str]] = {}
    for state in (state for state in contract.states if state.row < available_rows):
        hashes: list[str] = []
        for column in range(state.frame_count):
            cell = atlas.crop(
                (
                    column * contract.cell_width,
                    state.row * contract.cell_height,
                    (column + 1) * contract.cell_width,
                    (state.row + 1) * contract.cell_height,
                )
            )
            hashes.append(hashlib.sha256(cell.tobytes()).hexdigest())
        result[state.id] = hashes
    return result


def compare_atlases(before: Path, after: Path, contract: Contract) -> dict[str, Any]:
    left = frame_hashes(before, contract)
    right = frame_hashes(after, contract)
    changed: dict[str, list[int]] = {}
    unchanged: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    for state in contract.states:
        if state.id not in left:
            added.append(state.id)
            continue
        if state.id not in right:
            removed.append(state.id)
            continue
        indexes = [index for index, pair in enumerate(zip(left[state.id], right[state.id])) if pair[0] != pair[1]]
        if indexes:
            changed[state.id] = indexes
        else:
            unchanged.append(state.id)
    return {
        "ok": True,
        "before": str(before),
        "after": str(after),
        "changed_states": changed,
        "unchanged_states": unchanged,
        "added_states": added,
        "removed_states": removed,
    }


def extract_atlas_frames(atlas_path: Path, frames_root: Path, contract: Contract) -> dict[str, Any]:
    with Image.open(atlas_path) as opened:
        atlas = opened.convert("RGBA")
    if atlas.size != (contract.width, contract.height):
        raise ValueError(
            f"expected a {contract.width}x{contract.height} atlas; got {atlas.width}x{atlas.height}"
        )
    outputs: dict[str, list[str]] = {}
    for state in contract.states:
        state_dir = frames_root / state.id
        state_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for column in range(state.frame_count):
            frame = atlas.crop(
                (
                    column * contract.cell_width,
                    state.row * contract.cell_height,
                    (column + 1) * contract.cell_width,
                    (state.row + 1) * contract.cell_height,
                )
            )
            output = state_dir / f"{column:02d}.png"
            _atomic_save(clear_transparent_rgb(frame), output, "PNG")
            paths.append(str(output))
        outputs[state.id] = paths
    return {"ok": True, "atlas": str(atlas_path), "frames_root": str(frames_root), "frames": outputs}
