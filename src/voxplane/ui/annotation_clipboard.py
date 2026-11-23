"""Versioned, bounded geometry clipboard; no patient identifiers or cached metrics."""

import json
import math
from dataclasses import dataclass
from uuid import uuid4

import numpy as np

from voxplane.core.compare import plane_basis
from voxplane.model import ImageGeometryMeta

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QColor, QGuiApplication

MIME_TYPE = "application/x-voxplane-annotation+json"
LEGACY_MIME_TYPE = "application/x-voxenra-annotation+json"
MAX_BYTES = 256 * 1024


ROI_KINDS = {"rect", "ellipse", "freehand"}
_ORIGIN_MIME = "application/x-voxplane-roi-copy-token"
_local_copy = None


@dataclass(frozen=True)
class RoiCopyOrigin:
    series_uid: str
    phase: tuple | None
    reference_uid: str
    geometry: ImageGeometryMeta

    def mismatch(self, target):
        if self.phase is None or self.phase != target.phase or self.series_uid != target.series_uid:
            return "roiPaste.seriesPhase"
        if self.reference_uid != target.reference_uid:
            return "roiPaste.geometry"
        source, dest = self.geometry, target.geometry
        if (source.rows, source.columns) != (dest.rows, dest.columns):
            return "roiPaste.geometry"
        bases = [plane_basis((g.image_position_patient, g.image_orientation_patient,
                              g.pixel_spacing, g.rows, g.columns)) for g in (source, dest)]
        if any(basis is None for basis in bases):
            return "roiPaste.geometry"
        origin, col, row, _ = bases[0]
        other_origin, other_col, other_row, _ = bases[1]
        # Bound cumulative in-plane drift at the far image edge to 0.001 mm.
        delta = other_origin - origin
        if (any(abs(delta @ axis) / np.linalg.norm(axis) > 0.001 for axis in (col, row))
                or np.linalg.norm(col - other_col) * source.columns > 0.001
                or np.linalg.norm(row - other_row) * source.rows > 0.001):
            return "roiPaste.geometry"
        return ""


def write_annotation(payload, *, origin=None):
    global _local_copy
    data = json.dumps(dict(payload, version=1), allow_nan=False).encode("utf-8")
    mime = QMimeData()
    mime.setData(MIME_TYPE, data)
    _local_copy = None
    if origin is not None:
        token = uuid4().bytes
        _local_copy = (token, data, origin)
        mime.setData(_ORIGIN_MIME, token)
    QGuiApplication.clipboard().setMimeData(mime)


def read_annotation():
    return read_annotation_with_origin()[0]


def read_annotation_with_origin():
    mime = QGuiApplication.clipboard().mimeData()
    if mime is None:
        return None, None
    kind = next((name for name in (MIME_TYPE, LEGACY_MIME_TYPE) if mime.hasFormat(name)), None)
    if kind is None:
        return None, None
    raw = bytes(mime.data(kind))
    if len(raw) > MAX_BYTES:
        return None, None
    origin = None
    if _local_copy is not None:
        token, data, source = _local_copy
        if raw == data and mime.data(_ORIGIN_MIME) == token:
            origin = source
    return _parse_annotation(raw), origin


def _parse_annotation(raw):
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        kind = payload.get("kind")
        if kind not in ("length", "angle", "rect", "ellipse", "curve", "freehand", "arrow", "text"):
            return None
        points = payload.get("points")
        if not isinstance(points, list) or (not 3 <= len(points) <= 4096 if kind in ("freehand", "curve") else len(points) != (3 if kind == "angle" else 2)):
            return None
        if any(
            not isinstance(p, list)
            or len(p) != 2
            or any(
                type(v) not in (int, float) or abs(v) > 1e7 or not math.isfinite(v)
                for v in p
            )
            for p in points
        ):
            return None
        if "smooth" in payload and not isinstance(payload["smooth"], bool):
            return None
        if kind == "text":
            if (
                not isinstance(payload.get("text"), str)
                or len(payload["text"]) > 200
                or not isinstance(payload.get("color"), str)
                or not QColor(payload["color"]).isValid()
                or type(payload.get("fontSize")) is not int
                or not 10 <= payload["fontSize"] <= 48
            ):
                return None
        return payload
    except (ValueError, TypeError, RecursionError):
        return None
