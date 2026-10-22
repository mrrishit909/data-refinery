import io
import urllib.error
import urllib.request

from harness.core import Repo
from harness.github import GitHub


def test_artifact_download_never_sends_the_token_to_storage(monkeypatch):
    seen = {}

    class Opener:
        def open(self, req, timeout=None):
            seen["api_auth"] = req.get_header("Authorization")
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://storage.example/blob?sig=x"}, io.BytesIO())

    def fake_urlopen(req, timeout=None):
        seen["storage_url"] = req.full_url
        seen["storage_auth"] = req.get_header("Authorization")
        return io.BytesIO(b"zip-bytes")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    gh = GitHub(Repo("owner", "repo"), token="t0ken")
    assert gh.download_artifact(7) == b"zip-bytes"
    assert seen["api_auth"] == "Bearer t0ken"
    assert seen["storage_url"] == "https://storage.example/blob?sig=x"
    assert seen["storage_auth"] is None
