"""With a stride smaller than the patch, neighbouring patches share area: an annotation drawn in one
patch is listed for every other patch it reaches into, with the patch it belongs to."""
import pytest

from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)


@pytest.fixture()
def grid(env):  # noqa: F811
    client, pid, _ = env
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    # Half-overlapping patches: stride is half the patch size.
    res = client.put(f"/api/configs/{config_id}", json={"patch_width": 512, "patch_height": 512, "stride_x": 256, "stride_y": 256})
    assert res.status_code == 200, res.text
    sid = upload(client, pid, [("s.tif", SLIDE)]).json()["slides"][0]["id"]
    assert client.post(f"/api/slides/{sid}/generate-patches", json={"config_version_id": config_id}).status_code == 200
    patches = client.get(f"/api/slides/{sid}/patches", params={"limit": 5000}).json()["items"]
    at = {(p["x"], p["y"]): p for p in patches}
    return client, at


def square(x0, y0, size):
    return [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size]]


def test_an_annotation_shows_in_the_overlapping_neighbour_with_its_owner(grid):
    client, at = grid
    first = at[(0, 0)]
    step = first["width_l0"] // 2  # the stride in Level-0 pixels
    right = at[(step, 0)]
    ds = first["width_l0"] / first["width"]
    # Drawn in the first patch, inside the half it shares with its right neighbour.
    local = square(first["width"] * 0.6, 10, 40)
    ann = client.post(f"/api/patches/{first['id']}/annotations", json={"type": "polygon", "coordinates_patch_local": local}).json()

    listed = client.get(f"/api/patches/{right['id']}/overlapping-annotations").json()
    assert [o["annotation"]["id"] for o in listed] == [ann["id"]]
    owner = listed[0]["owner"]
    assert owner["id"] == first["id"] and (owner["x"], owner["y"], owner["width_l0"]) == (0, 0, first["width_l0"])
    assert listed[0]["annotation"]["coordinates_level0"][0] == [pytest.approx(local[0][0] * ds), pytest.approx(10 * ds)]

    # The patch it was drawn in does not list it as "from elsewhere".
    assert client.get(f"/api/patches/{first['id']}/overlapping-annotations").json() == []


def test_an_annotation_outside_the_shared_area_is_not_listed(grid):
    client, at = grid
    first = at[(0, 0)]
    step = first["width_l0"] // 2
    # In the first patch's top-left quarter, which neither its right nor its lower neighbour covers.
    client.post(f"/api/patches/{first['id']}/annotations", json={"type": "rectangle", "coordinates_patch_local": square(5, 5, 20)})
    assert client.get(f"/api/patches/{at[(step, 0)]['id']}/overlapping-annotations").json() == []
    assert client.get(f"/api/patches/{at[(0, step)]['id']}/overlapping-annotations").json() == []


def test_editing_through_the_owner_updates_what_the_neighbour_sees(grid):
    client, at = grid
    first = at[(0, 0)]
    step = first["width_l0"] // 2
    ds = first["width_l0"] / first["width"]
    ann = client.post(
        f"/api/patches/{first['id']}/annotations",
        json={"type": "circle", "coordinates_patch_local": [[first["width"] * 0.75, 100], [first["width"] * 0.75 + 20, 100]]},
    ).json()
    moved = [[first["width"] * 0.8, 120], [first["width"] * 0.8 + 20, 120]]
    assert client.put(f"/api/annotations/{ann['id']}", json={"coordinates_patch_local": moved}).status_code == 200

    (seen,) = client.get(f"/api/patches/{at[(step, 0)]['id']}/overlapping-annotations").json()
    assert seen["annotation"]["coordinates_level0"][0] == [pytest.approx(moved[0][0] * ds), pytest.approx(120 * ds)]


def test_a_patch_annotation_can_be_edited_in_slide_coordinates_and_stays_in_its_patch(grid):
    client, at = grid
    first = at[(0, 0)]
    ds = first["width_l0"] / first["width"]
    ann = client.post(f"/api/patches/{first['id']}/annotations", json={"type": "rectangle", "coordinates_patch_local": square(10, 10, 50)}).json()
    assert ann["patch_bounds_l0"] == [0, 0, first["width_l0"], first["height_l0"]]

    # Moved on the whole slide: sent in Level-0 pixels, stored in the patch's own pixels too.
    moved_l0 = square(200, 300, 50 * ds)
    res = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_level0": moved_l0})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patch_id"] == first["id"]
    assert body["coordinates_patch_local"][0] == [pytest.approx(200 / ds), pytest.approx(300 / ds)]
    assert body["coordinates_level0"][0] == [pytest.approx(200), pytest.approx(300)]

    # Past the patch's edge: refused, nothing changed.
    outside = square(first["width_l0"] - 10, 0, 40)
    assert client.put(f"/api/annotations/{ann['id']}", json={"coordinates_level0": outside}).status_code == 422
    listed = {a["id"]: a for a in client.get(f"/api/slides/{first['slide_id']}/annotations").json()}
    assert listed[ann["id"]]["coordinates_level0"][0] == [pytest.approx(200), pytest.approx(300)]
    assert listed[ann["id"]]["patch_bounds_l0"] == [0, 0, first["width_l0"], first["height_l0"]]
