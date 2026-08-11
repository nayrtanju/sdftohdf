
import io
import os
import tempfile
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import streamlit as st

from lms_sdf_reader import LMSStandaloneSDFReader, UnsupportedSDFError


APP_TITLE = "LMS SDF → HDF5 Converter"


def human_size(n):
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{size:.1f} {u}"
        size /= 1024


def safe_group_name(name):
    return name.replace("/", "__").replace("\x00", "_")


def create_hdf5_from_sdf(
    sdf_path,
    out_path,
    selected_names,
    compression,
    gzip_level,
    progress_cb=None,
):
    with LMSStandaloneSDFReader(sdf_path) as reader, h5py.File(out_path, "w") as h5:
        h5.attrs["format"] = "Converted from LMS Test.Lab SDF"
        h5.attrs["converter"] = "Standalone LMS SDF -> HDF5"
        h5.attrs["source_file_size_bytes"] = reader.file_size
        h5.attrs["channel_count"] = len(selected_names)

        signals = h5.create_group("channels")
        index = h5.create_group("index")

        selected = [reader.get_channel(name) for name in selected_names]
        total_samples = sum(ch.sample_count for ch in selected)
        processed = 0

        names, units, sample_counts, sample_rates = [], [], [], []

        for ch in selected:
            g = signals.create_group(safe_group_name(ch.name))
            g.attrs["original_name"] = ch.name
            g.attrs["unit"] = ch.unit
            g.attrs["sample_count"] = ch.sample_count
            g.attrs["sampling_type"] = ch.sampling_type
            g.attrs["source_data_offset"] = ch.data_offset

            if ch.sample_rate_hz is not None:
                g.attrs["sample_rate_hz"] = ch.sample_rate_hz
            if ch.sample_interval_s is not None:
                g.attrs["sample_interval_s"] = ch.sample_interval_s

            chunk_len = min(262144, ch.sample_count)

            create_kwargs = {}
            if compression == "gzip":
                create_kwargs = {
                    "compression": "gzip",
                    "compression_opts": int(gzip_level),
                    "shuffle": True,
                }
            elif compression == "lzf":
                create_kwargs = {
                    "compression": "lzf",
                    "shuffle": True,
                }

            ds = g.create_dataset(
                "data",
                shape=(ch.sample_count,),
                dtype=np.float32,
                chunks=(max(1, chunk_len),),
                **create_kwargs,
            )

            write_pos = 0
            for _, chunk in reader.iter_channel_chunks(ch, chunk_samples=1_000_000):
                ds[write_pos:write_pos + len(chunk)] = chunk
                write_pos += len(chunk)
                processed += len(chunk)
                if progress_cb and total_samples:
                    progress_cb(processed / total_samples)

            names.append(ch.name)
            units.append(ch.unit)
            sample_counts.append(ch.sample_count)
            sample_rates.append(
                np.nan if ch.sample_rate_hz is None else ch.sample_rate_hz
            )

        dt = h5py.string_dtype("utf-8")
        index.create_dataset("channel_names", data=np.asarray(names, dtype=object), dtype=dt)
        index.create_dataset("units", data=np.asarray(units, dtype=object), dtype=dt)
        index.create_dataset("sample_counts", data=np.asarray(sample_counts, dtype=np.int64))
        index.create_dataset("sample_rates_hz", data=np.asarray(sample_rates, dtype=np.float64))


def verify_hdf5(path, expected_channels):
    with h5py.File(path, "r") as h5:
        if "channels" not in h5:
            return False, "Missing /channels group"
        found = len(h5["channels"])
        if found != expected_channels:
            return False, f"Expected {expected_channels} channels, found {found}"
        for name, g in h5["channels"].items():
            if "data" not in g:
                return False, f"Channel {name} has no data dataset"
            if len(g["data"]) != int(g.attrs["sample_count"]):
                return False, f"Sample count mismatch in {name}"
        return True, "PASS"


st.set_page_config(page_title=APP_TITLE, page_icon="🔄", layout="wide")
st.title(APP_TITLE)
st.caption("Standalone conversion only — no Simcenter Testlab installation required.")

uploaded = st.file_uploader("Upload LMS SDF file", type=["sdf"])

if uploaded:
    suffix = Path(uploaded.name).suffix or ".sdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tf:
        tf.write(uploaded.getbuffer())
        sdf_path = tf.name

    try:
        reader = LMSStandaloneSDFReader(sdf_path)
    except UnsupportedSDFError as e:
        st.error(f"Unsupported or structurally different SDF file: {e}")
        os.unlink(sdf_path)
        st.stop()

    st.success(f"SDF structure validated. {len(reader.channels)} channels found.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Source size", human_size(reader.file_size))
    c2.metric("Channels", len(reader.channels))
    c3.metric("Data start", f"{reader.data_start:,} bytes")

    rows = []
    for ch in reader.channels:
        rows.append({
            "Channel": ch.name,
            "Unit": ch.unit,
            "Samples": ch.sample_count,
            "Sample rate [Hz]": None if ch.sample_rate_hz is None else round(ch.sample_rate_hz, 6),
            "Duration [s]": None if ch.duration_s is None else round(ch.duration_s, 6),
            "Type": ch.sampling_type,
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    default_channels = [ch.name for ch in reader.channels]
    selected = st.multiselect(
        "Channels to convert",
        options=default_channels,
        default=default_channels,
    )

    st.subheader("Output settings")
    col1, col2 = st.columns(2)
    compression = col1.selectbox("Compression", ["gzip", "lzf", "none"], index=0)
    gzip_level = col2.slider("gzip level", 1, 9, 4, disabled=(compression != "gzip"))

    with st.expander("Preview a channel"):
        preview_name = st.selectbox("Channel", default_channels)
        preview_count = st.slider("Preview samples", 100, 5000, 1000, step=100)
        preview = reader.read_channel(preview_name, start=0, count=preview_count)
        st.line_chart(preview)

    if st.button("Convert to HDF5", type="primary", disabled=(len(selected) == 0)):
        progress = st.progress(0.0, text="Converting...")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".h5") as hf:
            out_path = hf.name

        try:
            create_hdf5_from_sdf(
                sdf_path=sdf_path,
                out_path=out_path,
                selected_names=selected,
                compression=compression,
                gzip_level=gzip_level,
                progress_cb=lambda x: progress.progress(min(float(x), 1.0), text=f"Converting... {x*100:.1f}%"),
            )

            ok, msg = verify_hdf5(out_path, len(selected))
            if not ok:
                st.error(f"HDF5 verification failed: {msg}")
                st.stop()

            progress.progress(1.0, text="Conversion complete")
            out_size = os.path.getsize(out_path)

            st.success(f"Conversion and verification completed successfully. Output size: {human_size(out_size)}")

            with open(out_path, "rb") as f:
                hdf_bytes = f.read()

            out_name = Path(uploaded.name).stem + ".h5"
            st.download_button(
                "Download HDF5",
                data=hdf_bytes,
                file_name=out_name,
                mime="application/x-hdf5",
                use_container_width=True,
            )
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    reader.close()
    os.unlink(sdf_path)
