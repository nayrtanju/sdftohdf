
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, List, Optional, Union
import io
import math
import os
import struct

import numpy as np


SDF_SIGNATURE = b"LMS T.L"
DESCRIPTOR_SIZE = 148


class UnsupportedSDFError(RuntimeError):
    pass


@dataclass
class ChannelInfo:
    name: str
    unit: str
    sample_count: int
    sample_interval_s: Optional[float]
    sample_rate_hz: Optional[float]
    data_offset: int
    data_nbytes: int
    descriptor_offset: int
    dtype: str = ">f4"
    sampling_type: str = "uniform"

    @property
    def duration_s(self) -> Optional[float]:
        if self.sample_interval_s is None:
            return None
        return self.sample_interval_s * max(self.sample_count - 1, 0)


class LMSStandaloneSDFReader:
    """
    Standalone reader for the specific LMS Test.Lab SDF structure validated
    against the supplied sample file.

    It does not claim universal support for every LMS/Siemens SDF variant.
    """

    def __init__(self, source: Union[str, Path, bytes, bytearray, BinaryIO]):
        self._source = source
        self._fh = None
        self._owns_fh = False
        self.file_size = 0
        self.data_start = None
        self.channels: List[ChannelInfo] = []
        self._open()
        self._parse_and_validate()

    def _open(self):
        if isinstance(self._source, (str, Path)):
            self._fh = open(self._source, "rb")
            self._owns_fh = True
        elif isinstance(self._source, (bytes, bytearray)):
            self._fh = io.BytesIO(self._source)
            self._owns_fh = True
        else:
            self._fh = self._source
            self._owns_fh = False

        cur = self._fh.tell()
        self._fh.seek(0, os.SEEK_END)
        self.file_size = self._fh.tell()
        self._fh.seek(cur)

    def close(self):
        if self._owns_fh and self._fh:
            self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @staticmethod
    def _clean_text(raw: bytes) -> str:
        txt = raw.split(b"\x00", 1)[0].decode("latin-1", errors="ignore").strip()
        return "".join(ch for ch in txt if ch.isprintable())

    def _parse_and_validate(self):
        self._fh.seek(0)
        head = self._fh.read(min(self.file_size, 65536))
        if SDF_SIGNATURE not in head[:4096]:
            raise UnsupportedSDFError("LMS T.L signature was not found.")

        # This SDF layout stores channel descriptor records in a repeated 148-byte table.
        marker = b"Time Record"
        positions = []
        start = 0
        while True:
            pos = head.find(marker, start)
            if pos < 0:
                break
            positions.append(pos)
            start = pos + 1

        # The validated sample has one Time Record marker per channel descriptor.
        if len(positions) < 2:
            raise UnsupportedSDFError(
                "Expected repeated LMS channel descriptor structure was not found."
            )

        # Determine descriptor table start using the first repeated marker.
        # In the validated file each marker sits at a fixed relative position in its 148-byte record.
        diffs = [b - a for a, b in zip(positions, positions[1:])]
        if not diffs or max(set(diffs), key=diffs.count) != DESCRIPTOR_SIZE:
            raise UnsupportedSDFError(
                "Descriptor spacing does not match the validated 148-byte LMS layout."
            )

        first_marker = positions[0]

        # Empirically validated record start: marker is 28 bytes into the descriptor.
        descriptor_start = first_marker - 28
        if descriptor_start < 0:
            raise UnsupportedSDFError("Invalid descriptor table start.")

        # Parse descriptor records until names stop looking valid.
        descriptors = []
        off = descriptor_start
        max_records = 512

        for _ in range(max_records):
            if off + DESCRIPTOR_SIZE > self.file_size:
                break
            self._fh.seek(off)
            rec = self._fh.read(DESCRIPTOR_SIZE)
            if len(rec) != DESCRIPTOR_SIZE:
                break

            # Validated field positions for this layout.
            name = self._clean_text(rec[0:32])
            unit = self._clean_text(rec[32:48])

            if not name:
                break

            # Sample count, interval and data byte count positions validated against the sample.
            # Little-endian metadata; signal payload is big-endian float32.
            try:
                sample_count = struct.unpack_from("<I", rec, 88)[0]
                sample_interval = struct.unpack_from("<d", rec, 96)[0]
            except struct.error:
                break

            if sample_count <= 0 or sample_count > 2_000_000_000:
                break

            descriptors.append(
                {
                    "name": name,
                    "unit": unit,
                    "sample_count": sample_count,
                    "sample_interval": sample_interval,
                    "descriptor_offset": off,
                }
            )
            off += DESCRIPTOR_SIZE

        if not descriptors:
            raise UnsupportedSDFError("No valid channel descriptors could be parsed.")

        # Data starts immediately after the descriptor/header section in this validated format.
        # Align by finding the only position for which all float32 channel lengths reach EOF exactly.
        total_payload = sum(d["sample_count"] * 4 for d in descriptors)
        candidate = self.file_size - total_payload

        if candidate <= 0:
            raise UnsupportedSDFError("Computed data start is invalid.")

        self.data_start = candidate

        current = self.data_start
        channels = []
        for d in descriptors:
            count = int(d["sample_count"])
            nbytes = count * 4
            interval = float(d["sample_interval"])

            # Raw tacho is event timestamps, not a uniformly sampled waveform.
            sampling_type = "uniform"
            sample_rate = None
            sample_interval = None

            if d["name"].lower().startswith("raw:tacho"):
                sampling_type = "event_timestamps"
            else:
                if math.isfinite(interval) and interval > 0:
                    sample_interval = interval
                    sample_rate = 1.0 / interval

            channels.append(
                ChannelInfo(
                    name=d["name"],
                    unit=d["unit"],
                    sample_count=count,
                    sample_interval_s=sample_interval,
                    sample_rate_hz=sample_rate,
                    data_offset=current,
                    data_nbytes=nbytes,
                    descriptor_offset=d["descriptor_offset"],
                    sampling_type=sampling_type,
                )
            )
            current += nbytes

        if current != self.file_size:
            raise UnsupportedSDFError(
                f"Payload validation failed: expected EOF {self.file_size}, got {current}."
            )

        self.channels = channels

    def get_channel(self, name: str) -> ChannelInfo:
        for ch in self.channels:
            if ch.name == name:
                return ch
        raise KeyError(name)

    def read_channel(self, channel: Union[str, ChannelInfo], start=0, count=None) -> np.ndarray:
        ch = self.get_channel(channel) if isinstance(channel, str) else channel
        if start < 0 or start >= ch.sample_count:
            raise ValueError("start out of range")
        if count is None:
            count = ch.sample_count - start
        count = min(int(count), ch.sample_count - start)
        self._fh.seek(ch.data_offset + start * 4)
        raw = self._fh.read(count * 4)
        if len(raw) != count * 4:
            raise EOFError("Unexpected EOF while reading channel.")
        return np.frombuffer(raw, dtype=">f4").astype(np.float32, copy=False)

    def iter_channel_chunks(self, channel: Union[str, ChannelInfo], chunk_samples=1_000_000):
        ch = self.get_channel(channel) if isinstance(channel, str) else channel
        pos = 0
        while pos < ch.sample_count:
            n = min(chunk_samples, ch.sample_count - pos)
            yield pos, self.read_channel(ch, start=pos, count=n)
            pos += n
