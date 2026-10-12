import pytest

from morning_edition.config import AppConfig, load_config

BASE = {"DASHBOARD_PASSWORD": "pw", "DATA_DIR": "/tmp/me-data"}


def test_update_settings_default_to_unset_and_the_public_image():
    c = load_config(BASE)
    assert (c.watchtower_url, c.watchtower_token, c.app_version, c.app_commit) == ("", "", "", "")
    assert c.app_image == "ghcr.io/rickgram/morning-edition"
    assert c.app_repo == "rickgram/morning-edition"


def test_update_settings_come_from_the_environment():
    c = load_config({**BASE, "WATCHTOWER_URL": "http://watchtower:8080", "WATCHTOWER_TOKEN": "tok",
                     "APP_VERSION": "v1.2.0", "APP_COMMIT": "abc123", "APP_IMAGE": "ghcr.io/me/fork",
                     "APP_REPO": "me/fork"})
    assert (c.watchtower_url, c.watchtower_token) == ("http://watchtower:8080", "tok")
    assert (c.app_version, c.app_commit, c.app_image, c.app_repo) == ("v1.2.0", "abc123", "ghcr.io/me/fork", "me/fork")


def test_the_token_never_appears_in_the_config_repr():
    c = load_config({**BASE, "WATCHTOWER_TOKEN": "super-secret-token"})
    assert "super-secret-token" not in repr(c) and "super-secret-token" not in str(c)


def test_password_is_still_required():
    with pytest.raises(RuntimeError):
        load_config({})


def test_direct_construction_keeps_working(tmp_path):
    assert AppConfig(data_dir=tmp_path, dashboard_password="x", session_secret="y").watchtower_token == ""
