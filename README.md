# LMS / Testlab SDF → HDF5 Converter v2

Focused standalone converter. Simcenter Testlab is not required.

## Changes in v2

- Preserves the validated SDF parser used for the supplied LMS/Testlab file.
- Does **not** apply a second calibration/scaling to Pa channels.
- Stores sensor sensitivity / unit / SI scale as metadata only.
- Keeps `raw:Tacho1` and `Tacho1` unchanged.
- Adds an optional `raw:Tacho1 -> reconstructed RPM` export.
- RPM reconstruction is stored separately under `/derived/reconstructed_rpm`.
- PPR is user-selectable; it is never silently hard-coded as a universal value.
- Performs a lightweight HDF5 output verification after conversion.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Derived RPM

When enabled:

```text
/derived/reconstructed_rpm
    /time
    /rpm
    attributes:
        pulses_per_revolution
        source_channel
        method
```

RPM reconstruction uses a robust pulse-count window:

```text
RPM = (pulse_count / window_s) * 60 / PPR
```

The default window is 1.0 s. Both PPR and window length are user-selectable.

The original SDF channel data is never modified by this option.

## Compatibility

This is a strict reader for the LMS/Testlab SDF layout validated against the supplied
sample file. Structurally different SDF variants are rejected rather than guessed.
