"""Conversion policies.

Repair, the default, samples a fixed grid per recording block, reuses at most
a few missing camera frames, interpolates state across wider gaps and splits
at pauses unless a seam was reviewed for merging; no manual step is needed.
Strict samples at real main-camera frame times with fixed 20 ms camera, 50 ms
state and 50 ms command limits; it detects pauses only to report them. Both
are values of one policy type, planned by the same code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import math
from pathlib import Path

from bimanual_teleop.common.config import load_yaml_config
from bimanual_teleop.paths import PROJECT_ROOT

DEFAULT_CONFIG = PROJECT_ROOT / "configs/recording_conversion.yaml"
# Values for options a conversion config leaves out.
REPAIR_DEFAULTS = dict(mode="repair", target_fps=30, max_missing_camera_frames=2,
                       camera_match_tolerance_ms=20, max_state_interp_gap_ms=100,
                       max_command_age_ms=50, pause_policy="checked_compress",
                       pause_offset_tolerance_ms=100, pause_reviews={}, min_segment_frames=0)


@dataclass(frozen=True)
class ConversionPolicy:
    mode: str
    camera_tolerance_ns: int
    state_gap_ns: int
    command_age_ns: int
    segment_gap_ns: int
    # None samples at real main-camera frame times as one recording block.
    target_fps: float | None = None
    max_missing_camera_frames: int = 0
    pause_policy: str = "keep"
    pause_offset_tolerance_ms: float = REPAIR_DEFAULTS["pause_offset_tolerance_ms"]
    pause_reviews: Mapping = field(default_factory=dict)
    # Shorter continuous runs are left out of the dataset and reported.
    min_segment_frames: int = 0
    # Write per-frame provenance under meta/ and per-camera repair reports.
    provenance: bool = False
    # The validated YAML mapping recorded in reports.
    config: dict | None = None

    @property
    def frame_period_ns(self):
        return round(1e9 / self.target_fps)

    @property
    def camera_reuse_age_ns(self):
        return (self.max_missing_camera_frames + 1) * 1e9 / self.target_fps if self.target_fps else 0

    def output_attrs(self):
        """Root dataset attributes describing this policy's timestamps."""
        if self.target_fps is None:
            return dict(timestamp="seconds since source episode start; camera_0 real frame times")
        return dict(timestamp="seconds on per-block training grids; reviewed pauses may be compressed",
                    target_fps=self.target_fps, conversion_config=self.config)

    def reviews_for(self, source, sidecar=None):
        """Reviewed pause seams of one episode keyed by main-camera row; review.json wins per seam."""
        if self.target_fps is None:
            return {}
        reviews = {str(row): review for row, review in self.pause_reviews.get(source, {}).items()}
        reviews.update({str(row): review for row, review in (sidecar or {}).items()})
        return reviews


STRICT = ConversionPolicy(mode="strict", camera_tolerance_ns=20_000_000, state_gap_ns=50_000_000,
                          command_age_ns=50_000_000, segment_gap_ns=50_000_000)


def validate_pause_reviews(reviews, where):
    """``reviews`` maps main-camera row to {merge: bool, reason: nonempty str}."""
    if not isinstance(reviews, Mapping):
        raise ValueError(f"{where} must be a mapping")
    for row, review in reviews.items():
        if (not str(row).isdigit() or not isinstance(review, Mapping)
                or set(review) != {"merge", "reason"} or type(review["merge"]) is not bool
                or not isinstance(review["reason"], str) or not review["reason"].strip()):
            raise ValueError("Each pause review requires a frame index, boolean merge and nonempty reason")


def load_policy(config=None):
    """A validated YAML mapping or its path; configs/recording_conversion.yaml for None."""
    if config is None or isinstance(config, (str, Path)):
        config = load_yaml_config(DEFAULT_CONFIG if config is None else config)
    if not isinstance(config, Mapping):
        raise ValueError("conversion_config must be a YAML mapping or its path")
    result = dict(REPAIR_DEFAULTS)
    unknown = set(config) - set(result)
    if unknown:
        raise ValueError(f"Unknown conversion options: {sorted(unknown)}")
    result.update(config)
    if result["mode"] not in ("strict", "repair"):
        raise ValueError("mode must be strict or repair")
    if result["pause_policy"] not in ("keep", "checked_compress"):
        raise ValueError("pause_policy must be keep or checked_compress")
    for key in ("target_fps", "camera_match_tolerance_ms", "max_state_interp_gap_ms",
                "max_command_age_ms", "pause_offset_tolerance_ms"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a finite positive number")
    if result["target_fps"] > 1000:
        raise ValueError("target_fps must not exceed 1000")
    for key in ("max_missing_camera_frames", "min_segment_frames"):
        if type(result[key]) is not int or result[key] < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    if not isinstance(result["pause_reviews"], Mapping):
        raise ValueError("pause_reviews must map source_episode to reviewed seams")
    for source, reviews in result["pause_reviews"].items():
        validate_pause_reviews(reviews, f"pause_reviews/{source}")
    if result["mode"] == "strict":
        return replace(STRICT, min_segment_frames=result["min_segment_frames"],
                       pause_offset_tolerance_ms=result["pause_offset_tolerance_ms"],
                       config={key: result[key] for key in ("mode", "min_segment_frames", "pause_offset_tolerance_ms")})
    return ConversionPolicy(
        mode="repair", camera_tolerance_ns=round(result["camera_match_tolerance_ms"] * 1e6),
        state_gap_ns=round(result["max_state_interp_gap_ms"] * 1e6),
        command_age_ns=round(result["max_command_age_ms"] * 1e6),
        segment_gap_ns=round(1.5e9 / result["target_fps"]), target_fps=result["target_fps"],
        max_missing_camera_frames=result["max_missing_camera_frames"], pause_policy=result["pause_policy"],
        pause_offset_tolerance_ms=result["pause_offset_tolerance_ms"],
        pause_reviews=result["pause_reviews"], min_segment_frames=result["min_segment_frames"],
        provenance=True, config=result)
