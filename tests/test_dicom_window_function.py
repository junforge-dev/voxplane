import numpy as np
import pytest

from voxplane.core.dicom_loader import DicomLoader
from voxplane.core.export_images import frame_image
from voxplane.core.series_thumbnail import read_series_thumbnail
from test_dicom_tags import make_dicom
from test_pet_fusion import paired_series


@pytest.mark.parametrize("function,width,slope,expected", [
    (None, 1, 1, [0, 0, 255, 255, 255]),
    ("LINEAR", 3, 1, [0, 63, 191, 255, 255]),
    ("LINEAR_EXACT", 4, 1, [0, 63, 127, 191, 255]),
    ("SIGMOID", 2, 1, [4, 30, 127, 224, 250]),
    ("SIGMOID", .25, .125, [4, 30, 127, 224, 250]),
    ("LINEAR_EXACT", .5, .125, [0, 63, 127, 191, 255]),
])
def test_source_voi_for_loaded_exported_and_thumbnail_pixels(tmp_path, function, width, slope, expected):
    path = tmp_path / "voi.dcm"
    ds = make_dicom(path)
    ds.NumberOfFrames = 1
    ds.Rows, ds.Columns = 32, 5
    stored = np.tile(np.arange(5, dtype=np.uint16), (32, 1))
    ds.RescaleSlope, ds.RescaleIntercept = slope, -2 * slope
    ds.WindowCenter, ds.WindowWidth = 0, width
    if function is not None:
        ds.VOILUTFunction = function
    ds.PixelData = stored.tobytes()
    ds.save_as(path, enforce_file_format=True)

    loader = DicomLoader()
    header, samples = loader.read_frame(path)
    loaded = loader.load_dataset(header, None, False, modality_pixels=samples)
    assert loaded.image[0].tolist() == expected
    assert loaded.window.width == width
    assert loaded.instance_meta.voi_lut_function == (function or "LINEAR")
    exported = frame_image(stored, ds)
    assert [exported.pixelColor(x, 0).red() for x in range(5)] == expected
    # Each source column occupies four thumbnail columns. Check centers on
    # a constant slab below to avoid blending unequal neighbors in Qt.
    ds.PixelData = np.repeat(stored, 4, axis=1).tobytes()
    ds.Columns = 20
    ds.save_as(path, enforce_file_format=True)
    thumbnail = read_series_thumbnail(path)
    assert [thumbnail.pixelColor(16*x+7, 8).red() for x in range(5)] == expected


def test_non_hu_ct_without_source_window_uses_quantitative_range(tmp_path):
    ds = make_dicom(tmp_path / "derived.dcm")
    ds.RescaleType = "MGML"
    del ds.WindowCenter, ds.WindowWidth
    values = np.linspace(.1, .2, 100, dtype=np.float32).reshape(10, 10)
    result = DicomLoader().load_dataset(ds, None, False, modality_pixels=values)
    assert result.window.center == pytest.approx(.15)
    assert result.window.width == pytest.approx(.098)
    assert (result.image.min(), result.image.max()) == (0, 255)


@pytest.mark.parametrize("function,center,width", [("LINEAR", 128, 256), ("LINEAR_EXACT", 127.5, 255)])
@pytest.mark.parametrize("inverted", [False, True])
def test_eight_bit_identity_window_preserves_every_level(function, center, width, inverted):
    from voxplane.model import WindowLevel
    ramp = np.arange(256, dtype=np.uint8)
    actual = DicomLoader.apply_window(ramp, WindowLevel(center, width), inverted, voi_lut_function=function)
    np.testing.assert_array_equal(actual, 255 - ramp if inverted else ramp)


def test_ct_voi_reaches_cached_montage_mpr_and_pet_ct_fusion(paired_series):
    import pydicom
    from voxplane.core.pet_reconstruction import PetReconstructor
    from voxplane.core.volume_manager import VolumeManager
    from voxplane.model import MontageRenderRequest, MprPlane, MprRenderRequest, WindowLevel
    from voxplane.model.render_models import PetBatchRenderRequest
    from voxplane.ui.workers.dicom_render_worker import DicomRenderWorker

    catalog, ct, pet = paired_series
    for instance in ct.instances:
        ds = pydicom.dcmread(instance.path)
        ds.WindowCenter, ds.WindowWidth, ds.VOILUTFunction = 1000, 2000, "SIGMOID"
        ds.save_as(instance.path, enforce_file_format=True)

    volumes = VolumeManager()
    worker = DicomRenderWorker(catalog, volumes)
    results = []
    worker.render_finished.connect(results.append)
    options = dict(viewport_id="view", series_uid=ct.series_instance_uid, inverted=False)
    for request_id, window in (("source", None), ("cached", WindowLevel(500, 1000))):
        worker.handleRenderRequest(MontageRenderRequest(
            **options, request_id=request_id, slice_index=1, window=window))
    worker.handleRenderRequest(MprRenderRequest(**options, request_id="mpr",
        plane=MprPlane.AXIAL, window=None, mpr_frame=None))
    assert len(results) == 3

    batch = PetReconstructor(catalog, volumes).render(PetBatchRenderRequest(
        request_id="batch", viewport_id="fusion", series_uid=pet.series_instance_uid,
        ct_series_uid=ct.series_instance_uid, viewports=(("ct", "ct"), ("fusion", "fused")),
        opacity=0))
    results.append(batch.frames[0])
    for result in results:
        assert result.frame_meta.instance_meta.voi_lut_function == "SIGMOID"
        window = result.frame_meta.window
        values = result.modality_pixel.astype(float)
        expected = 255 / (1 + np.exp(-4 * (values - window.center) / window.width))
        np.testing.assert_array_equal(result.image, np.nan_to_num(expected).astype(np.uint8))
    np.testing.assert_array_equal(batch.frames[1].image, np.repeat(batch.frames[0].image[..., None], 3, axis=-1))
