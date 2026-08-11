import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from lms_sdf_reader import LMSTestLabSDF, SDFFormatError

st.set_page_config(page_title='LMS SDF → HDF5', page_icon='📈', layout='wide')
st.title('LMS / Testlab SDF → HDF5 Converter')
st.caption('Standalone Python converter — Simcenter Testlab installation is not required.')

uploaded = st.file_uploader('Upload .sdf file', type=['sdf'])

if uploaded is not None:
    sdf_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.sdf')
    try:
        while True:
            chunk = uploaded.read(4 * 1024 * 1024)
            if not chunk:
                break
            sdf_tmp.write(chunk)
        sdf_tmp.close()
        sdf_path = Path(sdf_tmp.name)

        try:
            reader = LMSTestLabSDF(sdf_path)
        except SDFFormatError as e:
            st.error(f'Unsupported or structurally different SDF file: {e}')
            st.stop()

        st.success(f'Validated LMS/Testlab SDF: {reader.channel_count} channels')

        table = pd.DataFrame([
            {
                'Channel': c.name,
                'Unit': c.unit,
                'Sampling': c.sampling_type,
                'Sample rate [Hz]': round(c.sample_rate, 6) if c.sample_rate else None,
                'Samples': c.sample_count,
                'Duration [s]': round(c.sample_count / c.sample_rate, 6) if c.sample_rate else None,
                'Sensor input unit': c.sensor_input_unit,
                'Sensor output unit': c.sensor_output_unit,
            }
            for c in reader.channels
        ])
        st.dataframe(table, use_container_width=True, hide_index=True)

        default_channels = [c.name for c in reader.channels]
        selected = st.multiselect('Channels to export', default_channels, default=default_channels)

        col1, col2 = st.columns(2)
        compression = col1.selectbox('HDF5 compression', ['lzf', 'gzip', 'none'], index=0)
        explicit_time = col2.checkbox(
            'Store explicit time arrays',
            value=False,
            help='Normally x_start=0 and sample_interval are stored as attributes. Explicit time arrays make the HDF5 much larger.'
        )

        if st.button('Convert to HDF5', type='primary', disabled=not selected):
            h5_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.h5')
            h5_tmp.close()
            out = Path(h5_tmp.name)

            with st.spinner('Decoding SDF and writing channel-level HDF5…'):
                reader.to_hdf5(
                    out,
                    selected_channels=selected,
                    compression=None if compression == 'none' else compression,
                    explicit_time=explicit_time,
                )

            st.success(f'Conversion complete: {out.stat().st_size / (1024**2):.1f} MB')
            output_name = Path(uploaded.name).stem + '.h5'
            with out.open('rb') as f:
                st.download_button(
                    'Download HDF5',
                    data=f,
                    file_name=output_name,
                    mime='application/x-hdf5',
                    use_container_width=True,
                )

            st.info(
                'The converter preserves decoded float values as-is. Unit/SI/calibration factors are saved as metadata and are not automatically applied.'
            )
    finally:
        try:
            sdf_tmp.close()
        except Exception:
            pass
