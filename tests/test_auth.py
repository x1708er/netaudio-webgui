import json

from netaudio_webgui.auth import (
    SessionStore,
    UserStore,
    hash_password,
    is_hash,
    verify_password,
)


def test_hash_password_has_scrypt_prefix():
    h = hash_password("secret")
    assert h.startswith("scrypt$")
    assert is_hash(h)


def test_hash_password_is_salted_unique():
    assert hash_password("secret") != hash_password("secret")


def test_verify_password_roundtrip():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_rejects_plaintext_stored():
    # A non-hash stored value never verifies (defends against unmigrated entries).
    assert verify_password("secret", "secret") is False


def test_is_hash_false_for_plaintext():
    assert is_hash("just-a-password") is False


def test_verify_password_rejects_absurd_params():
    # A hash claiming a huge N must be rejected fast, not fed to scrypt.
    forged = "scrypt$2147483648$8$1$c2FsdA==$aGFzaA=="
    assert verify_password("whatever", forged) is False


def test_userstore_from_plaintext_verifies():
    store = UserStore.from_plaintext({"alice": "pw1"})
    assert store.usernames() == ["alice"]
    assert store.verify("alice", "pw1") is True
    assert store.verify("alice", "nope") is False
    assert store.verify("bob", "pw1") is False


def test_userstore_load_hashes_plaintext_and_writes_back(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"alice": "plain"}), encoding="utf-8")

    store = UserStore.load(path)
    assert store.verify("alice", "plain") is True

    # File was rewritten with a hash, plaintext is gone.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["alice"].startswith("scrypt$")
    assert on_disk["alice"] != "plain"


def test_userstore_load_leaves_existing_hash_untouched(tmp_path):
    h = hash_password("plain")
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"alice": h}), encoding="utf-8")

    store = UserStore.load(path)
    assert store.verify("alice", "plain") is True
    # Unchanged on disk (no needless rewrite).
    assert json.loads(path.read_text(encoding="utf-8"))["alice"] == h


def test_userstore_load_missing_file_is_empty(tmp_path):
    store = UserStore.load(tmp_path / "absent.json")
    assert store.usernames() == []


def test_userstore_load_skips_malformed_entries(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"alice": "pw", "bad_num": 42, "empty": ""}),
                    encoding="utf-8")
    store = UserStore.load(path)
    assert store.usernames() == ["alice"]
    assert store.verify("alice", "pw") is True


def test_sessionstore_create_get_delete():
    sessions = SessionStore()
    token = sessions.create("alice")
    assert isinstance(token, str) and len(token) > 20
    assert sessions.get(token)["username"] == "alice"

    sessions.delete(token)
    assert sessions.get(token) is None


def test_sessionstore_get_unknown_or_empty():
    sessions = SessionStore()
    assert sessions.get("nope") is None
    assert sessions.get(None) is None


def test_sessionstore_tokens_unique():
    sessions = SessionStore()
    assert sessions.create("a") != sessions.create("a")
