import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from lms_sdf_reader import LMSTestLabSDF, SDFFormatError

st.set_page_config(page_title='LMS SDF → HDF5', page_icon='🔄', layout='wide')
st.title('LMS / Testlab SDF → HDF5 Converter')
st.caption('Standalone conversion only — Simcenter Testlab installation is not required.')

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
                'Sensor sensitivity metadata': c.sensor_scale,
            }
            for c in reader.channels
        ])
        st.dataframe(table, use_container_width=True, hide_index=True)

        st.info(
            'Signal values are exported exactly as decoded from the SDF. '
            'Pa channels are NOT scaled a second time. Sensitivity / SI information '
            'is preserved only as HDF5 metadata.'
        )

        default_channels = [c.name for c in reader.channels]
        selected = st.multiselect(
            'Channels to export',
            default_channels,
            default=default_channels,
        )

        col1, col2 = st.columns(2)
        compression = col1.selectbox(
            'HDF5 compression',
            ['lzf', 'gzip', 'none'],
            index=0,
        )
        explicit_time = col2.checkbox(
            'Store explicit time arrays',
            value=False,
            help=(
                'For uniformly sampled signals, x_start=0 and sample_interval are '
                'normally enough. Enabling this makes the HDF5 considerably larger.'
            ),
        )

        st.subheader('Optional tacho processing')
        has_raw_tacho = any(
            c.name.lower().startswith('raw:tacho') and c.sampling_type == 'event_timestamps'
            for c in reader.channels
        )

        reconstruct_rpm = st.checkbox(
            'Reconstruct RPM from raw:Tacho timestamps',
            value=False,
            disabled=not has_raw_tacho,
            help=(
                'Creates an additional /derived/reconstructed_rpm dataset. '
                'The original raw:Tacho1 and Tacho1 channels remain unchanged.'
            ),
        )

        rcol1, rcol2 = st.columns(2)
        pulses_per_revolution = rcol1.number_input(
            'Tacho pulses per revolution (PPR)',
            min_value=0.001,
            value=12.0,
            step=1.0,
            disabled=not reconstruct_rpm,
            help=(
                'PPR depends on the measurement setup. 12 is provided as a convenient '
                'starting value for this test family, but the converter does not assume '
                'that it is universally correct.'
            ),
        )
        rpm_window_s = rcol2.number_input(
            'RPM reconstruction window [s]',
            min_value=0.05,
            value=1.0,
            step=0.1,
            disabled=not reconstruct_rpm,
            help=(
                'RPM is calculated from the number of raw tacho events in each time window. '
                '1.0 s is robust for this file; shorter windows give higher time resolution.'
            ),
        )

        if reconstruct_rpm:
            st.warning(
                'Reconstructed RPM is a derived signal. Confirm the correct PPR for the '
                'measurement setup before using it as an engineering reference.'
            )

        if st.button('Convert to HDF5', type='primary', disabled=not selected):
            h5_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.h5')
            h5_tmp.close()
            out = Path(h5_tmp.name)

            with st.spinner('Decoding SDF and writing HDF5…'):
                reader.to_hdf5(
                    out,
                    selected_channels=selected,
                    compression=None if compression == 'none' else compression,
                    explicit_time=explicit_time,
                    reconstruct_rpm=reconstruct_rpm,
                    raw_tacho_channel='raw:Tacho1',
                    pulses_per_revolution=float(pulses_per_revolution),
                    rpm_window_s=float(rpm_window_s),
                )

            # Lightweight output verification.
            import h5py
            with h5py.File(out, 'r') as h5:
                converted_count = len(h5['channels'])
                if converted_count != len(selected):
                    st.error(
                        f'Output verification failed: expected {len(selected)} channels, '
                        f'found {converted_count}.'
                    )
                    st.stop()

                if reconstruct_rpm:
                    if 'derived/reconstructed_rpm/rpm' not in h5:
                        st.error('RPM reconstruction was requested but the derived RPM dataset is missing.')
                        st.stop()
                    rpm_count = len(h5['derived/reconstructed_rpm/rpm'])
                else:
                    rpm_count = None

            st.success(
                f'Conversion complete and verified: '
                f'{out.stat().st_size / (1024**2):.1f} MB'
            )

            if rpm_count is not None:
                st.success(
                    f'Derived RPM created: {rpm_count:,} windowed samples '
                    f'(PPR={float(pulses_per_revolution):g}, window={float(rpm_window_s):g} s).'
                )

            output_name = Path(uploaded.name).stem + '.h5'
            with out.open('rb') as f:
                st.download_button(
                    'Download HDF5',
                    data=f,
                    file_name=output_name,
                    mime='application/x-hdf5',
                    use_container_width=True,
                )

            st.caption(
                'Original decoded channel values are preserved as-is. '
                'Any reconstructed RPM is stored separately under /derived.'
            )
    finally:
        try:
            sdf_tmp.close()
        except Exception:
            pass
