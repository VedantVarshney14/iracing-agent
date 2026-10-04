import pytest

from iagent.references.ghosts import GhostInfo, file_name, install_dir, read_info
from iagent.testing.garage61 import fake_blap


def test_reads_driver_car_and_track_from_the_header():
    info = read_info(fake_blap("Some Driver", "formulair04", "okayama\\full"))
    assert info == GhostInfo("Some Driver", "formulair04", "okayama\\full")


def test_rejects_other_files():
    with pytest.raises(ValueError, match="blap"):
        read_info(b"PK\x03\x04 not a ghost")


def test_installs_into_the_folder_iracing_already_made(tmp_path):
    info = GhostInfo("x", "formulair04", "okayama\\full")
    assert install_dir(tmp_path, info) == tmp_path / "okayama"  # nothing yet: new track folder
    (tmp_path / "okayama").mkdir()
    assert install_dir(tmp_path, info) == tmp_path / "okayama"
    (tmp_path / "okayama" / "full").mkdir()
    assert install_dir(tmp_path, info) == tmp_path / "okayama" / "full"  # most specific wins


def test_file_names_never_collide_with_iracings_own():
    name = file_name("some-driver", "formulair04", 91.636)
    assert name == "g61_some-driver_formulair04_91_636.blap"
    assert file_name("a b/c", None, None) == "g61_a-b-c_car_lap.blap"
