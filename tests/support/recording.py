"""Synthetic raw episodes with real encoded video for offline recording tests."""

import json

import numpy as np
from scipy.spatial.transform import Rotation


START_NS = 1_000_000_000
TEST_METADATA = {"model": "test-model", "joint_unit": "rad"}


class CountingProgress:
    """Records what a converter reports to the terminal progress bars."""

    def __init__(self):
        self.episodes, self.names, self.totals, self.advanced, self.finished = None, [], [], [], 0

    def start(self, episodes):
        self.episodes = episodes

    def episode(self, name, total=None):
        self.names.append(name)
        self.advanced.append(0)

    def total(self, total):
        self.totals.append(total)

    def advance(self, count=1):
        self.advanced[-1] += count

    def finish_episode(self):
        self.finished += 1


def write_video(path, count, camera=0):
    import av

    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264rgb", rate=30)
        stream.width = stream.height = 16
        stream.pix_fmt = "rgb24"
        stream.options = {"crf": "0", "preset": "ultrafast"}
        for index in range(count):
            pixels = np.zeros((16, 16, 3), np.uint8)
            pixels[..., 0] = (20 + index * 20) % 256
            pixels[..., 1] = camera * 50
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def make_episode(parent, name="episode_000000", *, main=(10., 43., 77., 110.),
                 state=None, commands=None, other=None, depth=False, metadata=None,
                 stream_times=None, camera_sequences=None, camera_sources=None,
                 status="complete"):
    """Write one finalized episode; all times are milliseconds after START_NS.

    ``other`` replaces the frame times of camera 1 or 2. ``stream_times`` replaces
    the times of single low-dimensional streams such as ``"hands/left"``.
    ``camera_sequences`` and ``camera_sources`` replace a camera's device frame
    numbers and ``source_time_ms`` to model dropped frames and pauses.
    """
    import zarr

    path = parent / name
    path.mkdir(parents=True)
    stop = max(main) + 41
    state = np.arange(0, stop, 10.) if state is None else np.asarray(state)
    commands = np.arange(0, stop, 20.) if commands is None else np.asarray(commands)
    stream_times = stream_times or {}
    metadata = dict(TEST_METADATA if metadata is None else metadata)
    descriptor = {"schema_version": 1, "status": status, "start_ns": START_NS,
                  "end_ns": START_NS + round(stop * 1e6), "metadata": metadata}
    (path / "episode.json").write_text(json.dumps(descriptor))
    raw = zarr.open_group(str(path / "raw.zarr"), mode="w")
    raw.attrs["metadata"] = metadata

    def group(name, default):
        times = np.asarray(stream_times.get(name, default), dtype=float)
        result = raw.create_group(name)
        result.array("time_ns", START_NS + np.rint(times * 1e6).astype(np.int64))
        result.array("sequence", np.arange(len(times), dtype=np.int64))
        return result, times

    for side, base in (("left", 0.), ("right", 100.)):
        arm, times = group(f"arms/{side}", state)
        arm.array("joint_pos", base + np.arange(7) + times[:, None] / 1000.)
        poses = np.zeros((len(times), 7))
        poses[:, 0] = base + times / 1000.
        poses[:, 3:] = Rotation.from_euler("z", times / 1000.).as_quat()
        arm.array("eef_pose", poses)
        arm.array("wrench", base + np.arange(6) + times[:, None] / 1000.)
        hand, times = group(f"hands/{side}", state)
        hand.array("joint_pos", base + np.arange(20) + times[:, None] / 1000.)
        command, times = group(f"arm_commands/{side}", commands)
        command.array("joint_pos", base + 10 + np.arange(7) + times[:, None] / 1000.)
        goals = np.zeros((len(times), 7))
        goals[:, 0] = base + 5 + times / 1000.
        goals[:, 6] = 1.
        command.array("eef_pose", goals)
        hand_command, times = group(f"hand_commands/{side}", commands)
        hand_command.array("joint_pos", base + 20 + np.arange(20) + times[:, None] / 1000.)
    for camera in range(3):
        times = main if not other or camera not in other else other[camera]
        rgb, times = group(f"cameras/camera_{camera}/rgb", times)
        if camera_sequences and camera in camera_sequences:
            rgb["sequence"][:] = np.asarray(camera_sequences[camera], dtype=np.int64)
        sources = times if not camera_sources or camera not in camera_sources else camera_sources[camera]
        rgb.array("source_time_ms", np.asarray(sources, float))
        write_video(path / f"camera_{camera}.mp4", len(times), camera)
    if depth:
        frames, times = group("cameras/camera_0/depth", np.asarray(main) + 2)
        frames.array("source_time_ms", times)
        frames.array("image", np.stack([np.full((4, 4), i + 100, np.uint16) for i in range(len(times))]))
    return path, raw
