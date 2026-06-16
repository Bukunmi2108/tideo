import pytest

from app.domain import recommend
from app.domain.ladder import PRESETS, gop_size


@pytest.mark.parametrize("fps,g", [
    (24.0, 48), (30.0, 60), (60.0, 120),
    (23.976, 48), (29.97, 60),     # NTSC rates round to the nominal fps
])
def test_gop_is_two_seconds_of_frames(fps, g):
    assert gop_size(fps) == g


def test_catalog_has_the_four_rungs():
    assert set(PRESETS) == {"1080p", "720p", "480p", "360p"}
    for name, p in PRESETS.items():
        assert p.name == name
        assert p.profile in ("high", "main")


def test_catalog_names_match_recommendation_ladder():
    # the encode catalog and the recommendation ladder must name the same rungs
    assert set(PRESETS) == {name for name, _ in recommend.LADDER}
