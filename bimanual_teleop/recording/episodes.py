"""Episode directories, manifest statuses and recursive discovery."""

import json
import os
from pathlib import Path

MANIFEST = "episode.json"

# Manifest lifecycle: capturing -> captured -> finalizing -> complete. The
# offline EpisodeWriter starts at recording. Failed and discarded are final.
RECORDING = "recording"
CAPTURING = "capturing"
CAPTURED = "captured"
FINALIZING = "finalizing"
COMPLETE = "complete"
FAILED = "failed"
DISCARDED = "discarded"


def read_manifest(episode):
    return json.loads((Path(episode) / MANIFEST).read_text(encoding="utf-8"))


def find_episodes(path):
    """Episode directories at any depth below ``path``, including ``path`` itself.

    An episode's own contents are not searched, and hidden directories such as
    ``.episode_000000.finalizing-<pid>`` left by an interrupted run are skipped.
    """
    source = Path(path).resolve()
    if not source.is_dir():
        raise ValueError(f"输入不是目录：{source}")
    episodes = []
    for directory, children, files in os.walk(source):
        if MANIFEST in files:
            episodes.append(Path(directory))
            children.clear()
            continue
        children[:] = sorted(name for name in children if not name.startswith("."))
    return sorted(episodes)


def episode_label(episode, root):
    """Name relative to the searched root; an episode given directly keeps its own name."""
    episode, root = Path(episode), Path(root).resolve()
    return episode.name if episode == root else episode.relative_to(root).as_posix()
