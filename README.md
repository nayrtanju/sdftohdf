# LMS/Testlab SDF → ATFX Streamlit Converter

This package uses:

SDF → MDF 4.10 → Eclipse openATFX MDF converter → ATFX + linked MF4

The ATFX header references the MF4 as the external mass-data component.

## Setup

1. Install Python packages:

```bash
pip install -r requirements.txt
```

2. Install/select a Java 8 JDK, then run once:

```bash
python setup_openatfx.py
```

3. Start Streamlit:

```bash
streamlit run app.py
```

## Output

The app downloads a ZIP containing:

- `measurement.atfx`
- `measurement.mf4`
- `README_IMPORT.txt`

Extract the ZIP and keep `.atfx` and `.mf4` together. Open/import the `.atfx`
file in ArtemiS SUITE.

## Validation

Before ATFX creation, the generated MDF4 is re-opened and checked against the
source SDF for channel presence, representative numerical samples, units and
timebases.

The final ATFX header itself is delegated to Eclipse's official
`MDFConverter.writeATFXHeader(...)` implementation rather than hand-writing a
private ATFX XML dialect.


## Streamlit Cloud

`h5py` is required because `lms_sdf_reader.py` imports it. It is included in
this package's `requirements.txt`.

For Streamlit Cloud, keep `requirements.txt` in the repository root or in the
same app directory recognized by the deployment.
