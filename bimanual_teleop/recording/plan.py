"""Plan one complete episode without writing anything.

The plan holds the reference query times, each camera's selected raw frame,
per-reason validity masks, the continuous output runs and pause decisions.
Reports and dataset writing both read the same plan, so a dry run reports
exactly what a conversion would write.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .schema import CAMERAS, DEPTH_STREAM, MAIN_CAMERA, RAW_FIELDS, SIDES, rgb_stream
from .timeline import (Blocks, Stream, brackets, camera_pauses, hold, interpolate, latest_age, match_camera,
                       nearest, pose_vector, recording_blocks, segments, single_block)

# Per side, in the order of meta/state_interpolated columns.
STATE_FIELDS = (("robot_joint", "arms", "joint_pos"), ("robot_eef_pose", "arms", "eef_pose"),
                ("wrench", "arms", "wrench"), ("hand_joint", "hands", "joint_pos"))


def read_stream(raw, name, fields, start_ns, end_ns):
    """Samples of ``raw[name]`` inside the episode window [start_ns, end_ns)."""
    import numpy as np

    if name not in raw:
        raise ValueError(f"Missing required raw stream: {name}")
    group = raw[name]
    times = np.asarray(group["time_ns"][:])
    sequence = np.asarray(group["sequence"][:])
    if (times.ndim != 1 or times.dtype != np.dtype("int64")
            or sequence.shape != times.shape or sequence.dtype != np.dtype("int64")):
        raise ValueError(f"{name}: time_ns and sequence must be equally sized int64 vectors")
    if len(times) > 1 and np.any(times[1:] <= times[:-1]):
        raise ValueError(f"{name}: time_ns must be strictly increasing")
    rows = np.flatnonzero((times >= start_ns) & (times < end_ns))
    values = {}
    for field, width in fields.items():
        array = group[field]
        if array.shape != (len(times), width):
            raise ValueError(f"{name}/{field}: expected shape ({len(times)}, {width})")
        values[field] = np.asarray(array[:], dtype=np.float64)[rows]
    if name.startswith("cameras/"):
        if group["source_time_ms"].shape != times.shape:
            raise ValueError(f"{name}: source_time_ms length differs from timestamps")
    return Stream(times[rows], values, rows, len(times), sequence[rows])


@dataclass
class EpisodePlan:
    source: str
    path: Path
    start_ns: int
    end_ns: int
    blocks: Blocks
    # Recording time of each reference frame, and the same times with reviewed pauses compressed.
    query: object
    training_time: object
    # Each camera's frames inside the window and the window indices i with a pause after frame i.
    cameras: list
    camera_seams: list
    # Sample times inside the window of every state and command stream.
    stream_times: dict
    camera_rows: list
    camera_reused: list
    camera_offsets: list
    camera_source_ms: list
    masks: dict
    valid: object
    # Output runs, and valid runs shorter than the policy's min_segment_frames.
    runs: list
    short_runs: list
    pauses: list
    state: dict
    action: object
    state_interpolated: object
    # Time between the two samples each state value is interpolated from, in meta/state_interpolated column order.
    state_gap_ns: object
    command_age_ns: object
    # None unless depth is included.
    depth_rows: object

    @property
    def keep(self):
        """Reference frames written to the dataset, in output order."""
        import numpy as np

        return np.concatenate(self.runs) if self.runs else np.zeros(0, np.int64)

    def provenance(self):
        """Per-frame meta/ arrays for every reference frame."""
        import numpy as np

        return {
            "source_time_ns": self.query + self.blocks.source_shifts,
            "recording_time_ns": self.query,
            "recording_block": self.blocks.ids,
            "camera_source_row": np.stack(self.camera_rows, axis=1),
            "camera_reused": np.stack(self.camera_reused, axis=1),
            "camera_time_offset_ns": np.stack(self.camera_offsets, axis=1),
            "camera_source_time_ms": np.stack(self.camera_source_ms, axis=1),
            "state_interpolated": self.state_interpolated,
            "command_age_ns": self.command_age_ns,
        }

    def segment_records(self, offset):
        """meta.attrs['segments'] entries for the runs written from ``offset``."""
        result = []
        for run in self.runs:
            result.append({"source_episode": self.source,
                           "reference_frame_start": int(self.camera_rows[0][run[0]]),
                           "reference_frame_end": int(self.camera_rows[0][run[-1]]) + 1,
                           "start_ns": int(self.query[run[0]]), "end_ns": int(self.query[run[-1]]),
                           "output_start": offset, "output_end": offset + len(run)})
            offset += len(run)
        return result


def plan_episode(path, descriptor, raw, policy, *, source, action_space, include_depth, pause_reviews=None):
    """Align one complete episode under ``policy``.

    ``pause_reviews`` maps the main-camera row after a detected pause to
    {merge, reason}; reviews of seams that were not detected are an error.
    """
    import numpy as np

    start, end = descriptor["start_ns"], descriptor["end_ns"]
    if (not isinstance(start, int) or not isinstance(end, int) or end <= start):
        raise ValueError(f"{path}: invalid episode start_ns/end_ns")
    cameras = [read_stream(raw, rgb_stream(camera), {}, start, end) for camera in CAMERAS]
    sources = [np.asarray(raw[f"{rgb_stream(camera)}/source_time_ms"][:]) for camera in CAMERAS]
    offsets, seams = camera_pauses(cameras, [capture_ms[camera.rows] for capture_ms, camera in zip(sources, cameras)],
                                   policy.pause_offset_tolerance_ms)
    if policy.target_fps is None:
        blocks = single_block(cameras[0].times, start, end)
    else:
        blocks = recording_blocks(cameras, offsets, seams, start, end, target_fps=policy.target_fps,
                                  tolerance_ms=policy.pause_offset_tolerance_ms)
    query, bounds = blocks.query, blocks.bounds
    masks = {}
    camera_rows, camera_reused, camera_offsets, camera_source_ms = [], [], [], []
    for camera, stream, capture_ms in zip(CAMERAS, cameras, sources):
        rows, mask, reused, offsets = match_camera(
            stream, query, blocks.ids, bounds, tolerance_ns=policy.camera_tolerance_ns,
            max_missing=policy.max_missing_camera_frames, max_age_ns=policy.camera_reuse_age_ns)
        camera_rows.append(rows)
        camera_reused.append(reused)
        camera_offsets.append(offsets)
        camera_source_ms.append(capture_ms[rows] if len(capture_ms) else np.full(len(rows), np.nan))
        masks[f"{camera}_unmatched"] = mask

    state = {key: [] for key, _, _ in STATE_FIELDS}
    arm_actions, hand_actions, interpolated, state_gaps, command_ages = [], [], [], [], []
    stream_times = {}
    for side in SIDES:
        streams = {kind: read_stream(raw, f"{kind}/{side}", RAW_FIELDS[f"{kind}/{side}"], start, end)
                   for kind in ("arms", "hands")}
        stream_times.update({f"{kind}/{side}": stream.times for kind, stream in streams.items()})
        for key, kind, field in STATE_FIELDS:
            stream = streams[kind]
            result, mask = interpolate(stream, query, field, pose=field == "eef_pose",
                                       max_gap_ns=policy.state_gap_ns, bounds=bounds)
            lo, hi, _ = brackets(stream.times, query, policy.state_gap_ns, bounds)
            interpolated.append((lo != hi) & mask)
            state_gaps.append(stream.times[hi] - stream.times[lo] if len(stream.times)
                              else np.zeros(len(query), np.int64))
            if field == "eef_pose":
                result, pose_valid = pose_vector(result)
                mask &= pose_valid
            state[key].append(result)
            masks[f"{side}_{key}_invalid_or_gap"] = mask
        command_field = "eef_pose" if action_space == "eef" else "joint_pos"
        arm_command = read_stream(raw, f"arm_commands/{side}", {command_field: 7}, start, end)
        stream_times[f"arm_commands/{side}"] = arm_command.times
        action, mask = hold(arm_command, query, command_field, max_age_ns=policy.command_age_ns, bounds=bounds)
        command_ages.append(latest_age(arm_command, query))
        if action_space == "eef":
            action, pose_valid = pose_vector(action)
            mask &= pose_valid
        arm_actions.append(action)
        masks[f"{side}_arm_command_invalid_or_stale"] = mask
        hand_command = read_stream(raw, f"hand_commands/{side}", {"joint_pos": 20}, start, end)
        stream_times[f"hand_commands/{side}"] = hand_command.times
        action, mask = hold(hand_command, query, "joint_pos", max_age_ns=policy.command_age_ns, bounds=bounds)
        command_ages.append(latest_age(hand_command, query))
        hand_actions.append(action)
        masks[f"{side}_hand_command_invalid_or_stale"] = mask

    depth_rows = None
    if include_depth and DEPTH_STREAM in raw:
        depth = read_stream(raw, DEPTH_STREAM, {}, start, end)
        depth_rows, mask = nearest(depth, query, tolerance_ns=policy.camera_tolerance_ns, bounds=bounds)
        image = raw[f"{DEPTH_STREAM}/image"]
        if len(image.shape) != 3 or image.shape[0] != depth.raw_count or image.dtype != np.dtype("uint16"):
            raise ValueError(f"{path}: depth must be uint16 (N,H,W), matching its timestamp table")
        masks[f"{MAIN_CAMERA}_depth_unmatched"] = mask

    valid = np.ones(len(query), bool)
    for mask in masks.values():
        valid &= mask
    runs = segments(valid, query, max_gap_ns=policy.segment_gap_ns, block_ids=blocks.ids)
    training_time = query.copy()
    pauses = [dict(pause) for pause in blocks.pauses]
    reviews = {str(row): review for row, review in (pause_reviews or {}).items()}
    if set(reviews) - {str(pause["after_main_row"]) for pause in pauses}:
        raise ValueError(f"{source}: pause review references an undetected seam")
    for pause in pauses:
        block = pause["block"]
        review = reviews.get(str(pause["after_main_row"]))
        left = np.flatnonzero(valid & (blocks.ids == block - 1))
        right = np.flatnonzero(valid & (blocks.ids == block))
        merge = (policy.pause_policy == "checked_compress" and review is not None
                 and review["merge"] and len(left) > 0 and len(right) > 0)
        pause.update(merged=bool(merge), reason=review["reason"] if review else "Not reviewed; keep boundary")
        if merge:
            shift = training_time[left[-1]] + policy.frame_period_ns - training_time[right[0]]
            training_time[blocks.ids >= block] += shift
            pause["training_shift_ns"] = int(shift)
            # Join only the two adjacent runs; retain other invalid gaps.
            for index in range(1, len(runs)):
                if runs[index-1][-1] == left[-1] and runs[index][0] == right[0]:
                    runs[index-1:index+1] = [np.r_[runs[index-1], runs[index]]]
                    break
    short_runs = [run for run in runs if len(run) < policy.min_segment_frames]
    runs = [run for run in runs if len(run) >= policy.min_segment_frames]

    return EpisodePlan(
        source=source, path=Path(path), start_ns=start, end_ns=end, blocks=blocks, query=query,
        training_time=training_time, cameras=cameras, camera_seams=seams, stream_times=stream_times,
        camera_rows=camera_rows,
        camera_reused=camera_reused, camera_offsets=camera_offsets, camera_source_ms=camera_source_ms,
        masks=masks, valid=valid, runs=runs, short_runs=short_runs, pauses=pauses,
        state={key: np.concatenate(parts, axis=1) for key, parts in state.items()},
        action=np.concatenate(arm_actions + hand_actions, axis=1),
        state_interpolated=np.stack(interpolated, axis=1), state_gap_ns=np.stack(state_gaps, axis=1),
        command_age_ns=np.stack(command_ages, axis=1), depth_rows=depth_rows)
