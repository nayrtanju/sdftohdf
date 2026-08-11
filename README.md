# LMS / Testlab SDF → ASAM MDF4 Streamlit Converter

This package keeps the already validated LMS/Testlab SDF reader and replaces
the HDF5 output layer with **ASAM MDF 4.10 (`.mf4`)**.

## Why MDF4?

HEAD acoustics added MDF4 import support to ArtemiS SUITE 16.5 via **ASP 707 MDF4 Import**.
This is an ASAM MDF4 file, not a generic HDF5 file.

## Installation

Python 3.10+:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## First ArtemiS compatibility test

Use:

- MDF version: 4.10 (fixed by the app)
- Compression: **None**
- Export all channels

The app then:
1. creates the MF4,
2. re-opens it with `asammdf`,
3. compares representative numeric samples against the source SDF,
4. checks channel units and timebases,
5. only enables download after the verification passes.

## Timebase strategy

The supplied SDF has:
- a large common ~51.2 kHz group,
- a 200 Hz EHPS signal,
- non-equidistant `raw:Tacho1` event timestamps.

The converter creates separate MDF channel groups for different timebases.

## Calibration

Pa signal values are written exactly as decoded from the SDF.
No second sensitivity/calibration scaling is applied. The original LMS
sensor sensitivity information is stored in each channel comment as metadata.

## `raw:Tacho1`

The raw tacho event list is stored as a non-equidistant MDF channel.
Its sample value and timestamp both contain the event time, preserving the
original event-time information.

## ArtemiS requirement

ArtemiS SUITE requires MDF4 import capability (ASP 707 / corresponding license).
If an MF4 passes this package's asammdf verification but ArtemiS still does not
offer MDF4 import, check the installed ArtemiS version and MDF4 Import license/module.
