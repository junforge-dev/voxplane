"""Transactional series export. Source files are always read-only."""
from voxplane.i18n.messages import error_message
from voxplane.i18n import message as _msg

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil
import tempfile
from threading import Event
import uuid

import pydicom
from voxplane.core.pixel_codecs import iter_decoded_pixels, PixelDecodeError

from voxplane.core.dicom_anonymizer import Anonymizer, check_pixel_identity
from voxplane.core.export_images import frame_image
from voxplane.model import DicomInstanceMeta


class ExportCancelled(Exception):
    pass


class ExportError(ValueError):
    """User-facing errors must not include source paths or patient metadata."""


@dataclass(frozen=True)
class ExportSource:
    instance: DicomInstanceMeta
    selected: tuple[int, ...] | None

    def frames(self, file_format):
        if file_format == "png" and self.selected is not None:
            return self.selected
        return range(self.instance.number_of_frames)

    def check_header(self, header):
        instance = self.instance
        identity = (("SOPInstanceUID", instance.sop_instance_uid), ("StudyInstanceUID", instance.study_instance_uid),
                    ("SeriesInstanceUID", instance.series_instance_uid), ("PatientID", instance.patient_id),
                    ("PatientName", instance.patient_name), ("StudyDate", instance.study_date),
                    ("SeriesDescription", instance.series_description), ("StudyDescription", instance.study_description))
        if (any(str(header.get(key) or "") != value for key, value in identity)
                or max(1, int(header.get("NumberOfFrames", 1))) != instance.number_of_frames):
            raise ExportError(_msg("export.sourceChanged"))


def export_sources(instances):
    """Group the selected browser frames by physical file in export order."""
    files = {}
    indices = {}
    for instance in instances:
        files.setdefault(instance.path, instance)
        if instance.frame_index is not None:
            indices.setdefault(instance.path, set()).add(instance.frame_index)
    return tuple(ExportSource(instance, tuple(sorted(indices[path])) if path in indices else None)
                 for path, instance in files.items())


@dataclass(frozen=True)
class ExportRequest:
    paths: tuple[Path, ...]
    directory: Path
    format: str = "dicom"
    anonymous: bool = True
    frames: tuple[tuple[Path, int], ...] = ()
    sources: tuple[ExportSource, ...] = ()

    @classmethod
    def from_sources(cls, sources, directory, file_format, anonymous):
        paths = tuple(source.instance.path for source in sources)
        frames = tuple((source.instance.path, index) for source in sources for index in source.selected or ())
        return cls(paths, directory, file_format, anonymous, frames, sources)


@dataclass(frozen=True)
class ExportResult:
    directory: Path
    file_count: int
    frame_count: int
    format: str


def export_series(request: ExportRequest, *, cancel=None, progress=None):
    cancel = cancel or Event()
    progress = progress or (lambda completed, total: None)

    def check_cancelled():
        if cancel.is_set():
            raise ExportCancelled()

    if request.format not in ("png", "dicom"):
        raise ExportError(_msg('text.0139'))
    paths = tuple(dict.fromkeys(Path(path) for path in request.paths))
    if not paths:
        raise ExportError(_msg('text.0140'))
    selections = {}
    for path, index in request.frames:
        if Path(path) not in paths or type(index) is not int or index < 0:
            raise ExportError(_msg('text.0443'))
        selections.setdefault(Path(path), set()).add(index)
    root = Path(request.directory).expanduser()
    if not root.is_absolute():
        raise ExportError(_msg('text.0048'))
    stage = None
    try:
        # Validate all inputs before publishing any output, including late-series
        # burned-in annotations and multi-frame image counts.
        frame_counts = []
        for index, path in enumerate(paths, 1):
            check_cancelled()
            try:
                header = pydicom.dcmread(path, stop_before_pixels=True)
                if request.sources:
                    request.sources[index - 1].check_header(header)
                if request.anonymous:
                    check_pixel_identity(header)
                count = max(1, int(getattr(header, "NumberOfFrames", 1)))
                if path in selections and max(selections[path]) >= count: raise ValueError("Invalid frame index")
                frame_counts.append(len(selections[path]) if request.format == "png" and path in selections else count)
            except ExportError:
                raise
            except ValueError as exc:
                if error_message(exc).startswith(_msg('text.0141')):
                    raise ExportError(error_message(exc)) from exc
                raise ExportError(_msg('text.0142', value1=index)) from exc
            except Exception as exc:
                raise ExportError(_msg('text.0142', value1=index)) from exc
        total = sum(frame_counts) if request.format == "png" else len(paths)
        progress(0, total)
        root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".export-", dir=root))
        anonymizer = Anonymizer()
        completed = 0
        for index, path in enumerate(paths, 1):
            check_cancelled()
            try:
                if request.format == "dicom":
                    output = stage / f"instance-{index:06d}.dcm"
                    if request.anonymous:
                        dataset = pydicom.dcmread(path)
                        anonymizer.apply(dataset)
                        dataset.save_as(output, enforce_file_format=True)
                    else:
                        shutil.copyfile(path, output)
                    completed += 1
                    progress(completed, total)
                else:
                    dataset = pydicom.dcmread(path, stop_before_pixels=True)
                    count = 0
                    indices = sorted(selections[path]) if path in selections else list(range(frame_counts[index - 1]))
                    for frame_index, pixels in zip(indices, iter_decoded_pixels(path, header=dataset, indices=indices)):
                        check_cancelled()
                        image = frame_image(pixels, dataset, frame_index)
                        if not request.anonymous:
                            for key in ("PatientName", "PatientID", "StudyInstanceUID", "SeriesInstanceUID"):
                                image.setText(key, str(getattr(dataset, key, "")))
                        output = stage / f"instance-{index:06d}-frame-{frame_index + 1:06d}.png"
                        if not image.save(str(output), "PNG"):
                            raise ExportError(_msg('text.0143'))
                        completed += 1
                        count += 1
                        progress(completed, total)
                    if count != frame_counts[index - 1]:
                        raise ExportError(_msg('text.0144', value1=index))
            except PixelDecodeError as exc:
                raise ExportError(error_message(exc)) from exc
            except (ExportCancelled, ExportError):
                raise
            except Exception as exc:
                raise ExportError(_msg('text.0145', value1=index)) from exc
        check_cancelled()
        name = "series-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
        destination = root / name
        stage.rename(destination)
        stage = None
        return ExportResult(destination, completed, sum(frame_counts), request.format)
    except (ExportCancelled, ExportError):
        raise
    except OSError as exc:
        raise ExportError(_msg('text.0146')) from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage)
