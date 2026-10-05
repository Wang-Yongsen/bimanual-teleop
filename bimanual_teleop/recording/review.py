"""Human review kept next to each episode in review.json, plus review previews.

Every review.json field is optional::

    {"decision": "keep" | "reject", "note": "...", "reviewed_at": "<ISO time>",
     "pause_reviews": {"<main-camera row after a pause>": {"merge": true, "reason": "..."}}}

Conversion skips rejected episodes. A pause review here overrides one for the
same seam in the conversion YAML.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path

from .policy import validate_pause_reviews
from .schema import CAMERA_FPS, CAMERAS, IMAGE_HEIGHT, IMAGE_WIDTH, rgb_stream
from .storage import write_json

REVIEW = "review.json"
PREVIEW = "preview.mp4"
KEEP, REJECT = "keep", "reject"
_FIELDS = {"decision", "note", "reviewed_at", "pause_reviews"}
_MISSING_TILE = (80, 0, 0)


def validate_review(review, where):
    if not isinstance(review, dict):
        raise ValueError(f"{where} must be a JSON object")
    unknown = set(review) - _FIELDS
    if unknown:
        raise ValueError(f"{where}: unknown fields {sorted(unknown)}")
    if review.get("decision", KEEP) not in (KEEP, REJECT):
        raise ValueError(f"{where}: decision must be keep or reject")
    for key in ("note", "reviewed_at"):
        if not isinstance(review.get(key, ""), str):
            raise ValueError(f"{where}: {key} must be a string")
    validate_pause_reviews(review.get("pause_reviews", {}), f"{where}: pause_reviews")


def read_review(episode):
    """The validated review of ``episode``, or None without review.json."""
    path = Path(episode) / REVIEW
    if not path.is_file():
        return None
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON: {error}") from error
    validate_review(review, path)
    return review


def write_review(episode, **changes):
    """Merge ``changes`` into review.json and stamp reviewed_at; None removes a field."""
    path = Path(episode) / REVIEW
    review = read_review(episode) or {}
    for key, value in changes.items():
        if value is None:
            review.pop(key, None)
        else:
            review[key] = value
    review["reviewed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    validate_review(review, path)
    write_json(path, review)
    return review


def detect_pauses(raw, descriptor, tolerance_ms):
    """Pauses on the main camera inside the episode window.

    Each item has the raw main-camera row after the pause, which is the key of
    a pause review and also the preview frame index, and the removed wait.
    """
    import numpy as np

    from .timeline import clock_offsets, pause_seams

    group = raw[rgb_stream(CAMERAS[0])]
    times = np.asarray(group["time_ns"][:])
    rows = np.flatnonzero((times >= descriptor["start_ns"]) & (times < descriptor["end_ns"]))
    offsets = clock_offsets(np.asarray(group["source_time_ms"][:])[rows], times[rows], CAMERAS[0])
    return [dict(after_main_row=int(rows[seam + 1]), removed_wait_ms=float(offsets[seam + 1] - offsets[seam]))
            for seam in pause_seams(offsets, tolerance_ms)]


def _frames(container, width, height):
    for frame in container.decode(video=0):
        yield frame.reformat(width=width, height=height, format="rgb24").to_ndarray()


def make_preview(episode, output=None, *, scale=.5, tolerance_ms=20, progress=None):
    """Write the three cameras side by side, one output frame per main-camera frame.

    Preview frame ``i`` is main-camera row ``i``; a tile is dark red when that
    camera has no frame within ``tolerance_ms`` of it.
    """
    import av
    import numpy as np
    import zarr

    from .timeline import Stream, nearest

    episode = Path(episode)
    output = episode / PREVIEW if output is None else Path(output)
    raw = zarr.open_group(str(episode / "raw.zarr"), mode="r")
    times = [np.asarray(raw[f"{rgb_stream(camera)}/time_ns"][:]) for camera in CAMERAS]
    main = times[0]
    mappings = [np.arange(len(main))]
    for camera_times in times[1:]:
        stream = Stream(camera_times, {}, np.arange(len(camera_times)), len(camera_times))
        rows, valid = nearest(stream, main, tolerance_ns=round(tolerance_ms * 1e6))
        mappings.append(np.where(valid, rows, -1))
    width, height = round(IMAGE_WIDTH * scale) // 2 * 2, round(IMAGE_HEIGHT * scale) // 2 * 2
    missing = np.empty((height, width, 3), np.uint8)
    missing[:] = _MISSING_TILE
    if progress is not None:
        progress.total(len(main))
    temporary = output.with_name(f".{output.stem}.partial{output.suffix}")
    try:
        with ExitStack() as stack:
            decoders = [_frames(stack.enter_context(av.open(str(episode / f"{camera}.mp4"))), width, height)
                        for camera in CAMERAS]
            container = stack.enter_context(av.open(str(temporary), "w", format="mp4"))
            encoder = container.add_stream("libx264", rate=CAMERA_FPS)
            encoder.width, encoder.height, encoder.pix_fmt = width * len(CAMERAS), height, "yuv420p"
            encoder.options = {"crf": "28", "preset": "veryfast"}
            current = [(-1, None)] * len(CAMERAS)
            for index in range(len(main)):
                tiles = []
                for camera, (decoder, mapping) in enumerate(zip(decoders, mappings)):
                    row = int(mapping[index])
                    if row < 0:
                        tiles.append(missing)
                        continue
                    position, image = current[camera]
                    while position < row:
                        image = next(decoder, None)
                        if image is None:
                            raise ValueError(f"{CAMERAS[camera]}.mp4 has fewer frames than its timestamp table")
                        position += 1
                    current[camera] = position, image
                    tiles.append(image)
                frame = av.VideoFrame.from_ndarray(np.concatenate(tiles, axis=1), format="rgb24")
                for packet in encoder.encode(frame):
                    container.mux(packet)
                if progress is not None:
                    progress.advance()
            for packet in encoder.encode():
                container.mux(packet)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
