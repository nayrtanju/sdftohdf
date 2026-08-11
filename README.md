# LMS / Simcenter Testlab SDF → HDF5 Streamlit Converter

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## What this version does

- Accepts `.sdf` uploads in Streamlit.
- Detects the `LMS T.L` signature used by the supplied file.
- Creates a standards-compliant HDF5 (`.h5`) file.
- Stores the original SDF losslessly under `/source/raw_sdf`.
- Stores SHA-256 metadata and verifies byte-for-byte integrity.
- Scans printable metadata and exposes likely channel-name strings.

## Important limitation

The supplied file is an LMS / Siemens Simcenter Testlab proprietary SDF binary.
This portable Python application does **not** claim to decode its proprietary
numeric signal blocks.

For true signal conversion (Time/RPM/Tacho/accelerometer/microphone/CAN arrays,
units, sample rate, etc.), use Simcenter Testlab Automation or an officially
supported export mechanism on Windows with Testlab installed and licensed, then
write those arrays into `/signals`.
