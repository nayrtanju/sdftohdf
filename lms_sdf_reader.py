from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import io
import re
import struct
from typing import BinaryIO, Iterable

import h5py
import numpy as np


class SDFFormatError(RuntimeError):
    pass


@dataclass
class ChannelInfo:
    index: int
    name: str
    unit: str
    x_unit: str
    sample_interval: float | None
    sample_rate: float | None
    sample_count: int
    data_offset: int
    si_scale: float | None
    sensor_input_unit: str
    sensor_output_unit: str
    sensor_scale: float | None
    sampling_type: str


class LMSTestLabSDF:
    """
    Reader for the LMS/Testlab SDF layout verified against the supplied file.

    Verified characteristics of this family:
      * signature 'LMS T.L' at byte 18
      * big-endian numeric storage
      * channel count stored as BE uint16 at byte 26
      * channel descriptor pointer stored as BE uint32 at byte 46 (+2)
      * data pointer stored as BE uint32 at byte 62 (+2)
      * fixed-size channel descriptor table
      * channel-major big-endian float32 samples

    This is intentionally strict: if structural checks fail, it refuses conversion
    instead of silently producing incorrect data.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh: BinaryIO | None = None
        self.file_size = self.path.stat().st_size
        self.channels: list[ChannelInfo] = []
        self.channel_count = 0
        self.channel_desc_start = 0
        self.data_start = 0
        self.channel_desc_size = 0
        self.time_desc_start = 0
        self.time_desc_size = 0
        self._parse()

    @staticmethod
    def _be_u16(b: bytes, off: int) -> int:
        return struct.unpack_from('>H', b, off)[0]

    @staticmethod
    def _be_u32(b: bytes, off: int) -> int:
        return struct.unpack_from('>I', b, off)[0]

    @staticmethod
    def _be_f32(b: bytes, off: int) -> float:
        return struct.unpack_from('>f', b, off)[0]

    @staticmethod
    def _cstr(buf: bytes, off: int, max_len: int) -> str:
        raw = buf[off:off + max_len].split(b'\x00', 1)[0]
        return raw.decode('latin-1', errors='replace').strip()

    def _parse(self) -> None:
        with self.path.open('rb') as f:
            header = f.read(min(self.file_size, 16384))

        if len(header) < 100:
            raise SDFFormatError('File is too small to be this LMS/Testlab SDF format.')
        if header[18:25] != b'LMS T.L':
            raise SDFFormatError("Expected 'LMS T.L' signature at byte 18 was not found.")

        self.channel_count = self._be_u16(header, 26)
        if not (1 <= self.channel_count <= 4096):
            raise SDFFormatError(f'Implausible channel count: {self.channel_count}')

        # Stored pointers in this file family address two bytes before the real block.
        self.channel_desc_start = self._be_u32(header, 46) + 2
        self.data_start = self._be_u32(header, 62) + 2

        if not (100 < self.channel_desc_start < self.data_start < self.file_size):
            raise SDFFormatError(
                f'Invalid structural pointers: channel_desc={self.channel_desc_start}, '
                f'data={self.data_start}, size={self.file_size}'
            )

        span = self.data_start - self.channel_desc_start
        if span % self.channel_count != 0:
            raise SDFFormatError('Channel descriptor table does not divide evenly by channel count.')
        self.channel_desc_size = span // self.channel_count
        if self.channel_desc_size < 64:
            raise SDFFormatError(f'Implausible channel descriptor size: {self.channel_desc_size}')

        # Locate the parallel "Time Record" metadata table by its repeated fixed spacing.
        search_area = header[:self.channel_desc_start]
        positions = [m.start() for m in re.finditer(re.escape(b'Time Record'), search_area)]
        if len(positions) < self.channel_count:
            raise SDFFormatError(
                f'Only {len(positions)} Time Record descriptors found for '
                f'{self.channel_count} channels.'
            )
        positions = positions[:self.channel_count]
        if self.channel_count > 1:
            diffs = [b - a for a, b in zip(positions, positions[1:])]
            if len(set(diffs)) != 1:
                raise SDFFormatError(f'Inconsistent Time Record descriptor spacing: {diffs[:8]}')
            self.time_desc_size = diffs[0]
        else:
            raise SDFFormatError('Single-channel layout not yet verified.')
        self.time_desc_start = positions[0] - 48
        if self.time_desc_start < 0:
            raise SDFFormatError('Invalid Time Record table start.')

        time_records = []
        channel_records = []
        with self.path.open('rb') as f:
            f.seek(self.time_desc_start)
            for _ in range(self.channel_count):
                rec = f.read(self.time_desc_size)
                if len(rec) != self.time_desc_size:
                    raise SDFFormatError('Unexpected EOF in Time Record table.')
                time_records.append(rec)
            f.seek(self.channel_desc_start)
            for _ in range(self.channel_count):
                rec = f.read(self.channel_desc_size)
                if len(rec) != self.channel_desc_size:
                    raise SDFFormatError('Unexpected EOF in channel descriptor table.')
                channel_records.append(rec)

        names = [self._cstr(r, 8, min(80, len(r) - 8)) for r in channel_records]
        if any(not n for n in names):
            raise SDFFormatError('One or more channel names could not be decoded.')

        dts: list[float | None] = []
        units: list[str] = []
        x_units: list[str] = []
        si_scales: list[float | None] = []
        previous_count_fields: list[int] = []

        for r in time_records:
            dt = self._be_f32(r, 76) if len(r) >= 80 else float('nan')
            dts.append(float(dt) if np.isfinite(dt) and dt > 0 else None)
            units.append(self._cstr(r, 130, min(16, len(r) - 130)) if len(r) > 130 else '')
            x_units.append(self._cstr(r, 106, min(12, len(r) - 106)) if len(r) > 106 else '')
            scale = self._be_f32(r, 140) if len(r) >= 144 else float('nan')
            si_scales.append(float(scale) if np.isfinite(scale) else None)
            previous_count_fields.append(self._be_u32(r, 24) if len(r) >= 28 else 0)

        # For this verified family, Time Record[i+1].field24 gives the stored length
        # of channel i. The event-time raw tacho trace uses a last-index convention,
        # so its physical float count is +1. The final channel is reconciled from EOF.
        counts: list[int] = []
        for i in range(self.channel_count - 1):
            c = previous_count_fields[i + 1]
            if i == 0 and names[i].lower().startswith('raw:') and units[i].lower() in ('s', 'sec', 'second', 'seconds'):
                c += 1
            counts.append(int(c))

        floats_total = (self.file_size - self.data_start) // 4
        if (self.file_size - self.data_start) % 4:
            raise SDFFormatError('Data section is not aligned to 32-bit floats.')
        final_count = int(floats_total - sum(counts))
        if final_count <= 0:
            raise SDFFormatError(
                f'Could not reconcile final channel length; remaining float count={final_count}'
            )
        counts.append(final_count)

        # Cross-check duration of uniformly sampled channels. Refuse obviously bad parse.
        regular_durations = []
        for name, dt, c in zip(names, dts, counts):
            if dt and not name.lower().startswith('raw:'):
                regular_durations.append(c * dt)
        if regular_durations:
            med = float(np.median(regular_durations))
            # Different-rate channels should describe essentially the same recording duration.
            for name, dt, c in zip(names, dts, counts):
                if dt and not name.lower().startswith('raw:'):
                    dur = c * dt
                    if abs(dur - med) > max(0.1, 0.01 * med):
                        raise SDFFormatError(
                            f'Duration cross-check failed for {name}: {dur:.6f}s vs median {med:.6f}s'
                        )

        offset = self.data_start
        parsed: list[ChannelInfo] = []
        for i, (name, unit, x_unit, dt, count, si_scale, cr) in enumerate(
            zip(names, units, x_units, dts, counts, si_scales, channel_records)
        ):
            input_unit = self._cstr(cr, 104, min(10, len(cr) - 104)) if len(cr) > 104 else ''
            output_unit = self._cstr(cr, 114, min(10, len(cr) - 114)) if len(cr) > 114 else ''
            sensor_scale = self._be_f32(cr, 136) if len(cr) >= 140 else float('nan')
            sensor_scale = float(sensor_scale) if np.isfinite(sensor_scale) else None

            sampling_type = 'event_timestamps' if name.lower().startswith('raw:') and unit.lower() == 's' else 'uniform'
            rate = (1.0 / dt) if dt and sampling_type == 'uniform' else None
            parsed.append(ChannelInfo(
                index=i,
                name=name,
                unit=unit,
                x_unit=x_unit,
                sample_interval=dt if sampling_type == 'uniform' else None,
                sample_rate=rate,
                sample_count=count,
                data_offset=offset,
                si_scale=si_scale,
                sensor_input_unit=input_unit,
                sensor_output_unit=output_unit,
                sensor_scale=sensor_scale,
                sampling_type=sampling_type,
            ))
            offset += count * 4

        if offset != self.file_size:
            raise SDFFormatError(f'Channel data does not end at EOF: {offset} != {self.file_size}')

        self.channels = parsed

    def read_channel(self, channel: int | str) -> np.ndarray:
        info = self.channel(channel)
        with self.path.open('rb') as f:
            f.seek(info.data_offset)
            raw = f.read(info.sample_count * 4)
        if len(raw) != info.sample_count * 4:
            raise SDFFormatError(f'Unexpected EOF while reading {info.name}')
        # Convert to native-endian float32 for downstream libraries/HDF5.
        return np.frombuffer(raw, dtype='>f4').astype(np.float32, copy=True)

    def channel(self, channel: int | str) -> ChannelInfo:
        if isinstance(channel, int):
            return self.channels[channel]
        for c in self.channels:
            if c.name == channel:
                return c
        raise KeyError(channel)

    def iter_channel_chunks(self, info: ChannelInfo, chunk_samples: int = 1_000_000) -> Iterable[np.ndarray]:
        remaining = info.sample_count
        with self.path.open('rb') as f:
            f.seek(info.data_offset)
            while remaining:
                n = min(remaining, chunk_samples)
                raw = f.read(n * 4)
                if len(raw) != n * 4:
                    raise SDFFormatError(f'Unexpected EOF while streaming {info.name}')
                yield np.frombuffer(raw, dtype='>f4').astype(np.float32, copy=False)
                remaining -= n

    @staticmethod
    def _safe_h5_name(name: str) -> str:
        return name.replace('/', '__')

    def to_hdf5(
        self,
        output_path: str | Path,
        selected_channels: list[str] | None = None,
        compression: str | None = 'lzf',
        explicit_time: bool = False,
    ) -> Path:
        output_path = Path(output_path)
        selected = set(selected_channels) if selected_channels else None

        comp_kwargs = {}
        if compression == 'gzip':
            comp_kwargs = {'compression': 'gzip', 'compression_opts': 4, 'shuffle': True}
        elif compression == 'lzf':
            comp_kwargs = {'compression': 'lzf', 'shuffle': True}
        elif compression in (None, 'none'):
            comp_kwargs = {}
        else:
            raise ValueError(f'Unsupported compression: {compression}')

        with h5py.File(output_path, 'w') as h5:
            h5.attrs['source_format'] = 'LMS/Testlab SDF (LMS T.L)'
            h5.attrs['reader_layout_version'] = 'verified-2026-08-11'
            h5.attrs['source_filename'] = self.path.name
            h5.attrs['source_size_bytes'] = self.file_size
            h5.attrs['channel_count_total'] = self.channel_count
            h5.attrs['numeric_storage_source'] = 'big-endian float32'
            h5.attrs['conversion_note'] = (
                'Numeric values are decoded without applying SI/unit conversion factors. '
                'Original units and conversion/calibration metadata are stored as attributes.'
            )

            gch = h5.create_group('channels')
            index_rows = []

            for info in self.channels:
                if selected is not None and info.name not in selected:
                    continue

                g = gch.create_group(self._safe_h5_name(info.name))
                g.attrs['original_name'] = info.name
                g.attrs['unit'] = info.unit
                g.attrs['x_unit'] = info.x_unit
                g.attrs['sampling_type'] = info.sampling_type
                g.attrs['sample_count'] = info.sample_count
                g.attrs['source_data_offset'] = info.data_offset
                if info.sample_interval is not None:
                    g.attrs['sample_interval_s'] = info.sample_interval
                    g.attrs['sample_rate_hz'] = info.sample_rate
                    g.attrs['x_start_s'] = 0.0
                if info.si_scale is not None:
                    g.attrs['si_scale_metadata'] = info.si_scale
                if info.sensor_input_unit:
                    g.attrs['sensor_input_unit'] = info.sensor_input_unit
                if info.sensor_output_unit:
                    g.attrs['sensor_output_unit'] = info.sensor_output_unit
                if info.sensor_scale is not None:
                    g.attrs['sensor_scale_metadata'] = info.sensor_scale

                chunk_len = min(info.sample_count, 262_144)
                ds = g.create_dataset(
                    'data',
                    shape=(info.sample_count,),
                    dtype=np.float32,
                    chunks=(max(1, chunk_len),),
                    **comp_kwargs,
                )
                pos = 0
                for arr in self.iter_channel_chunks(info):
                    ds[pos:pos + len(arr)] = arr
                    pos += len(arr)

                if explicit_time and info.sampling_type == 'uniform' and info.sample_interval is not None:
                    # This can substantially increase HDF5 size; implicit x_start/dt is the default.
                    tds = g.create_dataset(
                        'time',
                        shape=(info.sample_count,),
                        dtype=np.float64,
                        chunks=(max(1, min(info.sample_count, 262_144)),),
                        **comp_kwargs,
                    )
                    step = 1_000_000
                    for s in range(0, info.sample_count, step):
                        e = min(info.sample_count, s + step)
                        tds[s:e] = np.arange(s, e, dtype=np.float64) * info.sample_interval
                elif explicit_time and info.sampling_type == 'event_timestamps':
                    # raw:Tacho data itself is already the timestamp vector.
                    g['data'].attrs['represents'] = 'event_time_seconds'

                index_rows.append((info.name, info.unit, info.sample_count, info.sample_rate or np.nan))

            idx = h5.create_group('index')
            sdt = h5py.string_dtype('utf-8')
            idx.create_dataset('channel_names', data=np.asarray([r[0] for r in index_rows], dtype=object), dtype=sdt)
            idx.create_dataset('units', data=np.asarray([r[1] for r in index_rows], dtype=object), dtype=sdt)
            idx.create_dataset('sample_counts', data=np.asarray([r[2] for r in index_rows], dtype=np.int64))
            idx.create_dataset('sample_rates_hz', data=np.asarray([r[3] for r in index_rows], dtype=np.float64))

        return output_path
