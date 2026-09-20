"""The upload receiver writes each part straight to disk. What matters: the bytes
arrive intact (including data that looks like a multipart boundary), client-supplied
names never become paths, and limits stop the request rather than being checked
after everything has been stored."""
import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.services.multipart_stream import receive_multipart


@pytest.fixture
def receiver(tmp_path):
    app = FastAPI()
    state = {"max_files": 10, "max_bytes": 10**9, "max_fields": 20}

    @app.post("/receive")
    async def receive(request: Request):
        form = await receive_multipart(
            request, tmp_path / "in", max_files=state["max_files"], max_bytes=state["max_bytes"], max_fields=state["max_fields"]
        )
        return {
            "files": [{"filename": f.filename, "path": str(f.path), "size": f.path.stat().st_size} for f in form.files],
            "fields": form.fields,
        }

    return TestClient(app), tmp_path, state


def test_files_and_fields_arrive_intact_with_the_field_after_the_files(receiver):
    client, tmp_path, _ = receiver
    # Binary data that contains CRLFs and boundary-looking lines must not confuse the parser.
    tricky = os.urandom(3 * 1024 * 1024) + b"\r\n--not-the-boundary\r\n--\r\n" + os.urandom(1000)
    res = client.post(
        "/receive",
        files=[("files", ("a.svs", tricky, "application/octet-stream")), ("files", ("b.txt", b"", "text/plain"))],
        data={"manifest": '["x/a.svs", "x/b.txt"]', "note": "héllo"},
    ).json()

    assert [f["filename"] for f in res["files"]] == ["a.svs", "b.txt"]
    assert (tmp_path / "in" / "0.part").read_bytes() == tricky
    assert (tmp_path / "in" / "1.part").read_bytes() == b""
    assert res["fields"] == {"manifest": '["x/a.svs", "x/b.txt"]', "note": "héllo"}


def test_a_client_supplied_name_is_reported_but_never_used_as_a_path(receiver):
    client, tmp_path, _ = receiver
    res = client.post("/receive", files=[("files", ("../../evil.tif", b"data", "application/octet-stream"))]).json()

    assert res["files"][0]["filename"] == "../../evil.tif"  # handed back as data, for the caller to validate
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == ["0.part"]
    assert not (tmp_path.parent / "evil.tif").exists()


def test_a_request_that_is_not_multipart_yields_an_empty_form(receiver):
    client, _, _ = receiver
    assert client.post("/receive").json() == {"files": [], "fields": {}}
    assert client.post("/receive", json={"a": 1}).json() == {"files": [], "fields": {}}


def test_going_over_the_size_limit_stops_the_request(receiver):
    client, tmp_path, state = receiver
    state["max_bytes"] = 1000
    res = client.post("/receive", files=[("files", ("a.bin", b"x" * 5000, "application/octet-stream"))])

    assert res.status_code == 413 and "size limit" in res.json()["detail"]


def test_the_size_limit_counts_all_files_together(receiver):
    client, _, state = receiver
    state["max_bytes"] = 1500
    files = [("files", (f"{i}.bin", b"x" * 1000, "application/octet-stream")) for i in range(2)]
    assert client.post("/receive", files=files).status_code == 413


def test_too_many_files_is_refused(receiver):
    client, _, state = receiver
    state["max_files"] = 2
    files = [("files", (f"{i}.bin", b"x", "application/octet-stream")) for i in range(3)]
    res = client.post("/receive", files=files)

    assert res.status_code == 400 and "Too many files" in res.json()["detail"]


def test_a_field_that_is_far_too_large_is_refused(receiver, monkeypatch):
    client, _, _ = receiver
    monkeypatch.setattr("app.services.multipart_stream.MAX_FIELD_BYTES", 100)
    assert client.post("/receive", data={"manifest": "y" * 500}, files=[("files", ("a", b"1"))]).status_code == 413


def test_a_malformed_or_boundaryless_body_is_a_400_not_a_crash(receiver):
    client, _, _ = receiver
    assert client.post("/receive", content=b"garbage", headers={"content-type": "multipart/form-data"}).status_code == 400

    body = b"--b\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a\"\r\n\r\nabc"  # never terminated
    res = client.post("/receive", content=body, headers={"content-type": "multipart/form-data; boundary=b"})
    assert res.status_code == 400
