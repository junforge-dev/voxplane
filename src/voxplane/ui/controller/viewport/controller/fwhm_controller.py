"""Independent wire-ramp ROI, analysis settings, jobs and per-slice results."""
from .mtf_controller import MtfController


class FwhmController(MtfController):
    TARGET_METHODS = ("ramp",)

    @property
    def completed_records(self):
        """Yield completed ROI records, including results on other slices."""
        for mid, record in self._records.items():
            analysis = self._analyses.get(mid)
            if analysis is None or analysis.result is None or analysis.error:
                continue
            if self._source_error and record["frame"][:3] == self._roi.frame_key[:3]:
                continue
            yield record
