"""Classic CT calibration follows source metadata through scanning, viewing and reconstruction."""
from pathlib import Path

import numpy as np
import pydicom
import pytest
from pydicom.uid import CTImageStorage, SecondaryCaptureImageStorage

from voxplane.application.series_catalog import SeriesCatalog
from voxplane.core.ct import ct_view_error
from voxplane.core.dicom_loader import DicomLoader
from voxplane.core.dicom_scanner import DicomFolderScanner
from voxplane.core.volume_manager import VolumeBuildError, VolumeManager
from voxplane.core.workspace_document import read_document

from test_dicom_tags import qt_app, wait_until
from test_pacs_qml import scene
from test_tag_qml import descendants, find


def ct_stack(root, image_type, rescale_type, multi_energy=None, storage=CTImageStorage):
    source = Path(__file__).parent / 'fixtures/upgrades/v2.5.0/images'
    paths = []
    for path in sorted(source.glob('*.dcm')):
        ds = pydicom.dcmread(path)
        ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = storage
        if image_type:
            ds.ImageType = image_type.split('\\')
        if rescale_type:
            ds.RescaleType = rescale_type
        if multi_energy:
            ds.MultienergyCTAcquisition = multi_energy
        ds.RescaleSlope, ds.RescaleIntercept = 2, -3
        paths.append(root / path.name)
        ds.save_as(paths[-1], enforce_file_format=True)
    return list(DicomFolderScanner().scan_files(paths, folder=root))[-1]


@pytest.mark.parametrize('image_type,source,energy,storage,expected', [
    (r'ORIGINAL\PRIMARY\AXIAL', None, None, CTImageStorage, 'HU'),
    (None, None, None, CTImageStorage, 'HU'),  # Legacy workspace source
    (r'DERIVED\SECONDARY\AXIAL', 'HU', None, CTImageStorage, 'HU'),
    (r'DERIVED\SECONDARY\AXIAL', 'MGML', None, CTImageStorage, 'mg/mL'),
    (r'DERIVED\SECONDARY\AXIAL', None, None, CTImageStorage, 'a.u.'),
    (r'ORIGINAL\PRIMARY\AXIAL', 'US', None, CTImageStorage, 'a.u.'),
    (r'ORIGINAL\PRIMARY\LOCALIZER', None, None, CTImageStorage, 'a.u.'),
    (r'ORIGINAL\PRIMARY\AXIAL', None, 'YES', CTImageStorage, 'a.u.'),
    (r'ORIGINAL\PRIMARY\AXIAL', None, None, SecondaryCaptureImageStorage, 'a.u.'),
])
def test_classic_ct_stack_units(tmp_path, image_type, source, energy, storage, expected):
    snapshot = ct_stack(tmp_path, image_type, source, energy, storage)
    series = snapshot.series[0]
    catalog = SeriesCatalog()
    catalog.update(snapshot)
    hu = expected == 'HU'
    assert catalog.get_series_display_meta(series.series_instance_uid).supports_ct_analysis == hu
    assert not ct_view_error(series, '2d')
    for view in ('mpr', 'cpr', '3d', '4d', 'comparempr', 'petctfusion'):
        assert bool(ct_view_error(series, view)) == (not hu)

    ds = pydicom.dcmread(series.first_file)
    loaded = DicomLoader().load_dataset(ds, None, False)
    assert loaded.pixel_value_meta.unit == expected
    if source:
        assert loaded.pixel_value_meta.source_unit == source
    np.testing.assert_equal(loaded.modality_pixel, ds.pixel_array.astype(float) * 2 - 3)
    if hu:
        assert VolumeManager().get_or_build(series).pixel_value_meta.unit == 'HU'
    else:
        with pytest.raises(VolumeBuildError):
            VolumeManager().get_or_build(series)


@pytest.mark.parametrize("change", ["missing-calibration", "derived"])
def test_untagged_legacy_ct_requires_calibration_and_no_derivation(tmp_path, change):
    snapshot = ct_stack(tmp_path, None, None)
    ds = pydicom.dcmread(snapshot.series[0].first_file)
    if change == "missing-calibration":
        del ds.RescaleSlope
    else:
        ds.DerivationDescription = "Material density"
    _, meta, _ = DicomLoader.to_display_values(ds, ds.pixel_array)
    assert meta.unit == "a.u."


def test_mixed_or_replaced_source_cannot_form_hu_volume(tmp_path):
    snapshot = ct_stack(tmp_path, r'ORIGINAL\PRIMARY\AXIAL', 'HU')
    series = snapshot.series[0]
    path = series.instances[-1].path
    ds = pydicom.dcmread(path)
    ds.ImageType, ds.RescaleType = ['DERIVED', 'SECONDARY', 'AXIAL'], 'MGML'
    ds.save_as(path, enforce_file_format=True)
    with pytest.raises(VolumeBuildError):
        VolumeManager().get_or_build(series)
    rescanned = list(DicomFolderScanner().scan_files([i.path for i in series.instances], folder=tmp_path))[-1]
    catalog = SeriesCatalog()
    catalog.update(rescanned)
    assert not catalog.get_series_display_meta(series.series_instance_uid).supports_ct_analysis


def test_derived_ct_qml_browsing_and_analysis(scene, tmp_path):
    window, app, warnings = scene
    snapshot = ct_stack(tmp_path, r'DERIVED\SECONDARY\AXIAL', 'MGML')
    app.panelController.acceptPacsImport(snapshot)
    workspace = app.workspaceController
    wait_until(lambda: workspace.activeViewport is not None and bool(workspace.activeViewport.imageSource))
    view = workspace.activeViewport
    assert find(window, 'openView-montage').isEnabled()
    assert not view.supportsCtWindow
    assert view.displayMapping['unit'] == 'mg/mL'
    view.updateCursorPosition(64, 64, 64, 64, 64, 64, True, 1, 1)
    cursor = view.cursorController.cursorInfo
    assert cursor['unit'] == 'mg/mL' and float(cursor['value']) == 11
    wait_until(lambda: any('11 mg/mL' in str(item.property('text'))
                           for item in descendants(window.contentItem())
                           if item.objectName() == 'cornerInformationLine' and item.isVisible()))
    assert 'service' not in [item['toolType'] for item in workspace.activeTab.toolController.tools]
    assert not view.qaController.available
    view.qaController.activate()
    assert not view.qaController.enabled
    assert not view.mtfController.frameToken and not view.fwhmController.frameToken

    legacy = read_document(Path(__file__).parent / 'fixtures/upgrades/v2.5.0/workspace.voxworkspace')
    old_view = next(iter(legacy['tabs'][0]['views'].values()))
    old_qa = old_view['analyses']['qa']
    assert old_qa['enabled'] and old_qa['records']
    view.qaController.restore_state(old_qa)
    assert not view.qaController.enabled and not view.qaController.currentResult
    assert view.qaController.persistent_state()['records'] == old_qa['records']
    assert not warnings


def test_non_hu_phase_blocks_four_d_without_disabling_the_hu_stack(tmp_path):
    from test_four_d import _cross_series_four_d

    records = _cross_series_four_d(tmp_path, phase_count=2)
    paths = [instance.path for record in records for instance in record.instances]
    for instance in records[1].instances:
        ds = pydicom.dcmread(instance.path)
        ds.ImageType, ds.RescaleType = ['DERIVED', 'SECONDARY', 'AXIAL'], 'MGML'
        ds.save_as(instance.path, enforce_file_format=True)
    snapshot = list(DicomFolderScanner().scan_files(paths, folder=tmp_path))[-1]
    catalog = SeriesCatalog()
    catalog.update(snapshot)
    hu = catalog.get_series_display_meta(records[0].series_instance_uid)
    assert hu.supports_ct_analysis and not hu.supports_four_d
