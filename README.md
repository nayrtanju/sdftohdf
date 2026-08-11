# Pure-Python LMS/Testlab SDF → ATFX Converter

No Java and no `setup_openatfx.py` step are required.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Output

The ZIP contains:

- `measurement.atfx` — ASAM ODS ATF/XML description
- `measurement.dat` — external binary mass-data component
- `conversion_report.json`
- `README_IMPORT.txt`

Extract `.atfx` and `.dat` into the same directory and open/import the `.atfx`
file in ArtemiS SUITE.

## Implementation

- Uses the validated LMS/Testlab SDF parser.
- Main uniformly sampled channels are represented with implicit-linear time axes.
- Signal payloads are stored as little-endian float32 in the external `.dat`.
- Different sample-rate groups are represented by separate SubMatrix instances.
- Event-timestamp channels are stored as explicit external-component columns.
- No second Pa calibration is applied.

## Compatibility note

ATFX is an ASAM ODS application-model format. This writer follows the ASAM
ATF/XML external-component structure and performs XML and payload-size checks.
Final vendor-specific import compatibility must still be confirmed in ArtemiS.
