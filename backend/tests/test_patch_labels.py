"""Patch labels fill their patch with a linked whole-patch annotation, and the patch-classification
export sorts patches into class folders by label or by drawn coverage."""
import csv
import io
import zipfile

from tests.test_patch_grids import BIG, SMALL, generate, key, patches, slide, square  # noqa: F401  (slide is a fixture)
from tests.test_slide_import_api import env  # noqa: F401


def setup_classes(client, pid):
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    res = client.put(
        f"/api/configs/{config_id}",
        json={"annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626"}, {"name": "Stroma", "color_hex": "#2563eb"}]},
    )
    return {c["name"]: c for c in res.json()["annotation_classes"]}


def label(client, patch_id, value):
    res = client.put(f"/api/patches/{patch_id}", json={"patch_label": value})
    assert res.status_code == 200, res.text
    return res.json()


def anns(client, patch_id):
    return client.get(f"/api/patches/{patch_id}/annotations").json()


def fills(client, patch_id):
    return [a for a in anns(client, patch_id) if a["whole_patch"]]


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_a_class_label_fills_the_patch_and_stays_linked(slide):
    client, pid, sid, config_id = slide
    classes = setup_classes(client, pid)
    generate(client, sid, config_id, BIG)
    p = patches(client, sid)[0]
    client.post(f"/api/patches/{p['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]})

    out = label(client, p["id"], "tumor")  # matched case-insensitively
    assert out["patch_label"] == "Tumor" and out["label_class_id"] == classes["Tumor"]["id"]
    assert out["status"] == "annotated"
    [fill] = fills(client, p["id"])
    assert fill["class_id"] == classes["Tumor"]["id"] and fill["type"] == "rectangle"
    assert fill["coordinates_patch_local"] == [[0, 0], [512, 0], [512, 512], [0, 512]]
    assert len(anns(client, p["id"])) == 2  # the drawn point stays

    label(client, p["id"], "Stroma")  # the fill follows the label
    [fill] = fills(client, p["id"])
    assert fill["class_id"] == classes["Stroma"]["id"]

    client.put(f"/api/annotations/{fill['id']}", json={"class_id": classes["Tumor"]["id"]})  # ...and the label the fill
    assert client.get(f"/api/patches/{p['id']}").json()["patch_label"] == "Tumor"

    label(client, p["id"], "Mixed")  # not a class: text only, no fill
    got = client.get(f"/api/patches/{p['id']}").json()
    assert got["patch_label"] == "Mixed" and got["label_class_id"] is None and fills(client, p["id"]) == []

    label(client, p["id"], "Tumor")
    [fill] = fills(client, p["id"])
    assert client.delete(f"/api/annotations/{fill['id']}").status_code == 204  # deleting the fill clears the label
    assert client.get(f"/api/patches/{p['id']}").json()["patch_label"] is None

    label(client, p["id"], "Tumor")
    label(client, p["id"], None)  # unset removes the fill
    assert fills(client, p["id"]) == [] and len(anns(client, p["id"])) == 1


def test_reshaping_the_fill_unlinks_it(slide):
    client, pid, sid, config_id = slide
    setup_classes(client, pid)
    generate(client, sid, config_id, BIG)
    p = patches(client, sid)[0]
    label(client, p["id"], "Tumor")
    [fill] = fills(client, p["id"])

    same = client.put(f"/api/annotations/{fill['id']}", json={"coordinates_patch_local": fill["coordinates_patch_local"]}).json()
    assert same["whole_patch"]  # nothing moved
    moved = client.put(f"/api/annotations/{fill['id']}", json={"coordinates_patch_local": square(0, 0, 300)}).json()
    assert not moved["whole_patch"]
    assert client.get(f"/api/patches/{p['id']}").json()["patch_label"] == "Tumor"  # the label stays
    label(client, p["id"], None)
    assert len(anns(client, p["id"])) == 1  # an ordinary shape now: clearing the label leaves it


def test_renaming_a_class_renames_its_labels_and_a_used_class_cannot_be_removed(slide):
    client, pid, sid, config_id = slide
    classes = setup_classes(client, pid)
    generate(client, sid, config_id, BIG)
    p = patches(client, sid)[0]
    label(client, p["id"], "Tumor")
    [fill] = fills(client, p["id"])
    client.delete(f"/api/annotations/{fill['id']}")
    label(client, p["id"], "Tumor")

    body = [{"id": classes["Tumor"]["id"], "name": "Carcinoma", "color_hex": "#dc2626"}, {"id": classes["Stroma"]["id"], "name": "Stroma", "color_hex": "#2563eb"}]
    assert client.put(f"/api/configs/{config_id}", json={"annotation_classes": body}).status_code == 200
    assert client.get(f"/api/patches/{p['id']}").json()["patch_label"] == "Carcinoma"

    res = client.put(f"/api/configs/{config_id}", json={"annotation_classes": body[1:]})
    assert res.status_code in (409, 422)


def test_classification_export_sorts_patches_into_class_folders(slide):
    client, pid, sid, config_id = slide
    setup_classes(client, pid)
    generate(client, sid, config_id, BIG)  # 3 x 2 patches of 512
    ps = sorted(patches(client, sid), key=lambda p: p["patch_index"])
    label(client, ps[0]["id"], "Tumor")
    label(client, ps[1]["id"], "Mixed")
    stroma_id = next(c["id"] for c in client.get(f"/api/configs/{config_id}").json()["annotation_classes"] if c["name"] == "Stroma")
    client.post(f"/api/patches/{ps[2]['id']}/annotations", json={"type": "rectangle", "class_id": stroma_id, "coordinates_patch_local": square(0, 0, 500)})  # ~95%
    client.post(f"/api/patches/{ps[3]['id']}/annotations", json={"type": "rectangle", "class_id": stroma_id, "coordinates_patch_local": square(0, 0, 300)})  # ~34%

    table = rows(client.get(f"/api/slides/{sid}/export/patch_classification").text)
    got = {int(r["patch_id"]): (r["class"], r["class_source"]) for r in table}
    assert got == {ps[0]["id"]: ("Tumor", "label"), ps[1]["id"]: ("Mixed", "label"), ps[2]["id"]: ("Stroma", "drawn")}
    assert all(r["file"].startswith(f"images/{r['class']}/") for r in table)

    lax = rows(client.get(f"/api/slides/{sid}/export/patch_classification", params={"min_coverage": 0.5, "other_labels": False, "unlabeled": "folder"}).text)
    got = {int(r["patch_id"]): r["class"] for r in lax}
    assert ps[1]["id"] not in got  # Mixed left out
    assert got[ps[3]["id"]] == "unlabeled"
    assert client.get(f"/api/slides/{sid}/export/patch_classification", params={"min_coverage": 0.2}).status_code == 422

    res = client.get(f"/api/slides/{sid}/export/patch_classification", params={"content": "images", "image_format": "png"})
    assert res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        names = z.namelist()
        labels = rows(z.read("labels.csv").decode())
    images = sorted(n for n in names if n.startswith("images/"))
    assert len(images) == 3 and {n.split("/")[1] for n in images} == {"Tumor", "Mixed", "Stroma"}
    assert sorted(r["file"] for r in labels) == images

    summary = client.get(f"/api/slides/{sid}/export-summary", params={"format": "patch_classification"}).json()
    assert summary["images"] == 3


def test_labels_reach_a_custom_export_grid_through_their_fills(slide):
    client, pid, sid, config_id = slide
    setup_classes(client, pid)
    generate(client, sid, config_id, BIG)
    first = sorted(patches(client, sid), key=lambda p: p["patch_index"])[0]
    label(client, first["id"], "Tumor")

    table = rows(client.get(f"/api/slides/{sid}/export/patch_classification", params={"grid": key(SMALL)}).text)
    inside = [r for r in table if int(r["level0_x"]) < 512 and int(r["level0_y"]) < 512]
    assert len(table) == 4 and len(inside) == 4  # the four 256 px patches inside the labelled 512 one
    assert all(r["class"] == "Tumor" and r["class_source"] == "drawn" and float(r["coverage"]) == 1.0 for r in table)
