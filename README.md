# LMS SDF → HDF5 Converter

A focused Streamlit tool for converting the validated LMS Test.Lab SDF structure
into standard HDF5 without requiring Simcenter Testlab.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Features

- Validates the LMS `LMS T.L` signature.
- Parses the validated 148-byte channel descriptor structure.
- Detects channel names, units, sample counts and sample intervals.
- Reads big-endian float32 signal payloads.
- Converts selected channels to HDF5.
- Chunked reading/writing to reduce peak memory usage.
- gzip / LZF / uncompressed HDF5 output.
- Channel preview.
- Output integrity verification.

## Important compatibility note

This reader is intentionally strict and targets the LMS SDF layout validated
against the supplied sample file. It will reject structurally different SDF
variants instead of silently producing incorrect measurement data.
