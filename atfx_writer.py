
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, ElementTree, register_namespace
import math
import os

import numpy as np

from lms_sdf_reader import LMSTestLabSDF


ASAM_NS = "http://www.asam.net"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
register_namespace("", ASAM_NS)
register_namespace("xsi", XSI_NS)

def q(tag: str) -> str:
    return f"{{{ASAM_NS}}}{tag}"

def add_text(parent, tag, value):
    e = SubElement(parent, q(tag))
    e.text = str(value)
    return e

def app_attr(parent, name, *, base=None, datatype=None, obligatory=None, unique=None, length=None):
    aa = SubElement(parent, q("application_attribute"))
    add_text(aa, "name", name)
    if base:
        add_text(aa, "base_attribute", base)
    if datatype:
        add_text(aa, "datatype", datatype)
    if obligatory is not None:
        add_text(aa, "obligatory", str(obligatory).lower())
    if unique is not None:
        add_text(aa, "unique", str(unique).lower())
    if length is not None:
        add_text(aa, "length", length)
    return aa

def rel(parent, name, ref_to, *, base=None, inverse=None, min_occurs=0, max_occurs=1):
    rr = SubElement(parent, q("relation_attribute"))
    add_text(rr, "name", name)
    add_text(rr, "ref_to", ref_to)
    if base:
        add_text(rr, "base_relation", base)
    add_text(rr, "min_occurs", min_occurs)
    add_text(rr, "max_occurs", max_occurs)
    if inverse:
        add_text(rr, "inverse_name", inverse)
    return rr


@dataclass
class Component:
    identifier: str
    filename: str
    start_offset: int
    sample_count: int
    value_type: str = "ieeefloat4"
    block_size: int = 4
    values_per_block: int = 1


def _build_application_model(root):
    am = SubElement(root, q("application_model"))

    # Measurement
    e = SubElement(am, q("application_element"))
    add_text(e, "name", "Measurement")
    add_text(e, "basetype", "AoMeasurement")
    app_attr(e, "Id", base="id", obligatory=True)
    app_attr(e, "Name", base="name", obligatory=True, length=255)
    rel(e, "SubMatrices", "SubMatrix", base="submatrices", inverse="Measurement", max_occurs="unbounded")
    rel(e, "Quantities", "MeasurementQuantity", base="measurement_quantities", inverse="Measurement", max_occurs="unbounded")

    # Unit
    e = SubElement(am, q("application_element"))
    add_text(e, "name", "Unit")
    add_text(e, "basetype", "AoUnit")
    app_attr(e, "Id", base="id", obligatory=True)
    app_attr(e, "Name", base="name", obligatory=True, length=64)
    app_attr(e, "Factor", base="factor", obligatory=False)
    app_attr(e, "Offset", base="offset", obligatory=False)

    # MeasurementQuantity
    e = SubElement(am, q("application_element"))
    add_text(e, "name", "MeasurementQuantity")
    add_text(e, "basetype", "AoMeasurementQuantity")
    app_attr(e, "Id", base="id", obligatory=True)
    app_attr(e, "Name", base="name", obligatory=True, length=255)
    app_attr(e, "Datatype", base="datatype_enum", obligatory=True)
    app_attr(e, "Rank", base="rank", obligatory=True)
    rel(e, "Measurement", "Measurement", base="measurement", inverse="Quantities", min_occurs=1)
    rel(e, "Unit", "Unit", base="unit", inverse="Quantities", min_occurs=0)

    # SubMatrix
    e = SubElement(am, q("application_element"))
    add_text(e, "name", "SubMatrix")
    add_text(e, "basetype", "AoSubmatrix")
    app_attr(e, "Id", base="id", obligatory=True)
    app_attr(e, "Name", base="name", obligatory=True, length=255)
    app_attr(e, "NumberOfRows", base="number_of_rows", obligatory=True)
    rel(e, "Measurement", "Measurement", base="measurement", inverse="SubMatrices", min_occurs=1)
    rel(e, "LocalColumns", "LocalColumn", base="local_columns", inverse="SubMatrix", max_occurs="unbounded")

    # LocalColumn
    e = SubElement(am, q("application_element"))
    add_text(e, "name", "LocalColumn")
    add_text(e, "basetype", "AoLocalColumn")
    app_attr(e, "Id", base="id", obligatory=True)
    app_attr(e, "Name", base="name", obligatory=True, length=255)
    app_attr(e, "Independent", base="independent", obligatory=True)
    app_attr(e, "SequenceRepresentation", base="sequence_representation", obligatory=True)
    app_attr(e, "GenerationParameters", base="generation_parameters", obligatory=False)
    app_attr(e, "GlobalFlag", base="global_flag", obligatory=False)
    rel(e, "SubMatrix", "SubMatrix", base="submatrix", inverse="LocalColumns", min_occurs=1)
    rel(e, "MeasurementQuantity", "MeasurementQuantity", base="measurement_quantity", inverse="LocalColumns", min_occurs=1)
    rel(e, "ExternalComponents", "ExternalComponent", inverse="LocalColumn", max_occurs="unbounded")

    # ExternalComponent
    e = SubElement(am, q("application_element"))
    add_text(e, "name", "ExternalComponent")
    add_text(e, "basetype", "AoExternalComponent")
    app_attr(e, "Id", base="id", obligatory=True)
    app_attr(e, "FilenameUrl", base="filename_url", obligatory=True, length=1024)
    app_attr(e, "ValueType", base="value_type", obligatory=True)
    app_attr(e, "StartOffset", base="start_offset", obligatory=True)
    app_attr(e, "BlockSize", base="block_size", obligatory=True)
    app_attr(e, "ValuesPerBlock", base="valuesperblock", obligatory=True)
    app_attr(e, "OrdinalNumber", base="ordinal_number", obligatory=False)
    rel(e, "LocalColumn", "LocalColumn", inverse="ExternalComponents", min_occurs=1)

    return am


def _add_instance(inst, tag, **fields):
    e = SubElement(inst, q(tag))
    for k, v in fields.items():
        if v is None:
            continue
        add_text(e, k, v)
    return e


def convert_sdf_to_atfx_bundle(
    sdf_path: str | Path,
    output_dir: str | Path,
    selected_channels: Iterable[str] | None = None,
) -> tuple[Path, Path, dict]:
    """
    Pure-Python SDF -> ATFX + DAT external component bundle.

    Uniform signal values are written as little-endian float32 to one external
    DAT file. Each different sample rate gets its own SubMatrix and implicit
    linear time column. raw:Tacho event timestamps get their own SubMatrix with
    an explicit external time/value column.

    No Java/openATFX runtime is required.
    """
    sdf_path = Path(sdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    reader = LMSTestLabSDF(sdf_path)

    selected = [c.name for c in reader.channels] if selected_channels is None else list(selected_channels)
    infos = [reader.channel(n) for n in selected]

    stem = sdf_path.stem
    dat_path = output_dir / f"{stem}.dat"
    atfx_path = output_dir / f"{stem}.atfx"

    # Write all binary payloads contiguously and record offsets.
    components = {}
    current_offset = 0
    with dat_path.open("wb") as bf:
        for ch in infos:
            values = reader.read_channel(ch.name).astype("<f4", copy=False)
            bf.write(values.tobytes(order="C"))
            components[ch.name] = Component(
                identifier=f"comp_{len(components)+1}",
                filename=dat_path.name,
                start_offset=current_offset,
                sample_count=len(values),
            )
            current_offset += values.nbytes

    root = Element(
        q("atfx_file"),
        {
            "version": "atfx_file v1.0.1",
            f"{{{XSI_NS}}}schemaLocation": "http://www.asam.net https://www.asam.net/ODS/5.3.0/Schema/Schema.xsd",
        },
    )

    doc = SubElement(root, q("documentation"))
    add_text(doc, "exported_by", "Streamlit SDF to ATFX Converter")
    add_text(doc, "exporter", "Pure Python LMS/Testlab SDF Converter")
    add_text(doc, "export_date_time", datetime.now(timezone.utc).strftime("%d.%m.%Y.%H%M%S00"))
    add_text(doc, "exporter_version", "1.0")

    add_text(root, "locale", "US-EN")
    add_text(root, "base_model_version", "31")

    files = SubElement(root, q("files"))
    comp = SubElement(files, q("component"))
    add_text(comp, "identifier", "massdata")
    add_text(comp, "filename", dat_path.name)

    _build_application_model(root)
    inst = SubElement(root, q("instance_data"))

    next_id = 1
    measurement_id = next_id
    next_id += 1
    _add_instance(inst, "Measurement", Id=measurement_id, Name=stem)

    # Units
    unit_ids = {}
    for ch in infos:
        unit = ch.unit or ch.sensor_output_unit or ""
        if unit and unit not in unit_ids:
            unit_ids[unit] = next_id
            _add_instance(inst, "Unit", Id=next_id, Name=unit, Factor=1.0, Offset=0.0)
            next_id += 1

    # Quantities
    mq_ids = {}
    for ch in infos:
        mq_ids[ch.name] = next_id
        unit = ch.unit or ch.sensor_output_unit or ""
        _add_instance(
            inst,
            "MeasurementQuantity",
            Id=next_id,
            Name=ch.name,
            Datatype="DT_FLOAT",
            Rank=1,
            Measurement=measurement_id,
            Unit=unit_ids.get(unit),
        )
        next_id += 1

    # Group channels by uniform timebase; event channels separate.
    groups = {}
    event_infos = []
    for ch in infos:
        if ch.sampling_type == "event_timestamps":
            event_infos.append(ch)
            continue
        dt = ch.sample_interval if ch.sample_interval else (1.0 / ch.sample_rate)
        key = (ch.sample_count, round(float(dt), 15))
        groups.setdefault(key, []).append(ch)

    submatrix_count = 0

    for (nrows, dt_key), chans in groups.items():
        submatrix_count += 1
        sm_id = next_id
        next_id += 1
        dt = chans[0].sample_interval if chans[0].sample_interval else 1.0 / chans[0].sample_rate
        _add_instance(
            inst,
            "SubMatrix",
            Id=sm_id,
            Name=f"TimeSeries_{chans[0].sample_rate:.6g}Hz",
            NumberOfRows=nrows,
            Measurement=measurement_id,
        )

        # Synthetic time quantity and local column using implicit_linear.
        time_mq_id = next_id
        next_id += 1
        _add_instance(
            inst, "MeasurementQuantity",
            Id=time_mq_id, Name=f"Time_{submatrix_count}",
            Datatype="DT_DOUBLE", Rank=1, Measurement=measurement_id,
            Unit=unit_ids.get("s"),
        )
        time_lc_id = next_id
        next_id += 1
        _add_instance(
            inst, "LocalColumn",
            Id=time_lc_id,
            Name=f"Time_{submatrix_count}",
            Independent="true",
            SequenceRepresentation="implicit_linear",
            GenerationParameters=f"0 {float(dt):.17g}",
            GlobalFlag=15,
            SubMatrix=sm_id,
            MeasurementQuantity=time_mq_id,
        )

        for ch in chans:
            lc_id = next_id
            next_id += 1
            ec_id = next_id
            next_id += 1
            c = components[ch.name]

            _add_instance(
                inst, "LocalColumn",
                Id=lc_id, Name=ch.name, Independent="false",
                SequenceRepresentation="external_component",
                GlobalFlag=15,
                SubMatrix=sm_id,
                MeasurementQuantity=mq_ids[ch.name],
                ExternalComponents=ec_id,
            )
            _add_instance(
                inst, "ExternalComponent",
                Id=ec_id,
                FilenameUrl=c.filename,
                ValueType=c.value_type,
                StartOffset=c.start_offset,
                BlockSize=c.block_size,
                ValuesPerBlock=c.values_per_block,
                OrdinalNumber=1,
                LocalColumn=lc_id,
            )

    # raw:Tacho event timestamps: explicit external values, treated as independent time.
    for ch in event_infos:
        sm_id = next_id
        next_id += 1
        _add_instance(
            inst, "SubMatrix",
            Id=sm_id,
            Name=f"Events_{ch.name}",
            NumberOfRows=ch.sample_count,
            Measurement=measurement_id,
        )

        lc_id = next_id
        next_id += 1
        ec_id = next_id
        next_id += 1
        c = components[ch.name]

        _add_instance(
            inst, "LocalColumn",
            Id=lc_id,
            Name=ch.name,
            Independent="true",
            SequenceRepresentation="external_component",
            GlobalFlag=15,
            SubMatrix=sm_id,
            MeasurementQuantity=mq_ids[ch.name],
            ExternalComponents=ec_id,
        )
        _add_instance(
            inst, "ExternalComponent",
            Id=ec_id,
            FilenameUrl=c.filename,
            ValueType=c.value_type,
            StartOffset=c.start_offset,
            BlockSize=c.block_size,
            ValuesPerBlock=c.values_per_block,
            OrdinalNumber=1,
            LocalColumn=lc_id,
        )

    ElementTree(root).write(
        atfx_path,
        encoding="utf-8",
        xml_declaration=True,
    )

    report = {
        "source": sdf_path.name,
        "channels": len(infos),
        "submatrices": submatrix_count + len(event_infos),
        "dat_bytes": dat_path.stat().st_size,
        "expected_dat_bytes": sum(ch.sample_count * 4 for ch in infos),
        "base_model_version": 31,
        "payload_dtype": "little-endian float32",
        "java_required": False,
    }

    if report["dat_bytes"] != report["expected_dat_bytes"]:
        raise RuntimeError("DAT payload size verification failed.")

    # Basic XML round-trip validation
    import xml.etree.ElementTree as ET
    parsed = ET.parse(atfx_path)
    if parsed.getroot().tag != q("atfx_file"):
        raise RuntimeError("ATFX XML root validation failed.")

    return atfx_path, dat_path, report
