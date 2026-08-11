
from pathlib import Path
import io
import tempfile
import zipfile

import pandas as pd
import streamlit as st

from lms_sdf_reader import LMSTestLabSDF, SDFFormatError
from mdf4_exporter import convert_sdf_to_mdf4, verify_mdf4_against_sdf
from openatfx_bridge import create_atfx_header, OpenATFXNotInstalled

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="LMS SDF → ATFX", page_icon="🔄", layout="wide")
st.title("LMS / Testlab SDF → ATFX Converter")
st.caption("Creates ASAM ATFX + linked MDF4 mass-data component.")

converter_ready = (ROOT / "tools" / "openatfx_mdf_converter" / "bin").exists()
if not converter_ready:
    st.warning(
        "ATFX bridge is not installed yet. Run once:\n\n"
        "`python setup_openatfx.py`\n\n"
        "Then restart Streamlit."
    )

uploaded = st.file_uploader("Upload LMS/Testlab .sdf file", type=["sdf"])

if uploaded:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        sdf_path = td / uploaded.name

        with sdf_path.open("wb") as f:
            while True:
                chunk = uploaded.read(4 * 1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

        try:
            reader = LMSTestLabSDF(sdf_path)
        except SDFFormatError as exc:
            st.error(f"Unsupported or structurally different SDF file: {exc}")
            st.stop()

        st.success(f"SDF validated: {reader.channel_count} channels")

        rows = [{
            "Channel": c.name,
            "Unit": c.unit,
            "Sampling": c.sampling_type,
            "Sample rate [Hz]": round(c.sample_rate, 6) if c.sample_rate else None,
            "Samples": c.sample_count,
        } for c in reader.channels]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        names = [c.name for c in reader.channels]
        selected = st.multiselect("Channels to export", names, default=names)

        st.info(
            "ATFX is the XML description layer; the large channel data remains in the "
            "linked MF4 external component. The download contains both files in one ZIP."
        )

        if st.button(
            "Convert to ATFX bundle",
            type="primary",
            disabled=(not selected or not converter_ready),
        ):
            stem = Path(uploaded.name).stem
            mf4_path = td / f"{stem}.mf4"

            with st.spinner("Step 1/3 — SDF → MDF4"):
                convert_sdf_to_mdf4(
                    sdf_path,
                    mf4_path,
                    selected_channels=selected,
                    mdf_version="4.10",
                    compression=0,
                )

            with st.spinner("Step 2/3 — Verify MDF4 against SDF"):
                result = verify_mdf4_against_sdf(
                    sdf_path,
                    mf4_path,
                    selected_channels=selected,
                )

            if not result.ok:
                st.error("MDF4 verification failed; ATFX generation was stopped.")
                for problem in result.problems:
                    st.write("• " + problem)
                st.stop()

            with st.spinner("Step 3/3 — MDF4 → ATFX"):
                try:
                    atfx_path = create_atfx_header(mf4_path, ROOT)
                except (OpenATFXNotInstalled, Exception) as exc:
                    st.error(str(exc))
                    st.stop()

            head = atfx_path.read_bytes()[:4096]
            if b"<" not in head:
                st.error("Generated ATFX does not look like XML.")
                st.stop()

            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(atfx_path, atfx_path.name)
                z.write(mf4_path, mf4_path.name)
                z.writestr(
                    "README_IMPORT.txt",
                    "Keep the .atfx and .mf4 files in the same folder.\n"
                    "Open/import the .atfx file in ArtemiS SUITE.\n"
                    "The .mf4 is the external mass-data component.\n"
                )
            bundle.seek(0)

            st.success(
                f"ATFX bundle created. MDF4 verification passed for "
                f"{result.channel_count} channels."
            )

            st.download_button(
                "Download ATFX bundle",
                data=bundle.getvalue(),
                file_name=f"{stem}_ATFX_bundle.zip",
                mime="application/zip",
                use_container_width=True,
            )
