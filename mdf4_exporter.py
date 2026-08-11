
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import math

import numpy as np

from lms_sdf_reader import LMSTestLabSDF


@dataclass
class MDF4VerificationResult:
    ok: bool
    version: str
    channel_count: int
    checked_channels: list[str]
    problems: list[str]


def _import_asammdf():
    try:
        from asammdf import MDF, Signal
    except ImportError as exc:
        raise RuntimeError(
            "asammdf is required for MDF4 writing. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc
    return MDF, Signal


def _channel_comment(ch) -> str:
    parts = [
        "Converted from LMS/Simcenter Testlab SDF by standalone Python converter.",
        "Source numeric samples are preserved without additional engineering-unit scaling.",
        f"source_sampling_type={ch.sampling_type}",
    ]
    if ch.sensor_input_unit:
        parts.append(f"sensor_input_unit={ch.sensor_input_unit}")
    if ch.sensor_output_unit:
        parts.append(f"sensor_output_unit={ch.sensor_output_unit}")
    if ch.sensor_scale is not None:
        parts.append(f"sensor_sensitivity_metadata={ch.sensor_scale:.17g}")
    if ch.si_scale is not None:
        parts.append(f"si_scale_metadata={ch.si_scale:.17g}")
    return "; ".join(parts)


def _uniform_timestamps(ch) -> np.ndarray:
    if ch.sample_interval is None or ch.sample_interval <= 0:
        if ch.sample_rate is None or ch.sample_rate <= 0:
            raise ValueError(f"No usable timebase for channel {ch.name!r}")
        dt = 1.0 / ch.sample_rate
    else:
        dt = ch.sample_interval
    # float64 master time for stable high-rate timing
    return np.arange(ch.sample_count, dtype=np.float64) * float(dt)


def convert_sdf_to_mdf4(
    sdf_path: str | Path,
    mf4_path: str | Path,
    selected_channels: Iterable[str] | None = None,
    *,
    mdf_version: str = "4.10",
    compression: int = 0,
) -> Path:
    """
    Convert the validated LMS/Testlab SDF family into ASAM MDF4.

    Strategy:
      * Uniform channels sharing sample_count and sample_interval are placed
        in the same MDF channel group with one common master timebase.
      * Non-equidistant raw-tacho event timestamps are written as their own group.
      * Original decoded signal values are not re-scaled.
      * MDF 4.10 is the default for broad interoperability.

    `compression=0` is recommended for the first ArtemiS compatibility test.
    """
    MDF, Signal = _import_asammdf()

    sdf_path = Path(sdf_path)
    mf4_path = Path(mf4_path)
    reader = LMSTestLabSDF(sdf_path)

    if selected_channels is None:
        selected = [c.name for c in reader.channels]
    else:
        selected = list(selected_channels)

    infos = [reader.channel(name) for name in selected]

    mdf = MDF(version=mdf_version)

    # Group uniformly sampled channels by identical timebase.
    uniform_groups: dict[tuple[int, float], list] = {}
    event_channels = []

    for ch in infos:
        if ch.sampling_type == "event_timestamps":
            event_channels.append(ch)
            continue

        if ch.sample_interval is not None and ch.sample_interval > 0:
            dt = float(ch.sample_interval)
        elif ch.sample_rate is not None and ch.sample_rate > 0:
            dt = 1.0 / float(ch.sample_rate)
        else:
            raise ValueError(f"Channel {ch.name!r} has no valid sampling information.")

        # round only for dictionary grouping; actual timestamp uses the original dt
        key = (int(ch.sample_count), round(dt, 15))
        uniform_groups.setdefault(key, []).append(ch)

    # Keep common-timebase channel groups together. This is efficient and preserves
    # the natural 51.2 kHz audio/NVH group in the supplied SDF.
    for (_, _), group in uniform_groups.items():
        master = _uniform_timestamps(group[0])
        signals = []

        for ch in group:
            samples = reader.read_channel(ch.name).astype(np.float32, copy=False)
            sig = Signal(
                samples=samples,
                timestamps=master,
                name=ch.name,
                unit=ch.unit or ch.sensor_output_unit or "",
                comment=_channel_comment(ch),
            )
            signals.append(sig)

        mdf.append(
            signals,
            acq_name=f"LMS SDF {group[0].sample_rate or 0:.6g} Hz",
            comment="LMS/Testlab SDF converted to ASAM MDF4",
            common_timebase=True,
        )

    # Raw tacho is an event-timestamp list. Store it as a non-equidistant channel
    # with timestamps equal to the source event times. Samples also contain the event
    # time value so no information is lost even in tools that expose only samples.
    for ch in event_channels:
        event_times = reader.read_channel(ch.name).astype(np.float64, copy=False)
        valid = np.isfinite(event_times)
        event_times = event_times[valid]

        # Ensure monotonic time axis for MDF.
        order = np.argsort(event_times, kind="stable")
        event_times = event_times[order]

        sig = Signal(
            samples=event_times.astype(np.float64, copy=False),
            timestamps=event_times,
            name=ch.name,
            unit=ch.unit or "s",
            comment=(
                _channel_comment(ch)
                + "; representation=non-equidistant event timestamps; "
                  "sample value equals event timestamp"
            ),
        )
        mdf.append(
            sig,
            acq_name="LMS SDF raw tacho events",
            comment="Non-equidistant raw tacho event timestamps",
            common_timebase=True,
        )

    saved = mdf.save(
        mf4_path,
        overwrite=True,
        compression=int(compression),
    )
    mdf.close()
    return Path(saved)


def verify_mdf4_against_sdf(
    sdf_path: str | Path,
    mf4_path: str | Path,
    selected_channels: Iterable[str] | None = None,
    *,
    sample_points: int = 9,
) -> MDF4VerificationResult:
    """
    Re-open the generated MF4 with asammdf and compare representative source samples,
    units and timebase information against the SDF parser.

    This verifies the file created by the converter, not ArtemiS itself.
    """
    MDF, _ = _import_asammdf()

    sdf_path = Path(sdf_path)
    mf4_path = Path(mf4_path)
    reader = LMSTestLabSDF(sdf_path)

    if selected_channels is None:
        selected = [c.name for c in reader.channels]
    else:
        selected = list(selected_channels)

    problems: list[str] = []
    checked: list[str] = []

    with MDF(mf4_path) as mdf:
        version = str(mdf.version)

        for name in selected:
            src_info = reader.channel(name)
            try:
                sig = mdf.get(name)
            except Exception as exc:
                problems.append(f"{name}: not readable from MF4 ({exc})")
                continue

            checked.append(name)

            src = reader.read_channel(name)

            if src_info.sampling_type == "event_timestamps":
                expected = src.astype(np.float64)
                expected = expected[np.isfinite(expected)]
                expected = np.sort(expected, kind="stable")
                got = np.asarray(sig.samples, dtype=np.float64)

                if len(got) != len(expected):
                    problems.append(
                        f"{name}: sample count mismatch {len(got)} != {len(expected)}"
                    )
                else:
                    idx = np.linspace(0, max(len(expected)-1, 0),
                                      min(sample_points, len(expected)),
                                      dtype=int)
                    if len(idx) and not np.allclose(
                        got[idx], expected[idx], rtol=0, atol=1e-10, equal_nan=True
                    ):
                        problems.append(f"{name}: event sample values changed")

                ts = np.asarray(sig.timestamps, dtype=np.float64)
                if len(ts) == len(expected):
                    idx = np.linspace(0, max(len(expected)-1, 0),
                                      min(sample_points, len(expected)),
                                      dtype=int)
                    if len(idx) and not np.allclose(
                        ts[idx], expected[idx], rtol=0, atol=1e-10, equal_nan=True
                    ):
                        problems.append(f"{name}: event timestamps changed")
            else:
                got = np.asarray(sig.samples)
                if len(got) != len(src):
                    problems.append(
                        f"{name}: sample count mismatch {len(got)} != {len(src)}"
                    )
                else:
                    idx = np.linspace(
                        0,
                        max(len(src) - 1, 0),
                        min(sample_points, len(src)),
                        dtype=int,
                    )
                    if len(idx):
                        src_check = src[idx].astype(np.float32, copy=False)
                        got_check = got[idx].astype(np.float32, copy=False)
                        if not np.array_equal(src_check, got_check, equal_nan=True):
                            problems.append(f"{name}: numeric samples changed")

                ts = np.asarray(sig.timestamps, dtype=np.float64)
                if len(ts) != src_info.sample_count:
                    problems.append(f"{name}: master time sample count mismatch")
                elif len(ts) > 1:
                    dt = float(np.median(np.diff(ts[: min(len(ts), 10000)])))
                    expected_dt = (
                        src_info.sample_interval
                        if src_info.sample_interval is not None
                        else 1.0 / src_info.sample_rate
                    )
                    if not math.isclose(
                        dt, float(expected_dt), rel_tol=1e-7, abs_tol=1e-12
                    ):
                        problems.append(
                            f"{name}: sample interval mismatch "
                            f"{dt:.12g} != {expected_dt:.12g}"
                        )

            expected_unit = src_info.unit or src_info.sensor_output_unit or ""
            got_unit = str(sig.unit or "")
            if expected_unit and got_unit != expected_unit:
                problems.append(
                    f"{name}: unit mismatch {got_unit!r} != {expected_unit!r}"
                )

        return MDF4VerificationResult(
            ok=not problems,
            version=version,
            channel_count=len(checked),
            checked_channels=checked,
            problems=problems,
        )
