
from pathlib import Path
import io
import json
import tempfile
import zipfile

import pandas as pd
import streamlit as st

from lms_sdf_reader import LMSTestLabSDF, SDFFormatError
from atfx_writer import convert_sdf_to_atfx_bundle


st.set_page_config(page_title="SDF → ATFX", page_icon="🔄", layout="wide")
st.title("LMS / Testlab SDF → ATFX Converter")
st.caption("Pure Python — no Java, no openATFX bridge, no manual setup command.")

uploaded = st.file_uploader("Upload LMS/Testlab .sdf file", type=["sdf"])

if uploaded is not None:
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
            st.error(f"Unsupported or structurally different SDF: {exc}")
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
            "Output is an ATFX + external DAT bundle. Keep the .atfx and .dat "
            "files in the same folder. Open/import the .atfx file in ArtemiS."
        )

        if st.button("Convert to ATFX", type="primary", disabled=not selected):
            out_dir = td / "output"
            with st.spinner("Writing ATFX metadata and external DAT payload…"):
                atfx, dat, report = convert_sdf_to_atfx_bundle(
                    sdf_path, out_dir, selected_channels=selected
                )

            st.success(
                f"Conversion complete: {report['channels']} channels, "
                f"{report['dat_bytes'] / (1024**2):.1f} MB binary payload."
            )

            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(atfx, atfx.name)
                z.write(dat, dat.name)
                z.writestr("conversion_report.json", json.dumps(report, indent=2))
                z.writestr(
                    "README_IMPORT.txt",
                    "Extract both files into the same folder.\n"
                    "Open/import the .atfx file in ArtemiS SUITE.\n"
                    "Do not rename the .dat file without also changing the ATFX reference.\n"
                )
            bundle.seek(0)

            st.download_button(
                "Download ATFX bundle",
                data=bundle.getvalue(),
                file_name=f"{Path(uploaded.name).stem}_ATFX_bundle.zip",
                mime="application/zip",
                use_container_width=True,
            )
