"""Own the export dialog and one cancellable background export at a time."""
from voxplane.i18n.messages import error_message
from voxplane.i18n import message as _msg
from voxplane.i18n.qt import translated_property as _TextProperty

from pathlib import Path
from threading import Event

import pydicom
from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, Signal, Slot

from voxplane.core.export_images import frame_image, full_range_review_image
from voxplane.core.pixel_codecs import decode_pixels
from voxplane.core.series_export import ExportCancelled, ExportError, ExportRequest, export_series, export_sources
from voxplane.ui.file_location import reveal_path
from voxplane.ui.reading_identity import format_study_date, source_code


class _ExportJob(QRunnable):
    def __init__(self, request, cancel, progress, completed):
        super().__init__()
        self.request, self.cancel = request, cancel
        self.progress, self.completed = progress, completed

    def run(self):
        try:
            result = export_series(self.request, cancel=self.cancel, progress=self.progress.emit)
            self.completed.emit(result, "")
        except ExportCancelled:
            self.completed.emit(None, _msg('text.0441'))
        except ExportError as exc:
            self.completed.emit(None, error_message(exc))
        except Exception:
            self.completed.emit(None, _msg('text.0442'))


class _PreviewJob(QRunnable):
    def __init__(self, source, index, full_range, generation, completed):
        super().__init__()
        self.source, self.index = source, index
        self.full_range = full_range
        self.generation, self.completed = generation, completed

    def run(self):
        path = self.source.instance.path
        try:
            header = pydicom.dcmread(path, stop_before_pixels=True)
            self.source.check_header(header)
            pixels = decode_pixels(path, header=header, index=self.index)
            if self.full_range:
                image = full_range_review_image(pixels, inverted=header.PhotometricInterpretation == "MONOCHROME1")
            else:
                image = frame_image(pixels, header, self.index)
            flag = str(header.get("BurnedInAnnotation", "")).upper()
            status = {"YES": "yes", "NO": "no"}.get(flag, "unknown")
        except Exception:
            image, status = None, "failed"
        self.completed.emit(self.generation, image, status)


class SeriesExportController(QObject):
    _i18n_message = Signal()
    _i18n_source = Signal()
    changed = Signal()
    dialogChanged = Signal()
    sourceChanged = Signal()
    previewChanged = Signal()
    _progress = Signal(int, int)
    _completed = Signal(object, object)
    _preview_completed = Signal(int, object, str)

    def __init__(self, catalog, settings, parent=None, *, image_provider=None, workspace=None):
        super().__init__(parent)
        self._catalog, self._settings = catalog, settings
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._workspace = workspace
        self._image_provider = image_provider
        self._preview_pool = QThreadPool(self)
        self._preview_pool.setMaxThreadCount(1)
        self._generation = 0
        self._preview_url = self._preview_status = ""
        self._preview_can_window = False
        self._preview_completed.connect(self._on_preview)
        self._cancel = Event()
        self._closing = False
        self._busy = False
        self._open = False
        self._locked = False
        self._series = None
        self._sources = ()
        self.dialogChanged.connect(self.sourceChanged)
        settings.sectionChanged.connect(self._settings_changed)
        if workspace is not None:
            workspace.tabPresentationChanged.connect(self.sourceChanged)
            workspace.tabsChanged.connect(self.sourceChanged)
            workspace.activeTabChanged.connect(self.sourceChanged)
        self._done = self._total = 0
        self._message = self._output = ""
        self._progress.connect(self._on_progress)
        self._completed.connect(self._on_completed)

    @Property(bool, notify=dialogChanged)
    def dialogOpen(self):
        return self._open

    @Property(bool, notify=dialogChanged)
    def anonymousLocked(self):
        return self._locked

    @Slot(str)
    def _settings_changed(self, section):
        if section == "privacy":
            self.sourceChanged.emit()

    @_TextProperty(str, notify=_i18n_source, notify_name='_i18n_source', source_notify='sourceChanged')
    def sourceDescription(self):
        series = self._series
        if not series:
            return ""
        if (self._settings.section("privacy")["hideIdentity"] or
                (self._workspace and self._workspace.hides_export_identity(series.series_instance_uid))):
            return _msg("export.hiddenSource", modality=series.modality or "—",
                        number=series.series_number if series.series_number is not None else "—",
                        source=source_code(series.series_instance_uid))
        return _msg("export.sourceSummary", patient=series.patient_name or "—", patientId=series.patient_id or "—",
                    date=format_study_date(series.study_date) or "—", study=series.study_description or "—",
                    number=series.series_number if series.series_number is not None else "—",
                    series=series.series_description or "—")

    @Property(int, notify=dialogChanged)
    def sourceFileCount(self):
        return len(self._sources)

    @Property(int, notify=dialogChanged)
    def selectedFrameCount(self):
        return sum(len(source.frames("png")) for source in self._sources)

    @Property(int, notify=dialogChanged)
    def sourceFrameCount(self):
        return sum(len(source.frames("dicom")) for source in self._sources)

    @Property(str, notify=previewChanged)
    def previewUrl(self):
        return self._preview_url

    @Property(str, notify=previewChanged)
    def previewStatus(self):
        return self._preview_status

    @Property(bool, notify=previewChanged)
    def previewCanWindow(self):
        return self._preview_can_window

    @Slot()
    def clearPreview(self):
        self._generation += 1
        self._preview_pool.clear()
        self._preview_url = self._preview_status = ""
        self._preview_can_window = False
        if self._image_provider is not None:
            self._image_provider.remove_image("series-export-preview")
        self.previewChanged.emit()

    @Slot(str, int)
    @Slot(str, int, bool)
    def previewFrame(self, file_format, index, full_range=False):
        self.clearPreview()
        if self._closing or not self._open or self._busy or file_format not in ("png", "dicom") or index < 0:
            return
        for source in self._sources:
            indices = source.frames(file_format)
            if index < len(indices):
                self._preview_can_window = (
                    file_format == "dicom"
                    and source.instance.photometric_interpretation in ("MONOCHROME1", "MONOCHROME2")
                )
                self._preview_status = "loading"
                self.previewChanged.emit()
                self._preview_pool.start(_PreviewJob(source, indices[index], full_range and self._preview_can_window,
                                                     self._generation, self._preview_completed))
                return
            index -= len(indices)

    @Slot(int, object, str)
    def _on_preview(self, generation, image, status):
        if self._closing or generation != self._generation:
            return
        if image is not None and self._image_provider is not None:
            self._image_provider.set_image("series-export-preview", image)
            self._preview_url = f"image://dicom/series-export-preview/{generation}"
        self._preview_status = status
        self.previewChanged.emit()

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @_TextProperty(str, notify=_i18n_message, notify_name='_i18n_message', source_notify='changed')
    def message(self):
        return self._message

    @Property(str, notify=changed)
    def outputDirectory(self):
        return self._output

    @Property(int, notify=changed)
    def completedCount(self):
        return self._done

    @Property(int, notify=changed)
    def totalCount(self):
        return self._total

    @Slot(str, bool)
    def openSeries(self, uid, anonymous_locked=False):
        if self._busy or self._closing:
            return
        self.clearPreview()
        self._series = self._catalog.get_series(uid)
        self._sources = export_sources(self._series.instances) if self._series else ()
        self._locked = anonymous_locked
        self._message = "" if self._sources else _msg('text.0140')
        self._output = ""
        self._done = self._total = 0
        self._open = True
        self.dialogChanged.emit()
        self.changed.emit()

    @Slot()
    def closeDialog(self):
        if not self._busy:
            self._open = False
            self.clearPreview()
            self._series = None
            self._sources = ()
            self.dialogChanged.emit()

    @Slot(str, bool)
    def startExport(self, file_format, anonymous):
        if self._busy or self._closing or not self._open:
            return
        if file_format not in ("png", "dicom") or not self._sources:
            self._message = _msg('text.0443')
            self.changed.emit()
            return
        request = ExportRequest.from_sources(self._sources, Path(self._settings.exportDirectory), file_format,
                                             self._locked or anonymous)
        self._cancel = Event()
        self._busy = True
        self._output = ""
        self._message = _msg('text.0444')
        self._done = self._total = 0
        self.changed.emit()
        self._pool.start(_ExportJob(request, self._cancel, self._progress, self._completed))

    @Slot()
    def cancelExport(self):
        if self._busy:
            self._cancel.set()
            self._message = _msg('text.0445')
            self.changed.emit()

    @Slot(int, int)
    def _on_progress(self, done, total):
        if self._closing:
            return
        self._done, self._total = done, total
        if not self._cancel.is_set():
            self._message = _msg('text.0446', value1=done, value2=total)
        self.changed.emit()

    @Slot(object, object)
    def _on_completed(self, result, error):
        self._busy = False
        if self._closing:
            return
        self._output = str(result.directory) if result else ""
        self._message = (_msg("export.written", format=result.format.upper(), files=result.file_count,
                              frames=result.frame_count) if result else error)
        self.changed.emit()

    @Slot()
    def openOutputDirectory(self):
        if self._output and not reveal_path(self._output):
            self._message = _msg('text.0448')
            self.changed.emit()

    def shutdown(self):
        self._closing = True
        self._cancel.set()
        self.clearPreview()
        self._preview_pool.waitForDone()
        self._pool.waitForDone()
