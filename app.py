
from pathlib import Path
import os
import tempfile

import pandas as pd
import streamlit as st

from lms_sdf_reader import LMSTestLabSDF, SDFFormatError
from mdf4_exporter import convert_sdf_to_mdf4, verify_mdf4_against_sdf


st.set_page_config(page_title="LMS SDF → MDF4", page_icon="🔄", layout="wide")
st.title("LMS / Testlab SDF → MDF4 Converter")
st.caption("Output: ASAM MDF 4.10 (.mf4) for MDF4-capable analysis tools such as ArtemiS SUITE.")

uploaded = st.file_uploader("Upload LMS/Testlab .sdf file", type=["sdf"])

if uploaded is not None:
    sdf_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sdf")
    sdf_path = Path(sdf_tmp.name)

    try:
        # Stream upload to disk to avoid an extra full-size RAM copy.
        while True:
            chunk = uploaded.read(4 * 1024 * 1024)
            if not chunk:
                break
            sdf_tmp.write(chunk)
        sdf_tmp.close()

        try:
            reader = LMSTestLabSDF(sdf_path)
        except SDFFormatError as exc:
            st.error(f"Unsupported or structurally different SDF file: {exc}")
            st.stop()

        st.success(f"SDF structure validated: {reader.channel_count} channels")

        rows = []
        for ch in reader.channels:
            rows.append(
                {
                    "Channel": ch.name,
                    "Unit": ch.unit,
                    "Sampling": ch.sampling_type,
                    "Sample rate [Hz]": (
                        round(ch.sample_rate, 6) if ch.sample_rate else None
                    ),
                    "Samples": ch.sample_count,
                    "Sensor input": ch.sensor_input_unit,
                    "Sensor output": ch.sensor_output_unit,
                    "Sensitivity metadata": ch.sensor_scale,
                }
            )

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.info(
            "The converter writes standard ASAM MDF4. Signal values decoded from the "
            "SDF are preserved without applying a second Pa calibration. Channels with "
            "the same sampling rate share a common MDF master timebase."
        )

        names = [ch.name for ch in reader.channels]
        selected = st.multiselect(
            "Channels to export",
            names,
            default=names,
        )

        compression_label = st.selectbox(
            "MDF4 compression",
            [
                "None — maximum compatibility",
                "Deflate — smaller file",
                "Transpose + Deflate — smallest MDF 4.10 file",
            ],
            index=0,
        )
        compression = {
            "None — maximum compatibility": 0,
            "Deflate — smaller file": 1,
            "Transpose + Deflate — smallest MDF 4.10 file": 2,
        }[compression_label]

        st.caption(
            "For the first ArtemiS test, use MDF 4.10 with no compression. "
            "Once import is confirmed, Deflate can be enabled."
        )

        if st.button("Convert to MDF4", type="primary", disabled=not selected):
            out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mf4")
            out_tmp.close()
            out_path = Path(out_tmp.name)

            try:
                with st.spinner("Creating ASAM MDF4…"):
                    convert_sdf_to_mdf4(
                        sdf_path,
                        out_path,
                        selected_channels=selected,
                        mdf_version="4.10",
                        compression=compression,
                    )

                with st.spinner("Re-opening MDF4 and verifying channels…"):
                    result = verify_mdf4_against_sdf(
                        sdf_path,
                        out_path,
                        selected_channels=selected,
                    )

                if not result.ok:
                    st.error("MDF4 was created but verification failed.")
                    for problem in result.problems:
                        st.write("• " + problem)
                    st.stop()

                st.success(
                    f"MDF4 verified successfully: version {result.version}, "
                    f"{result.channel_count} channels checked."
                )

                output_name = Path(uploaded.name).stem + ".mf4"
                with out_path.open("rb") as f:
                    st.download_button(
                        "Download MDF4",
                        data=f,
                        file_name=output_name,
                        mime="application/octet-stream",
                        use_container_width=True,
                    )

                st.caption(
                    "Verification re-opens the MF4 with asammdf and compares channel "
                    "presence, representative numeric samples, units and timebases "
                    "against the source SDF."
                )
            finally:
                try:
                    out_path.unlink(missing_ok=True)
                except Exception:
                    pass
    finally:
        try:
            sdf_tmp.close()
        except Exception:
            pass
        try:
            sdf_path.unlink(missing_ok=True)
        except Exception:
            pass
