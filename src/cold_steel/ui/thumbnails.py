"""Loads mod thumbnails off the main thread, fitted into one box size so the
names beside them line up.

Each shrunk thumbnail is saved in `~/.cache/cold-steel/thumbnails/`, so the next
start doesn't decode the full-size pictures again. The file name changes when
the picture does, so a stale thumbnail is never shown.
"""

from collections.abc import Sequence
from pathlib import Path

import xxhash
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter

from cold_steel.core.jobs import Job, JobContext
from cold_steel.core.mods import Mod, read_picture

# The box each thumbnail is shown in. Workshop pictures are mostly wide.
SHOWN_SIZE = QSize(96, 48)
# Stored at twice that, so thumbnails stay sharp on high-DPI screens.
PIXEL_RATIO = 2
STORED_SIZE = SHOWN_SIZE * PIXEL_RATIO


def thumbnail_job(mods: Sequence[Mod], cache_dir: Path) -> Job[dict[str, QImage]]:
    def job(ctx: JobContext) -> dict[str, QImage]:
        images: dict[str, QImage] = {}
        with_pictures = [m for m in mods if m.picture]
        for done, mod in enumerate(with_pictures):
            ctx.progress(done, len(with_pictures), "Loading thumbnails")
            image = load_thumbnail(mod, cache_dir)
            if image is not None:
                images[mod.key] = image
        return images

    return job


def load_thumbnail(mod: Mod, cache_dir: Path) -> QImage | None:
    """QImage, unlike QPixmap, is safe to use on a worker thread."""
    name = xxhash.xxh3_64_hexdigest(f"{mod.key}\0{mod.picture}\0{mod.picture_stamp}".encode())
    cached = cache_dir / f"{name}.png"
    if cached.is_file():
        image = QImage(str(cached))
        if not image.isNull():
            image.setDevicePixelRatio(PIXEL_RATIO)
            return image

    data = read_picture(mod)
    if data is None:
        return None
    picture = QImage.fromData(data)
    if picture.isNull():
        return None
    picture = picture.scaled(
        STORED_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    image = blank_thumbnail()
    image.setDevicePixelRatio(1)  # draw in stored pixels
    painter = QPainter(image)
    painter.drawImage(
        (STORED_SIZE.width() - picture.width()) // 2,
        (STORED_SIZE.height() - picture.height()) // 2,
        picture,
    )
    painter.end()
    cache_dir.mkdir(parents=True, exist_ok=True)
    image.save(str(cached))  # PNG, from the suffix
    image.setDevicePixelRatio(PIXEL_RATIO)
    return image


def blank_thumbnail() -> QImage:
    """An empty box, for mods with no picture."""
    image = QImage(STORED_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(PIXEL_RATIO)
    return image
