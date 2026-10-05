"""Alignment on int64 nanosecond timelines; pure numpy, no zarr or video access.

A ``bounds`` row gives the [low, high) recording interval a query may draw
samples from, so no interpolation, held value or image match crosses a pause
or an episode boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import CAMERAS


@dataclass
class Stream:
    """Samples inside an episode window and their row numbers in the raw stream."""

    times: object
    values: dict
    rows: object
    raw_count: int
    # Device frame or sample numbers at ``rows``.
    sequence: object = None


@dataclass
class Blocks:
    """Reference query times split into recording blocks at detected pauses."""

    query: object
    ids: object
    bounds: object
    source_shifts: object
    pauses: list


def brackets(times, query, max_gap_ns, bounds=None):
    import numpy as np

    if len(times) == 0:
        return np.zeros(len(query), int), np.zeros(len(query), int), np.zeros(len(query), bool)
    right = np.searchsorted(times, query, side="left")
    hi = np.clip(right, 0, len(times) - 1)
    exact = times[hi] == query
    lo = np.where(exact, hi, np.clip(right - 1, 0, len(times) - 1))
    valid = (query >= times[0]) & (query <= times[-1])
    valid &= times[hi] - times[lo] <= max_gap_ns
    if bounds is not None:
        valid &= (times[lo] >= bounds[:, 0]) & (times[hi] < bounds[:, 1])
    return lo, hi, valid


def interpolate(stream, query, field, *, max_gap_ns, pose=False, bounds=None):
    import numpy as np

    values = stream.values[field]
    width = 7 if pose else values.shape[1]
    if not len(stream.times):
        return np.zeros((len(query), width)), np.zeros(len(query), bool)
    lo, hi, valid = brackets(stream.times, query, max_gap_ns, bounds)
    a, b = values[lo], values[hi]
    valid &= np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    gap = stream.times[hi] - stream.times[lo]
    weight = np.divide(query - stream.times[lo], gap,
                       out=np.zeros(len(query), float), where=gap != 0)
    weight = np.clip(weight, 0., 1.)
    result = a + weight[:, None] * (b - a)
    if pose:
        # Pairwise shortest-path SLERP also handles q and -q identically.
        qa, qb = a[:, 3:].copy(), b[:, 3:].copy()
        na, nb = np.linalg.norm(qa, axis=1), np.linalg.norm(qb, axis=1)
        good = valid & np.isfinite(na) & np.isfinite(nb) & (na > 1e-8) & (nb > 1e-8)
        qa[~good] = qb[~good] = (0., 0., 0., 1.)
        qa /= np.linalg.norm(qa, axis=1)[:, None]
        qb /= np.linalg.norm(qb, axis=1)[:, None]
        dot = np.sum(qa * qb, axis=1)
        qb[dot < 0] *= -1
        theta = np.arccos(np.clip(np.abs(dot), 0., 1.))
        denom = np.sin(theta)
        wa, wb = 1. - weight, weight.copy()
        curved = denom > 1e-6
        wa[curved] = np.sin((1. - weight[curved]) * theta[curved]) / denom[curved]
        wb[curved] = np.sin(weight[curved] * theta[curved]) / denom[curved]
        quat = wa[:, None] * qa + wb[:, None] * qb
        quat /= np.linalg.norm(quat, axis=1)[:, None]
        result[:, 3:] = quat
        valid = good
    return result, valid


def hold(stream, query, field, *, max_age_ns, bounds=None):
    import numpy as np

    values = stream.values[field]
    if not len(stream.times):
        return np.zeros((len(query), values.shape[1])), np.zeros(len(query), bool)
    indices = np.searchsorted(stream.times, query, side="right") - 1
    safe = np.maximum(indices, 0)
    result = values[safe]
    valid = (indices >= 0) & (query - stream.times[safe] <= max_age_ns)
    if bounds is not None:
        valid &= (stream.times[safe] >= bounds[:, 0]) & (stream.times[safe] < bounds[:, 1])
    valid &= np.isfinite(result).all(axis=1)
    return result, valid


def latest_age(stream, query):
    """Time since the latest sample at or before each query; 0 for an empty stream."""
    import numpy as np

    if not len(stream.times):
        return np.zeros(len(query), np.int64)
    indices = np.maximum(np.searchsorted(stream.times, query, side="right") - 1, 0)
    return query - stream.times[indices]


def pose_vector(poses):
    import numpy as np
    from scipy.spatial.transform import Rotation

    norms = np.linalg.norm(poses[:, 3:], axis=1)
    valid = np.isfinite(poses).all(axis=1) & np.isfinite(norms) & (norms > 1e-8)
    result = np.zeros((len(poses), 6), dtype=np.float64)
    result[valid, :3] = poses[valid, :3]
    if np.any(valid):
        result[valid, 3:] = Rotation.from_quat(poses[valid, 3:]).as_rotvec()
    return result, valid


def nearest(stream, query, *, tolerance_ns, bounds=None):
    import numpy as np

    if not len(stream.times):
        return np.zeros(len(query), int), np.zeros(len(query), bool)
    right = np.clip(np.searchsorted(stream.times, query), 0, len(stream.times) - 1)
    left = np.maximum(right - 1, 0)
    indices = np.where(abs(stream.times[left] - query) <= abs(stream.times[right] - query), left, right)
    valid = abs(stream.times[indices] - query) <= tolerance_ns
    if bounds is not None:
        valid &= (stream.times[indices] >= bounds[:, 0]) & (stream.times[indices] < bounds[:, 1])
    return stream.rows[indices], valid


def match_camera(stream, query, block_ids, bounds, *, tolerance_ns, max_missing, max_age_ns):
    """Nearest frame per query; short interior holes reuse the previous frame.

    Returns raw rows, validity, reuse flags and image time minus query time.
    """
    import numpy as np

    rows, valid = nearest(stream, query, tolerance_ns=tolerance_ns, bounds=bounds)
    reused = np.zeros(len(query), bool)
    for block in np.unique(block_ids) if max_missing else ():
        indices = np.flatnonzero(block_ids == block)
        missing = indices[~valid[indices]]
        runs = np.split(missing, np.flatnonzero(np.diff(missing) != 1) + 1)
        camera_times = stream.times[(stream.times >= bounds[indices[0], 0]) & (stream.times < bounds[indices[0], 1])]
        if not len(camera_times):
            continue
        for run in runs:
            if not len(run) or len(run) > max_missing:
                continue
            # Do not extend coverage or keep the first few frames of a long hole.
            if query[run[0]] < camera_times[0] or query[run[-1]] > camera_times[-1]:
                continue
            previous = np.searchsorted(stream.times, query[run], side="right") - 1
            safe = np.maximum(previous, 0)
            good = (previous >= 0) & (stream.times[safe] >= bounds[run, 0]) & (query[run] - stream.times[safe] <= max_age_ns)
            if not np.all(good):
                continue
            rows[run] = stream.rows[safe]
            valid[run] = reused[run] = True
    if not len(stream.times):
        return rows, valid, reused, np.zeros(len(query), np.int64)
    selected = np.searchsorted(stream.rows, rows)
    return rows, valid, reused, stream.times[selected] - query


def segments(valid, times, *, max_gap_ns, block_ids=None):
    import numpy as np

    keep = np.flatnonzero(valid)
    if not len(keep):
        return []
    breaks = (np.diff(keep) != 1) | (np.diff(times[keep]) > max_gap_ns)
    if block_ids is not None:
        breaks |= np.diff(block_ids[keep]) != 0
    return np.split(keep, np.flatnonzero(breaks) + 1)


def clock_offsets(source_ms, times_ns, name):
    """Device clock minus recording clock in ms; a recording pause makes it jump ahead."""
    import numpy as np

    if not len(source_ms) or not np.isfinite(source_ms).all() or np.any(np.diff(source_ms) <= 0):
        raise ValueError(f"{name}: missing, invalid or reset source timestamps")
    return source_ms - times_ns / 1e6


def pause_seams(offsets, tolerance_ms):
    """Indices i such that a recording pause lies between samples i and i + 1."""
    import numpy as np

    return np.flatnonzero(np.diff(offsets) > tolerance_ms)


def single_block(times, start_ns, end_ns):
    """Query at the given frame times as one block spanning the episode."""
    import numpy as np

    count = len(times)
    return Blocks(times, np.zeros(count, np.int64), np.tile(np.asarray([start_ns, end_ns], np.int64), (count, 1)),
                  np.zeros(count, np.int64), [])


def camera_pauses(cameras, sources, tolerance_ms, names=CAMERAS):
    """Clock offsets and pause seams of each camera.

    ``sources`` holds each camera's device ``source_time_ms`` for its window rows.
    """
    offsets = [clock_offsets(source, camera.times, name) for name, camera, source in zip(names, cameras, sources)]
    return offsets, [pause_seams(offset, tolerance_ms) for offset in offsets]


def recording_blocks(cameras, offsets, seams, start_ns, end_ns, *, target_fps, tolerance_ms, names=CAMERAS):
    """Split at pauses all cameras agree on, then grid each shared recording block.

    ``offsets`` and ``seams`` come from ``camera_pauses`` for the same cameras.
    """
    import numpy as np

    for name, offset in zip(names, offsets):
        if np.any(np.diff(offset) < -tolerance_ms):
            raise ValueError(f"{name}: source clock or recording time moved backwards")
    if len({len(seam) for seam in seams}) != 1:
        raise ValueError("Ambiguous pause ownership: camera pause counts differ")
    splits = [np.r_[0, seam + 1, len(offset)] for offset, seam in zip(offsets, seams)]
    query_parts, id_parts, ranges, pauses = [], [], [], []
    baseline_offset = offsets[0][0]
    source_shifts = []
    for block in range(len(splits[0]) - 1):
        low = start_ns if block == 0 else max(int(c.times[splits[i][block]]) for i, c in enumerate(cameras))
        high = end_ns if block == len(splits[0]) - 2 else min(int(c.times[splits[i][block+1]-1]) + 1 for i, c in enumerate(cameras))
        if low >= high:
            raise ValueError("Ambiguous pause ownership: no shared recording interval")
        ranges.append((low, high))
        main = cameras[0].times
        available = main[(main >= low) & (main < high)]
        if not len(available):
            raise ValueError("Recording block contains no main camera frames")
        count = int(np.ceil((available[-1] - available[0]) * target_fps / 1e9)) + 1
        times = available[0] + np.rint(np.arange(count) * 1e9 / target_fps).astype(np.int64)
        times = times[times <= available[-1]]
        query_parts.append(times)
        id_parts.append(np.full(len(times), block, np.int64))
        source_shifts.append(round((offsets[0][splits[0][block]] - baseline_offset) * 1e6))
        if block:
            row = splits[0][block]
            shifts = [offset[split[block]] - offset[split[block]-1] for offset, split in zip(offsets, splits)]
            if max(shifts) - min(shifts) > tolerance_ms:
                raise ValueError("Ambiguous pause ownership: camera pause durations differ")
            pauses.append(dict(after_main_row=int(cameras[0].rows[row]), block=block,
                               removed_wait_ns=int(source_shifts[-1] - source_shifts[-2])))
    ids = np.concatenate(id_parts)
    return Blocks(np.concatenate(query_parts), ids, np.asarray(ranges, np.int64)[ids],
                  np.asarray(source_shifts)[ids], pauses)
