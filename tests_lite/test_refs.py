import pytest

from apm_lite.errors import ApmError
from apm_lite.refs import classify


@pytest.mark.parametrize(
    "ref,kind",
    [
        (None, "none"),
        ("main", "literal"),
        ("v1.2.3", "literal"),
        ("release/2024-q1", "literal"),
        ("a" * 40, "sha"),
        ("1a2b" * 10, "sha"),
    ],
)
def test_classify_ok(ref, kind):
    assert classify(ref) == kind


@pytest.mark.parametrize(
    "ref",
    ["1.2.3", "^1.2.0", "~2.1.0", ">=1.0.0 <2.0.0", "1.2.x", ">=banana", "release@candidate"],
)
def test_classify_rejects_semver_like_and_at(ref):
    with pytest.raises(ApmError):
        classify(ref)
