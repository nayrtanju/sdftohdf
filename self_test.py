
"""
Run a full converter self-test against an SDF file.

Example:
    python self_test.py "C:\\Data\\240825154-20%-100% 5bar.sdf"

The script creates a temporary MF4, re-opens it with asammdf, and checks
all channels against the source SDF.
"""
from pathlib import Path
import sys
import tempfile

from lms_sdf_reader import LMSTestLabSDF
from mdf4_exporter import convert_sdf_to_mdf4, verify_mdf4_against_sdf


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python self_test.py "path\\to\\measurement.sdf"')

    source = Path(sys.argv[1])
    reader = LMSTestLabSDF(source)
    print(f"SDF PASS: {reader.channel_count} channels; data start={reader.data_start}")

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "verification.mf4"
        convert_sdf_to_mdf4(source, out, compression=0)
        print(f"MF4 created: {out.stat().st_size / (1024**2):.1f} MB")

        result = verify_mdf4_against_sdf(source, out)
        print(f"MDF version: {result.version}")
        print(f"Channels checked: {result.channel_count}")
        if result.problems:
            print("FAIL")
            for p in result.problems:
                print(" -", p)
            raise SystemExit(1)

        print("FULL SDF -> MDF4 -> REOPEN VERIFICATION: PASS")


if __name__ == "__main__":
    main()
