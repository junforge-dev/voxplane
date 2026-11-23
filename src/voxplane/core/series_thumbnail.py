"""Decode one frame of a representative instance into a small, detached QImage."""

from pathlib import Path

import numpy as np
import pydicom
from pydicom.pixels import apply_modality_lut
from voxplane.core.pixel_codecs import decode_pixels
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from voxplane.core.dicom_loader import DicomLoader, source_voi
from voxplane.model import WindowLevel


def read_series_thumbnail(path: Path, frame_index=None) -> QImage:
    metadata = pydicom.dcmread(path, stop_before_pixels=True)
    pixels = decode_pixels(path, index=frame_index or 0, header=metadata)
    if frame_index is not None:
        from voxplane.core.export_images import frame_image
        image = frame_image(pixels, metadata, frame_index)
        return image.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    photometric = str(getattr(metadata, "PhotometricInterpretation", ""))
    from voxplane.core.color_image import is_color, color_pixels
    if is_color(metadata):
        pixels = color_pixels(pixels, metadata)
    if pixels.ndim == 2 and str(getattr(metadata, "Modality", "")).upper() == "MR":
        pixels = DicomLoader().load_dataset(metadata, None, False,
                    modality_pixels=DicomLoader.rescale_pixels(pixels, metadata)).image
        image_format = QImage.Format_Grayscale8
    elif pixels.ndim == 2:
        pixels = np.asarray(apply_modality_lut(pixels, metadata), dtype=np.float32)
        window, function = source_voi(metadata)
        if window is None:
            finite = pixels[np.isfinite(pixels)]
            low, high = np.percentile(finite, [1, 99]) if finite.size else (0.0, 1.0)
            window = WindowLevel(float((low + high) / 2), max(1.0, float(high - low)))
        pixels = DicomLoader.apply_window(
            pixels, window, photometric == "MONOCHROME1",
            minimum_width=min(1.0, window.width), voi_lut_function=function,
        )
        image_format = QImage.Format_Grayscale8
    elif pixels.ndim == 3 and pixels.shape[2] in (3, 4):
        if pixels.dtype != np.uint8:
            maximum = float(np.iinfo(pixels.dtype).max) if photometric == "PALETTE COLOR" else (
                2 ** int(getattr(metadata, "BitsStored", 8)) - 1)
            pixels = np.clip(pixels.astype(np.float32) * 255 / max(1, maximum), 0, 255).astype(np.uint8)
        image_format = QImage.Format_RGB888 if pixels.shape[2] == 3 else QImage.Format_RGBA8888
    else:
        raise ValueError("Unsupported thumbnail pixel shape")
    pixels = np.ascontiguousarray(pixels)
    height, width = pixels.shape[:2]
    image = QImage(pixels.data, width, height, pixels.strides[0], image_format).copy()
    return image.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)
