"""Train / val / test split: whole slides are dealt out at random or assigned by hand, and an export
writes each set into its own folder."""
import io
import json
import zipfile

from tests.test_export_selection import project  # noqa: F401  (fixture: three slides, two with a grid)
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)


def sets(body):
    return {s["slide_id"]: s["split"] for s in body["slides"]}


def test_a_project_starts_without_a_split(env):  # noqa: F811
    client, pid, _ = env
    body = client.get(f"/api/projects/{pid}/split").json()
    assert body["mode"] == "off" and (body["train"], body["val"], body["test"]) == (70, 15, 15)


def test_a_random_split_matches_the_shares_and_stays_put(env):  # noqa: F811
    client, pid, _ = env
    upload(client, pid, [(f"s{i}.tif", SLIDE) for i in range(10)])
    body = client.put(f"/api/projects/{pid}/split", json={"mode": "random", "train": 60, "val": 20, "test": 20}).json()
    assert body["counts"] == {"train": 6, "val": 2, "test": 2, "unassigned": 0}

    # Asking again changes nothing; a slide added later joins without moving the others.
    assert sets(client.get(f"/api/projects/{pid}/split").json()) == sets(body)
    upload(client, pid, [("late.tif", SLIDE)])
    after = client.get(f"/api/projects/{pid}/split").json()
    assert after["counts"]["unassigned"] == 0 and sum(after["counts"].values()) == 11
    assert {k: v for k, v in sets(after).items() if k in sets(body)} == sets(body)

    # New shares deal everything out again.
    changed = client.put(f"/api/projects/{pid}/split", json={"mode": "random", "train": 100, "val": 0, "test": 0}).json()
    assert changed["counts"]["train"] == 11


def test_shares_must_add_up(env):  # noqa: F811
    client, pid, _ = env
    assert client.put(f"/api/projects/{pid}/split", json={"mode": "random", "train": 70, "val": 20, "test": 20}).status_code == 422
    assert client.put(f"/api/projects/{pid}/split", json={"mode": "random", "train": 0, "val": 50, "test": 50}).status_code == 422


def test_a_manual_split_assigns_only_what_it_is_told(env):  # noqa: F811
    client, pid, _ = env
    ids = [s["id"] for s in upload(client, pid, [("a.tif", SLIDE), ("b.tif", SLIDE), ("c.tif", SLIDE)]).json()["slides"]]
    body = client.put(
        f"/api/projects/{pid}/split", json={"mode": "manual", "assignments": {ids[0]: "train", ids[1]: "test"}}
    ).json()
    assert sets(body) == {ids[0]: "train", ids[1]: "test", ids[2]: None}
    body = client.put(f"/api/projects/{pid}/split", json={"mode": "manual", "assignments": {ids[1]: None, ids[2]: "val"}}).json()
    assert sets(body) == {ids[0]: "train", ids[1]: None, ids[2]: "val"}
    assert client.put(f"/api/projects/{pid}/split", json={"mode": "manual", "assignments": {999999: "train"}}).status_code == 422
    listed = {s["id"]: s["split"] for s in client.get(f"/api/projects/{pid}/slides").json()}
    assert listed == {ids[0]: "train", ids[1]: None, ids[2]: "val"}


def test_a_project_can_be_created_with_a_split(env):  # noqa: F811
    client, _, _ = env
    created = client.post("/api/projects", json={"name": "With split", "split": {"mode": "random", "train": 80, "val": 10, "test": 10}})
    assert created.status_code == 201, created.text
    config = created.json()["split_config"]
    assert (config["mode"], config["train"]) == ("random", 80)
    pid = created.json()["id"]
    upload(client, pid, [("a.tif", SLIDE)])
    assert client.get(f"/api/projects/{pid}/split").json()["counts"]["train"] == 1  # dealt out as it arrives


def test_an_export_puts_each_set_in_its_own_folder(project):  # noqa: F811
    client, pid, ids = project
    params = {"formats": "coco", "content": "images", "patches": "all", "split": "true"}
    assert client.get(f"/api/projects/{pid}/export", params=params).status_code == 422  # no split yet

    client.put(f"/api/projects/{pid}/split", json={"mode": "manual", "assignments": {ids[0]: "train", ids[1]: "val"}})
    res = client.get(f"/api/projects/{pid}/export", params=params)
    assert res.status_code == 200, res.text
    assert res.headers["content-disposition"].endswith('_split.zip"')
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
        table = z.read("splits.csv").decode().splitlines()
        train = json.loads(z.read(next(n for n in names if n.startswith("train/annotations/"))))
    assert len([n for n in names if n.startswith("train/images/")]) == 6
    assert len([n for n in names if n.startswith("val/images/")]) == 6
    assert not any(n.startswith("images/") for n in names)
    assert manifest["splits"] == {"train": ["a.tif"], "val": ["b.tif"]}
    assert table == ["slide_id,slide,split", f"{ids[0]},a.tif,train", f"{ids[1]},b.tif,val"]
    assert len(train["images"]) == 6  # the training file names only the training images

    # Without images, and a slide in no set: still one folder per set.
    client.put(f"/api/projects/{pid}/split", json={"mode": "manual", "assignments": {ids[1]: None}})
    res = client.get(f"/api/projects/{pid}/export", params={"formats": "wsi_json", "split": "true"})
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        folders = {n.split("/")[0] for n in z.namelist() if "/" in n}
    assert folders == {"train", "unassigned"}
