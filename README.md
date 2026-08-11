# Fixed LMS/Testlab SDF → HDF5 Converter

This package fixes the descriptor-parsing regression in the previous clean package.

Run:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The parser in this package was regression-tested against the supplied
`240825154-20%-100% 5bar.sdf` sample and detects 22 channels.

Important: this is a strict reader for the validated LMS/Testlab SDF family.
Structurally different SDF variants are rejected rather than guessed.
