"""A pasted contour keeps its acquisition coordinates and samples the displayed slice."""
from dataclasses import replace

import numpy as np
import pydicom
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtTest import QTest

from test_annotation_clipboard import draw
from test_measurement_qml import qt_app, viewport, _click_points, _scene
from test_series_sidebar import sidebar_scene
from test_sidebar_keyboard_access import all_controls_keyboard
from test_viewport_transform import _render_result
from voxplane.model import ImagePoint, MeasurementKind, PixelSpacing
from voxplane.ui.annotation_clipboard import read_annotation, write_annotation


@pytest.fixture(autouse=True)
def clear_clipboard(qt_app):
    yield
    QGuiApplication.clipboard().clear()


@pytest.mark.parametrize("kind", ["rect", "ellipse", "freehand"])
def test_in_place_samples_target_and_preserves_control_points(viewport, kind):
    window, view, pixels, warnings = viewport
    measure = view.measurementController
    if kind == "freehand":
        points = [ImagePoint(10.5, 20), ImagePoint(32, 20), ImagePoint(31, 45), ImagePoint(10, 43)]
        measure.paste_points(points, view._measurement_context(0, 0, kind=MeasurementKind.FREEHAND), smooth=True)
    else:
        draw(window, view, pixels, kind)
    source = measure.committed_measurements[0]
    assert view.copySelectedAnnotation()
    frame = view._frame_meta
    geometry = replace(frame.geometry, image_position_patient=(0, 0, 5))
    result = replace(_render_result(view), frame_meta=replace(frame, slice_index=1, geometry=geometry),
                     modality_pixel=np.full((256, 256), 37.0))
    view.handleRenderResult(result)
    view.applyTransformAction("rotate:cw90")
    view.applyTransformAction("rotate:mirror-h")
    view.apply_zoom(2)
    view.apply_pan(17, -10)
    assert hasattr(view, "pasteRoiInPlace")
    assert view.pasteRoiInPlace()
    target = measure.committed_measurements[-1]
    assert target.measurement_id != source.measurement_id and target.slice_index == 1
    assert target.points == source.points and target.smooth == source.smooth
    assert (target.metrics.mean, target.metrics.std, target.metrics.minimum, target.metrics.maximum) == (37, 0, 37, 37)
    assert target.metrics.pixel_count == source.metrics.pixel_count > 0
    assert target.metrics.area_mm2 == source.metrics.area_mm2
    assert not warnings


@pytest.mark.parametrize("change", ["shift", "rotate", "spacing", "size", "no_position", "no_pixels",
                                   "other_phase", "other_series", "legacy", "unloaded"])
def test_unavailable_in_place_preserves_records(viewport, change):
    window, view, pixels, _ = viewport
    draw(window, view, pixels, "rect")
    assert view.copySelectedAnnotation()
    frame = view._frame_meta
    updates = {"shift": dict(image_position_patient=(1, 0, 5)),
               "rotate": dict(image_orientation_patient=(0, 1, 0, -1, 0, 0)),
               "spacing": dict(pixel_spacing=PixelSpacing(1, 1.2)),
               "size": dict(columns=255), "no_position": dict(image_position_patient=None)}
    if change in updates:
        view._frame_meta = replace(frame, geometry=replace(frame.geometry, **updates[change]))
    elif change == "no_pixels":
        view._modality_pixel = None
    elif change == "other_phase":
        view._frame_meta = replace(frame, stack_phase=(("TemporalPositionIndex", 2),))
    elif change == "other_series":
        view.viewport_config = replace(view.viewport_config, series_uid="another-series")
    elif change == "legacy":
        write_annotation(read_annotation())
    elif change == "unloaded":
        view._state = replace(view._state, slice_index=5)
    assert view.roiPasteIssue()
    assert not view.pasteRoiInPlace()
    assert len(view.measurementController.committed_measurements) == 1


def test_right_click_pastes_in_place_and_single_undo_restores_source(sidebar_scene, tmp_path):
    from test_input_settings_qml import reading_scene
    from test_tag_qml import find, click
    from test_dicom_tags import wait_until
    window, app, tab = reading_scene(sidebar_scene)
    view = tab.activeViewport
    pixels = find(window, "dicomPixelLayer")
    view._tool_controller.selectInteraction("measure:rect")
    _click_points(window, _scene(pixels, 21, 37), _scene(pixels, 44, 58))
    original = view.measurementController.committed_measurements[0]
    QTest.keySequence(window, QKeySequence(QKeySequence.Copy))
    view.setSliceIndex(1)
    wait_until(lambda: view._frame_meta.slice_index == 1 and not view.render_pending)
    QTest.mouseClick(window, Qt.RightButton, pos=_scene(pixels, 43, 42))
    action = find(window, "pasteRoiInPlaceAction")
    assert action.isEnabled()
    assert window.grabWindow().save(str(tmp_path / "roi-paste-menu.png"))
    click(window, action)
    copy = view.measurementController.committed_measurements[-1]
    assert copy.slice_index == 1 and copy.points == original.points
    assert copy.metrics.mean != original.metrics.mean
    assert find(window, "roiMetricCard").isVisible()
    assert window.grabWindow().save(str(tmp_path / "roi-paste-result.png"))
    QTest.keySequence(window, QKeySequence(QKeySequence.Undo))
    wait_until(lambda: len(view.measurementController.committed_measurements) == 1)
    assert view.measurementController.committed_measurements[0] == original
    assert not sidebar_scene[3]


def test_menu_explains_replaced_clipboard_while_open(sidebar_scene):
    from test_input_settings_qml import reading_scene
    from test_tag_qml import find
    from test_dicom_tags import wait_until
    window, _, tab = reading_scene(sidebar_scene)
    pixels = find(window, "dicomPixelLayer")
    view = tab.activeViewport
    view._tool_controller.selectInteraction("measure:rect")
    _click_points(window, _scene(pixels, 12, 14), _scene(pixels, 36, 39))
    assert view.copySelectedAnnotation()
    QTest.mouseClick(window, Qt.RightButton, pos=_scene(pixels, 50, 50))
    action = find(window, "pasteRoiInPlaceAction")
    assert action.isEnabled()
    QGuiApplication.clipboard().setText("ordinary text")
    wait_until(lambda: not action.isEnabled(), timeout=1000)
    assert find(window, "roiPasteReason").property("text")
    assert len(view.measurementController.committed_measurements) == 1
    assert not sidebar_scene[3]


def test_declared_multiphase_requires_a_temporal_identifier(viewport):
    window, view, pixels, _ = viewport
    draw(window, view, pixels, "rect")
    view._frame_meta = replace(view._frame_meta, stack_phase=None)
    assert view.copySelectedAnnotation()
    assert view.roiPasteIssue() == "roiPaste.seriesPhase"
    assert not view.pasteRoiInPlace()


def test_oblique_plane_normal_offset_and_target_padding(viewport):
    window, view, pixels, _ = viewport
    # Plane normal (0, -0.6, 0.8) moves all slices in patient Y and Z.
    frame = view._frame_meta
    geometry = replace(frame.geometry, image_orientation_patient=(1, 0, 0, 0, .8, .6))
    source = replace(_render_result(view), frame_meta=replace(frame, geometry=geometry),
                     modality_pixel=np.ones((256, 256)))
    view.handleRenderResult(source)
    draw(window, view, pixels, "rect")
    assert view.copySelectedAnnotation()
    target_pixels = np.full((256, 256), np.nan)
    target_pixels[60, 80:82] = (4., 6.)
    moved = replace(geometry, image_position_patient=(0, -3, 4))
    view.handleRenderResult(replace(source, modality_pixel=target_pixels,
                                    frame_meta=replace(frame, slice_index=1, geometry=moved)))
    assert view.pasteRoiInPlace()
    copy = view.measurementController.committed_measurements[-1]
    assert copy.metrics.pixel_count == 2 and copy.metrics.mean == 5 and copy.metrics.std == 1
    assert view.pasteAnnotation()
    offset = view.measurementController.committed_measurements[-1]
    assert offset.points[0].column == pytest.approx(copy.points[0].column + 10)
    assert offset.points[0].row == pytest.approx(copy.points[0].row + 10)


def test_temporal_key_ignores_slice_time_and_blocks_unknown_dynamic_phase(tmp_path):
    from test_series_sidebar import phantom_series
    from voxplane.core.dicom_scanner import stack_phase_key
    series = phantom_series(tmp_path, 1, "demo", "1.2.3.1", "20260903")
    base = series.instances[0]
    def key(values, count=None):
        return stack_phase_key(replace(base, phase_values=values, number_of_temporal_positions=count), series)
    assert key((("AcquisitionTime", "110000"),)) == key((("AcquisitionTime", "110002"),)) == ()
    assert key((("AcquisitionTime", "110000"),), 3) is None
    assert key((("TemporalPositionIdentifier", 1),), 3) != key((("TemporalPositionIdentifier", 2),), 3)


def test_keyboard_and_panel_in_place_paste_follow_focus_and_availability(sidebar_scene, tmp_path):
    from test_dicom_tags import wait_until
    from test_input_settings_qml import reading_scene, visible_viewport
    from test_tag_qml import find

    window, app, tab = reading_scene(sidebar_scene)
    window.resize(1320, 900)
    view = tab.activeViewport
    measure = view.measurementController
    QTest.keyClick(window, Qt.Key_M)
    panel_button = find(window, "measurementPasteRoiInPlace")
    assert not panel_button.isEnabled()
    assert "复制" in panel_button.property("hintText")
    QTest.keyClick(window, Qt.Key_R)
    pixels = find(window, "dicomPixelLayer")
    _click_points(window, _scene(pixels, 21, 37), _scene(pixels, 44, 58))
    source, = measure.committed_measurements
    QTest.keySequence(window, QKeySequence(QKeySequence.Copy))
    wait_until(panel_button.isEnabled)
    assert window.grabWindow().save(str(tmp_path / "measurement-paste-action.png"))
    QTest.keyClick(window, Qt.Key_S)
    QTest.keyClick(window, Qt.Key_K)
    wait_until(lambda: view.sliceIndex == 1 and not view.render_pending)
    shortcut = QKeySequence("Ctrl+Shift+V")
    QTest.keySequence(window, shortcut)
    source, target = measure.committed_measurements
    assert (source.slice_index, target.slice_index) == (0, 1)
    assert target.points == source.points and target.metrics.mean != source.metrics.mean
    assert find(window, "roiMetricCard").isVisible()
    assert window.grabWindow().save(str(tmp_path / "keyboard-in-place.png"))

    shortcuts = app.shortcutController
    assert not shortcuts.assign("paste-roi-in-place", "Ctrl+V")
    assert not shortcuts.assign("paste-roi-in-place", "W")
    assert shortcuts.assign("paste-roi-in-place", "F8")
    QTest.keySequence(window, shortcut)
    assert len(measure.committed_measurements) == 2
    find(window, "sidebarPatientSearch").forceActiveFocus()
    QTest.keyClick(window, Qt.Key_F8)
    visible_viewport(window).forceActiveFocus()
    app.feedbackController.show()
    wait_until(lambda: window.property("shortcutPopupOpen"))
    QTest.keyClick(window, Qt.Key_F8)
    QTest.keyClick(window, Qt.Key_Escape)
    wait_until(lambda: not window.property("shortcutPopupOpen"))
    position = _scene(pixels, 75, 70)
    QTest.mousePress(window, Qt.RightButton, pos=position)
    QTest.keyClick(window, Qt.Key_F8)
    QTest.mouseRelease(window, Qt.RightButton, pos=position)
    assert len(measure.committed_measurements) == 2

    QTest.keyClick(window, Qt.Key_F8)
    assert len(measure.committed_measurements) == 3
    assert measure.committed_measurements[-1].points == source.points
    if tab.toolController.activePanel != "measure":
        QTest.keyClick(window, Qt.Key_M)
    button = find(window, "measurementPasteRoiInPlace")
    assert "F8" in button.property("text")
    for _ in range(150):
        QTest.keyClick(window, Qt.Key_Tab)
        if button.hasActiveFocus():
            break
    assert button.hasActiveFocus()
    QTest.keyClick(window, Qt.Key_Space)
    assert len(measure.committed_measurements) == 4
    QTest.keySequence(window, QKeySequence(QKeySequence.Paste))
    assert measure.committed_measurements[-1].points[0].column == pytest.approx(source.points[0].column + 10)
    QGuiApplication.clipboard().setText("replaced")
    wait_until(lambda: not button.isEnabled())
    QTest.keyClick(window, Qt.Key_F8)
    assert len(measure.committed_measurements) == 5
    assert not sidebar_scene[3], sidebar_scene[3]


def test_visible_card_locales_and_population_sd_on_two_pixels(sidebar_scene, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QAccessible
    from qt_pointer import move_pointer
    from test_dicom_tags import wait_until
    from test_input_settings_qml import reading_scene
    from test_tag_qml import descendants, find

    path = sidebar_scene[2][0].first_file
    ds = pydicom.dcmread(path)
    data = ds.pixel_array.copy()
    ds.add_new((0x0028, 0x0120), "US", 4095)
    data[35:61, 19:47] = ds.PixelPaddingValue
    data[38, 22:24] = (1028, 1030)  # CT intercept -1024 gives 4 and 6 HU.
    ds.PixelData = data.tobytes()
    ds.save_as(path, enforce_file_format=True)

    window, app, tab = reading_scene(sidebar_scene)
    view = tab.activeViewport
    tab.toolController.selectInteraction("measure:rect")
    pixels = find(window, "dicomPixelLayer")
    _click_points(window, _scene(pixels, 21, 37), _scene(pixels, 44, 58))
    measured, = view.measurementController.committed_measurements
    assert (measured.metrics.mean, measured.metrics.std, measured.metrics.pixel_count) == (5, 1, 2)
    for language, size, mean, sd, count, explanation in [
        ("zh-CN", "尺寸", "均值", "总体标准差", "有效像素", "分母为 N"),
        ("en-US", "Size", "Mean", "SD (population)", "Valid pixels", "N as its denominator"),
        ("pt-BR", "Tamanho", "Média", "DP populacional", "Pixels válidos", "N como denominador"),
    ]:
        assert app.languageController.selectLanguage(language)
        QTest.qWait(300)  # Allow the translated row delegates and panel layout to settle.
        card = find(window, "roiMetricCard")
        def labels():
            return {i.property("text"): i for i in descendants(card) if i.isVisible() and i.property("text")}
        wait_until(lambda: {size, mean, sd, count, "5.00 HU", "1.00 HU", "2"} <= labels().keys())
        label = labels()[sd]
        interface = QAccessible.queryAccessibleInterface(label)
        hint = interface.text(QAccessible.Description)
        assert explanation in hint
        position = label.mapToScene(QPointF(label.width()/2, label.height()/2)).toPoint()
        move_pointer(window, position)
        def tooltip_window():
            return next((w for w in QGuiApplication.allWindows() if w.isVisible() and hasattr(w, "contentItem")
                         and any(i.isVisible() and i.property("text") == hint
                                 for i in descendants(w.contentItem()))), None)

        wait_until(lambda: tooltip_window() is not None)
        assert tooltip_window().grabWindow().save(str(tmp_path / f"sd-tooltip-{language}.png"))
        assert window.grabWindow().save(str(tmp_path / f"statistics-{language}.png"))
        move_pointer(window, _scene(pixels, 85, 82))
        QTest.qWait(40)
    assert not sidebar_scene[3], sidebar_scene[3]
