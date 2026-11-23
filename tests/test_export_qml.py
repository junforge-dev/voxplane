from dataclasses import replace
from pathlib import Path

import numpy as np
import pydicom
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from voxplane.model import DicomFolderScanSnapshot
from test_dicom_tags import qt_app, make_series, wait_until
from test_pacs_qml import scene
from test_series_sidebar import right_click
from test_tag_qml import find, click, type_text


def load_series(app, tmp_path):
    series = make_series(tmp_path, 2)
    snapshot = DicomFolderScanSnapshot(tmp_path, 2, 2, 0, [series])
    app.panelController.update_series_session(snapshot)
    app.panelController._update_series_record(snapshot)
    app.panelController.selectSeries(series.series_instance_uid)
    return series


def test_export_dialog_defaults_formats_and_right_click_whole_series(scene, tmp_path, monkeypatch):
    window, app, warnings = scene
    assert not find(window, "sidebarExport").isEnabled()
    series = load_series(app, tmp_path)
    app.settingsController.setValue("export", "directory", str(tmp_path / "exports"))
    click(window, find(window, "sidebarExport"))
    assert app.seriesExportController.dialogOpen
    checkbox = find(window, "exportAnonymous")
    assert checkbox.property("checked") and checkbox.isEnabled()
    click(window, checkbox)
    assert not checkbox.property("checked")
    click(window, find(window, "startExport"))
    wait_until(lambda: not app.seriesExportController.busy)
    output = Path(app.seriesExportController.outputDirectory)
    assert len(list(output.glob("*.dcm"))) == 2
    revealed = []
    monkeypatch.setattr('voxplane.ui.controller.series_export_controller.reveal_path', lambda path: revealed.append(path) or True)
    QTest.qWait(30)
    link = find(window, 'exportOutputDirectory')
    assert link.property('text') == str(output)
    click(window, link)
    assert revealed == [str(output)]
    assert pydicom.dcmread(next(output.iterdir())).PatientName != "ANONYMOUS"
    click(window, find(window, "cancelExport"))
    QTest.qWait(100)
    right_click(window, find(window, "series-" + series.series_instance_uid))
    click(window, find(window, "seriesContextAction-deidentify"))
    assert app.seriesExportController.dialogOpen and app.seriesExportController.anonymousLocked
    assert checkbox.property("checked") and not checkbox.isEnabled()
    combo = find(window, "exportFormat")
    click(window, combo)
    QTest.keyClick(window, Qt.Key_End)
    QTest.keyClick(window, Qt.Key_Return)
    assert combo.property("currentIndex") == 1
    window.resize(1000, 600)
    QTest.qWait(80)
    for name in ("exportFormat", "exportAnonymous", "cancelExport", "startExport"):
        item = find(window, name)
        top = item.mapToScene(QPointF(0, 0))
        bottom = item.mapToScene(QPointF(item.width(), item.height()))
        assert 0 <= top.x() < bottom.x() <= window.width()
        assert 0 <= top.y() < bottom.y() <= window.height()
    assert window.grabWindow().save(str(tmp_path / "dicom-export-dialog.png"))
    click(window, find(window, "startExport"))
    wait_until(lambda: not app.seriesExportController.busy)
    assert app.seriesExportController.outputDirectory
    output = Path(app.seriesExportController.outputDirectory)
    assert len(list(output.glob("*.png"))) == 4
    assert app.seriesExportController.completedCount == app.seriesExportController.totalCount == 4
    QTest.qWait(50)
    assert window.grabWindow().save(str(tmp_path / "dicom-export-complete.png"))
    click(window, find(window, "cancelExport"))
    QTest.qWait(100)
    click(window, find(window, "sidebarExport"))
    assert checkbox.property("checked") and checkbox.isEnabled()
    click(window, find(window, 'seriesExportManualLink'))
    wait_until(lambda: app.workspaceController.activeTabType == 'manual')
    assert not app.seriesExportController.dialogOpen
    assert app.workspaceController.manualController.chapterId == 'export'
    assert not warnings, warnings


def test_export_settings_reset_preserves_shared_destination(scene, tmp_path):
    window, app, warnings = scene
    app.workspaceController.openSettings()
    QTest.qWait(50)
    click(window, find(window, "settingsCategory-export"))
    field = find(window, "exportDirectoryField")
    path = str(tmp_path / "new-export-location")
    type_text(window, field, path)
    QTest.keyClick(window, Qt.Key_Tab)
    assert app.settingsController.exportDirectory == path
    assert field.property("text") == path
    window.resize(1000, 600)
    QTest.qWait(50)
    assert window.grabWindow().save(str(tmp_path / "dicom-export-settings.png"))
    app.settingsController.setExportOptions("screenshot", {"format": "jpeg"})
    click(window, find(window, "resetDisplaySettings"))
    click(window, find(window, 'confirmSettingsReset'))
    assert field.property("text") == path
    assert app.settingsController.exportDirectory == path
    assert app.settingsController.section("screenshot")["format"] == "png"
    assert not warnings, warnings


def test_selected_export_identity_is_frozen_and_privacy_masks_source(scene, tmp_path):
    from test_mr import write_mr_series

    window, app, warnings = scene
    first = write_mr_series(tmp_path / "first", count=3)

    def patient_b(ds, _):
        ds.PatientName, ds.PatientID = "DEMO^B", "B-123"
        ds.StudyDate, ds.StudyDescription = "20260109", "Study B"
        ds.SeriesNumber, ds.SeriesDescription = 17, "Series B"

    selected = write_mr_series(tmp_path / "second", count=3, change=patient_b)
    snapshot = DicomFolderScanSnapshot(tmp_path, 6, 6, 0, [first, selected])
    app.panelController.update_series_session(snapshot)
    app.panelController._update_series_record(snapshot)
    app.workspaceController.createTab(first.series_instance_uid, first.series_description, "2d")
    app.panelController.selectSeries(selected.series_instance_uid)
    app.settingsController.setValue("export", "directory", str(tmp_path / "out"))
    click(window, find(window, "sidebarExport"))
    summary = find(window, "exportSourceSummary")
    for expected in ("DEMO^B", "B-123", "2026/01/09", "Study B", "17", "Series B"):
        assert expected in summary.property("text")
    app.settingsController.setValue("privacy", "hideIdentity", True)
    hidden = summary.property("text")
    assert "MR" in hidden and "17" in hidden and "身份已隐藏" in hidden
    assert all(field not in hidden for field in ("DEMO^B", "B-123", "2026", "Study B", "Series B"))
    app.settingsController.setValue("privacy", "hideIdentity", False)
    app.workspaceController.activeViewport.setViewportSetting("hide-sensitive-info", True)
    assert summary.property("text") == hidden
    app.workspaceController.activeViewport.setViewportSetting("hide-sensitive-info", False)
    assert "B-123" in summary.property("text")

    # An incremental import can replace a record under the same catalog key
    # while a confirmation is open. Both the displayed and written series stay B.
    app._series_catalog.update(replace(snapshot, series=[replace(first, series_instance_uid=selected.series_instance_uid)]))
    click(window, find(window, "exportAnonymous"))
    click(window, find(window, "startExport"))
    export = app.seriesExportController
    wait_until(lambda: not export.busy)
    files = list(Path(export.outputDirectory).glob("*.dcm"))
    assert len(files) == 3
    assert {pydicom.dcmread(path).PatientID for path in files} == {"B-123"}
    assert not warnings, warnings


def preview_pixel(window, frame, x, y):
    """Read the displayed centre of a source pixel in an 8 by 8 preview."""
    width, height = frame.property("paintedWidth"), frame.property("paintedHeight")
    point = frame.mapToScene(QPointF((frame.width() - width) / 2 + (x + .5) * width / 8,
                                     (frame.height() - height) / 2 + (y + .5) * height / 8))
    shot = window.grabWindow()
    scale = shot.devicePixelRatio()
    return shot.pixelColor(round(point.x() * scale), round(point.y() * scale)).red()


@pytest.mark.parametrize("flag", [None, "NO", "YES"])
def test_review_and_export_selected_enhanced_frames(scene, tmp_path, flag):
    from test_enhanced_ct import enhanced_ct_dataset, save_and_scan

    window, app, warnings = scene
    ds = enhanced_ct_dataset()
    raw = np.zeros((6, 8, 8), dtype=np.int16)
    raw[:, 1:5, 1] = 4000  # A burned-in "ID", with a different marker on each frame.
    raw[:, 1:5, 3] = 4000
    raw[:, (1, 4), 4] = 4000
    raw[:, 2:4, 5] = 4000
    for index in range(6):
        raw[index, 6, index] = 4000
    ds.PixelData = raw.tobytes()
    if flag:
        ds.BurnedInAnnotation = flag
    _, snapshot = save_and_scan(tmp_path, ds)
    selected = next(series for series in snapshot.series if min(i.frame_index for i in series.instances) == 3)
    app.panelController.update_series_session(snapshot)
    app.panelController._update_series_record(snapshot)
    app.panelController.selectSeries(selected.series_instance_uid)
    app.settingsController.setValue("export", "directory", str(tmp_path / "out"))
    export = app.seriesExportController
    click(window, find(window, "sidebarExport"))
    assert selected.series_description in find(window, "exportSourceSummary").property("text")
    assert find(window, "exportSelectionCount").property("text") == "当前组 3 帧，来自 1 个源文件"
    assert "像素" in find(window, "exportPixelNotice").property("text")

    for file_format, count, first in (("dicom", 6, 0), ("png", 3, 3)):
        if file_format == "png":
            combo = find(window, "exportFormat")
            click(window, combo)
            QTest.keyClick(window, Qt.Key_End)
            QTest.keyClick(window, Qt.Key_Return)
        output_label = find(window, "exportOutputCount").property("text")
        assert ("1 个完整文件，共 6 帧" if file_format == "dicom" else "3 个文件，每帧一个") in output_label
        assert find(window, "exportAnonymous").property("text") == (
            "匿名化 DICOM 标签" if file_format == "dicom" else "省略 PNG 身份元数据")
        click(window, find(window, "reviewExportFrames"))
        frame = find(window, "exportPreviewImage")
        wait_until(lambda: frame.property("ready"))
        assert {None: "未明确标记", "YES": "YES", "NO": "NO"}[flag] in find(window, "exportPreviewFlag").property("text")
        assert preview_pixel(window, frame, 1, 2) > 245
        assert preview_pixel(window, frame, first, 6) > 245
        assert preview_pixel(window, frame, first + 1, 6) < 10
        click(window, find(window, "exportPreviewNext"))
        wait_until(lambda: frame.property("ready") and preview_pixel(window, frame, first + 1, 6) > 245)
        assert preview_pixel(window, frame, first, 6) < 10
        QTest.keyClick(window, Qt.Key_Escape)
        wait_until(lambda: not export.previewUrl)
        assert export.dialogOpen and not export.busy
        click(window, find(window, "startExport"))
        wait_until(lambda: not export.busy)
        if flag == "YES":
            assert "烧录" in export.message and not export.outputDirectory
            assert not (tmp_path / "out").exists()
            continue
        output = Path(export.outputDirectory)
        files = sorted(output.iterdir())
        assert len(files) == export.completedCount == export.totalCount == (1 if file_format == "dicom" else count)
        assert find(window, "exportMessage").property("text") == f"{file_format.upper()} 导出完成，共 {len(files)} 个文件、{count} 帧。"
        if file_format == "dicom":
            saved = pydicom.dcmread(files[0])
            assert saved.PatientName == "ANONYMOUS" and saved.NumberOfFrames == count
            np.testing.assert_array_equal(saved.pixel_array, raw)
        else:
            for index, file in enumerate(files, first):
                image = QImage(str(file))
                assert file.name.endswith(f"frame-{index + 1:06d}.png")
                assert image.pixelColor(index, 6).red() == 255
                assert "PatientID" not in image.textKeys()
    QTest.keyClick(window, Qt.Key_Escape)
    assert not export.dialogOpen and not warnings, warnings


@pytest.mark.parametrize("enhanced,photometric", [
    (False, "MONOCHROME2"), (False, "MONOCHROME1"), (True, "MONOCHROME2"),
])
def test_dicom_review_full_range_reveals_clipped_mark(scene, tmp_path, enhanced, photometric):
    from test_dicom_tags import make_dicom
    from test_enhanced_ct import enhanced_ct_dataset, save_and_scan

    window, app, warnings = scene
    ds = enhanced_ct_dataset() if enhanced else make_dicom(tmp_path / "enhanced-ct.dcm")
    ds.Rows = ds.Columns = 8
    ds.PhotometricInterpretation = photometric
    dtype = np.int16 if enhanced else np.uint16
    raw = np.full((int(ds.NumberOfFrames), 8, 8), 5, dtype=dtype)
    raw[0, 1:6, 2] = 230  # An "L" wholly below the source window.
    raw[0, 5, 2:5] = 230
    ds.PixelData = raw.tobytes()
    _, snapshot = save_and_scan(tmp_path, ds)
    selected = next(s for s in snapshot.series if any(i.frame_index in (None, 0) for i in s.instances))
    app.panelController.update_series_session(snapshot)
    app.panelController._update_series_record(snapshot)
    app.panelController.selectSeries(selected.series_instance_uid)
    app.settingsController.setValue("export", "directory", str(tmp_path / "out"))
    controller = app.seriesExportController
    click(window, find(window, "sidebarExport"))
    click(window, find(window, "reviewExportFrames"))
    frame = find(window, "exportPreviewImage")
    wait_until(lambda: frame.property("ready"))
    background = 255 if photometric == "MONOCHROME1" else 0
    assert preview_pixel(window, frame, 2, 3) == preview_pixel(window, frame, 3, 3) == background
    toggle = find(window, "exportPreviewFullRange")
    assert not toggle.property("checked")
    click(window, toggle)
    wait_until(lambda: frame.property("ready") and abs(preview_pixel(window, frame, 2, 3) - background) > 245)
    assert abs(preview_pixel(window, frame, 3, 3) - background) < 10
    click(window, find(window, "exportPreviewNext"))
    wait_until(lambda: frame.property("ready"))
    assert toggle.property("checked") and preview_pixel(window, frame, 2, 3) == preview_pixel(window, frame, 3, 3)
    click(window, find(window, "exportPreviewPrevious"))
    wait_until(lambda: frame.property("ready") and abs(preview_pixel(window, frame, 2, 3) - background) > 245)
    click(window, toggle)
    wait_until(lambda: frame.property("ready") and preview_pixel(window, frame, 2, 3) == background)
    QTest.keyClick(window, Qt.Key_Escape)
    click(window, find(window, "startExport"))
    wait_until(lambda: not controller.busy)
    saved = pydicom.dcmread(next(Path(controller.outputDirectory).glob("*.dcm")))
    np.testing.assert_array_equal(saved.pixel_array, raw)

    combo = find(window, "exportFormat")
    click(window, combo)
    QTest.keyClick(window, Qt.Key_End)
    QTest.keyClick(window, Qt.Key_Return)
    click(window, find(window, "reviewExportFrames"))
    wait_until(lambda: frame.property("ready"))
    assert not toggle.isVisible()
    assert preview_pixel(window, frame, 2, 3) == preview_pixel(window, frame, 3, 3) == background
    QTest.keyClick(window, Qt.Key_Escape)
    click(window, find(window, "startExport"))
    wait_until(lambda: not controller.busy)
    png = QImage(str(next(Path(controller.outputDirectory).glob("*frame-000001.png"))))
    assert png.pixelColor(2, 3).red() == png.pixelColor(3, 3).red() == background
    assert not warnings, warnings
