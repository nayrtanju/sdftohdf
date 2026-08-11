import io
import re
import hashlib
from datetime import datetime, timezone

import h5py
import numpy as np
import streamlit as st


APP_TITLE = "LMS / Simcenter Testlab SDF → HDF5 Converter"
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_sdf_family(head: bytes) -> str:
    if b"LMS T.L" in head[:4096]:
        return "LMS / Siemens Simcenter Testlab SDF"
    return "Unknown SDF family"


def extract_printable_strings(data: bytes, min_len: int = 5, limit: int = 5000):
    """
    Best-effort metadata scan only.
    This does NOT decode proprietary numeric Testlab signal blocks.
    """
    pattern = re.compile(rb"[\x20-\x7E]{" + str(min_len).encode() + rb",}")
    out = []
    seen = set()

    for match in pattern.finditer(data):
        text = match.group().decode("latin-1", errors="ignore").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def interesting_names(strings):
    """
    Heuristic list for user preview.
    Keeps strings that look like channels / measurements / tacho / rpm / time.
    """
    keywords = (
        "time", "tacho", "rpm", "speed", "channel", "acc", "mic",
        "pressure", "force", "torque", "ehps", "raw:", ":s", "record"
    )
    result = []
    for s in strings:
        low = s.lower()
        if any(k in low for k in keywords):
            result.append(s)
    return result[:300]


def build_hdf5(raw: bytes, original_name: str, metadata_strings, compression: str):
    """
    Creates a VALID, LOSSLESS HDF5 container.

    Important:
    /source/raw_sdf contains the original SDF byte-for-byte.
    This version does not pretend to decode proprietary LMS numeric channels.
    """
    bio = io.BytesIO()

    compression_args = {}
    if compression == "gzip":
        compression_args = {"compression": "gzip", "compression_opts": 4, "shuffle": True}
    elif compression == "lzf":
        compression_args = {"compression": "lzf", "shuffle": True}

    arr = np.frombuffer(raw, dtype=np.uint8)

    with h5py.File(bio, "w") as h5:
        h5.attrs["format"] = "SDF-in-HDF5"
        h5.attrs["container_version"] = "1.0"
        h5.attrs["created_utc"] = datetime.now(timezone.utc).isoformat()
        h5.attrs["source_filename"] = original_name
        h5.attrs["source_size_bytes"] = len(raw)
        h5.attrs["source_sha256"] = sha256_bytes(raw)
        h5.attrs["detected_source_family"] = detect_sdf_family(raw[:4096])
        h5.attrs["numeric_channels_decoded"] = False
        h5.attrs[
            "note"
        ] = (
            "Lossless archival conversion. Original LMS/Simcenter Testlab SDF "
            "is stored byte-for-byte in /source/raw_sdf. Proprietary signal "
            "blocks are not decoded by this portable converter."
        )

        source = h5.create_group("source")
        chunk = min(CHUNK_SIZE, max(1, len(arr)))
        source.create_dataset(
            "raw_sdf",
            data=arr,
            dtype=np.uint8,
            chunks=(chunk,),
            **compression_args,
        )

        meta = h5.create_group("metadata")
        utf8 = h5py.string_dtype(encoding="utf-8")

        meta.create_dataset(
            "printable_strings",
            data=np.asarray(metadata_strings, dtype=object),
            dtype=utf8,
        )

        candidates = interesting_names(metadata_strings)
        meta.create_dataset(
            "channel_name_candidates",
            data=np.asarray(candidates, dtype=object),
            dtype=utf8,
        )

        structure = h5.create_group("signals")
        structure.attrs["status"] = "not_decoded"
        structure.attrs[
            "required_for_true_conversion"
        ] = (
            "Use Siemens Simcenter Testlab Automation / supported export API "
            "on a Windows machine with Testlab installed and licensed, then "
            "write decoded X/Y arrays and attributes here."
        )

    bio.seek(0)
    return bio.getvalue()


def verify_hdf5(hdf_bytes: bytes, expected_sha256: str):
    with h5py.File(io.BytesIO(hdf_bytes), "r") as h5:
        restored = h5["source/raw_sdf"][:].tobytes()
        return {
            "valid_hdf5": True,
            "stored_sha256": h5.attrs["source_sha256"],
            "restored_sha256": sha256_bytes(restored),
            "lossless": sha256_bytes(restored) == expected_sha256,
            "source_size": int(h5.attrs["source_size_bytes"]),
        }


st.set_page_config(page_title="SDF → HDF5", page_icon="📈", layout="wide")
st.title(APP_TITLE)

st.info(
    "This application creates a valid, lossless HDF5 container from an LMS / "
    "Simcenter Testlab SDF file. The original SDF is preserved byte-for-byte. "
    "Portable Python alone does not reliably decode proprietary Testlab numeric "
    "signal blocks."
)

uploaded = st.file_uploader("Upload SDF file", type=["sdf"])

if uploaded is not None:
    raw = uploaded.getvalue()
    source_hash = sha256_bytes(raw)
    family = detect_sdf_family(raw[:4096])

    c1, c2, c3 = st.columns(3)
    c1.metric("File size", f"{len(raw) / (1024**2):.1f} MB")
    c2.metric("Detected format", family)
    c3.metric("SHA-256", source_hash[:16] + "…")

    st.subheader("Metadata inspection")

    # Scan a bounded portion first for responsiveness.
    # User can choose full scan if desired.
    full_scan = st.checkbox(
        "Scan complete file for printable metadata",
        value=False,
        help="For large files this uses more RAM/CPU. It still does not decode numeric signal blocks.",
    )
    scan_bytes = raw if full_scan else raw[: min(len(raw), 32 * 1024 * 1024)]
    strings = extract_printable_strings(scan_bytes)

    candidates = interesting_names(strings)
    if candidates:
        st.write("Possible measurement/channel labels found:")
        st.dataframe(
            {"candidate": candidates},
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.warning("No obvious channel-name strings were detected in the scanned region.")

    with st.expander("Show extracted printable metadata strings"):
        st.write(strings[:1000])

    st.subheader("Create HDF5")

    compression = st.selectbox(
        "Compression",
        ["gzip", "lzf", "none"],
        index=0,
        help="gzip gives smaller files; LZF is faster; none avoids compression overhead.",
    )

    if st.button("Convert to HDF5", type="primary"):
        with st.spinner("Creating HDF5 container…"):
            hdf_bytes = build_hdf5(
                raw=raw,
                original_name=uploaded.name,
                metadata_strings=strings,
                compression=compression,
            )
            check = verify_hdf5(hdf_bytes, source_hash)

        if check["lossless"]:
            st.success(
                "Conversion completed and verified: the SDF stored inside the HDF5 "
                "matches the uploaded file byte-for-byte."
            )
        else:
            st.error("Integrity verification failed.")
            st.stop()

        out_name = uploaded.name.rsplit(".", 1)[0] + ".h5"

        v1, v2 = st.columns(2)
        v1.metric("HDF5 size", f"{len(hdf_bytes) / (1024**2):.1f} MB")
        v2.metric("Integrity", "PASS" if check["lossless"] else "FAIL")

        st.download_button(
            "Download HDF5",
            data=hdf_bytes,
            file_name=out_name,
            mime="application/x-hdf5",
            use_container_width=True,
        )

        with st.expander("HDF5 structure"):
            st.code(
                """/
├── attributes
│   ├── source_filename
│   ├── source_size_bytes
│   ├── source_sha256
│   ├── detected_source_family
│   └── numeric_channels_decoded = False
├── source/
│   └── raw_sdf                 # original SDF, byte-for-byte
├── metadata/
│   ├── printable_strings
│   └── channel_name_candidates
└── signals/
    └── status = not_decoded
""",
                language="text",
            )

st.divider()

st.caption(
    "For TRUE channel-level conversion (Time, RPM/Tacho, accelerometers, microphones, "
    "CAN channels, units, sampling rates, etc.), add a Siemens Simcenter Testlab "
    "Automation export service on Windows with Testlab installed/licensed. The exported "
    "numeric arrays can then be written under /signals in HDF5."
)
