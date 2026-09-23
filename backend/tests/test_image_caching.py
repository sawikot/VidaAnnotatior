"""Slide images may be cached by the browser only when asked for with the slide's current version,
so a slide id reused after a delete can never show the old slide's pixels."""
from app.services.deepzoom_service import _LRUTileCache
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)

IMMUTABLE = "private, max-age=31536000, immutable"


def test_image_urls_with_the_slide_version_are_cacheable_and_others_are_not(env):  # noqa: F811
    client, pid, _ = env
    slide = upload(client, pid, [("s.tif", SLIDE)]).json()["slides"][0]
    sid, version = slide["id"], slide["image_version"]
    assert version and version != "0"

    urls = [
        f"/api/slides/{sid}/dzi.dzi",
        f"/api/slides/{sid}/dzi_files/10/0_0.jpeg",
        f"/api/slides/{sid}/thumbnail?max_size=128",
        f"/api/slides/{sid}/patch?x=0&y=0&width=64&height=64&level=0",
    ]
    for url in urls:
        sep = "&" if "?" in url else "?"
        assert client.get(f"{url}{sep}v={version}").headers["cache-control"] == IMMUTABLE, url
        assert client.get(url).headers["cache-control"] == "no-cache", url
        assert client.get(f"{url}{sep}v=stale").headers["cache-control"] == "no-cache", url


def test_a_replacement_slide_gets_a_new_version(env):  # noqa: F811
    client, pid, _ = env
    first = upload(client, pid, [("a.tif", SLIDE)]).json()["slides"][0]
    client.delete(f"/api/slides/{first['id']}")
    second = upload(client, pid, [("b.tif", SLIDE)]).json()["slides"][0]
    assert second["image_version"] != first["image_version"]


def test_tile_cache_is_bounded_by_memory_not_count():
    cache = _LRUTileCache(max_bytes=1000)
    for i in range(10):
        cache.put((1, i), b"x" * 300)
    kept = [i for i in range(10) if cache.get((1, i)) is not None]
    assert kept == [7, 8, 9]  # the most recent that fit in 1000 bytes

    cache.put((2, 0), b"y" * 5000)  # larger than the whole budget: still kept, alone
    assert cache.get((2, 0)) is not None and cache.get((1, 9)) is None

    cache.purge(2)
    cache.put((3, 0), b"z" * 900)
    assert cache.get((3, 0)) is not None
