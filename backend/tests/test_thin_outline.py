from app.services.geometry import thin_outline


def test_drops_points_on_a_straight_run_and_rounds_noise():
    ring = [[0, 0]] + [[i * 0.1, 45352.979999999996] for i in range(1, 100)] + [[10, 10], [0, 10]]
    out = thin_outline("polygon", ring)
    assert len(out) < 20
    assert out[0] == [0, 0] and out[-1] == [0, 10]
    assert all(round(v, 2) == v for p in out for v in p)


def test_leaves_simple_shapes_alone():
    rect = [[0, 0], [5, 5]]
    assert thin_outline("rectangle", rect) is rect
    circle = [[0, 0], [0.1, 0], [0.2, 0], [0.3, 0], [0.4, 0]]
    assert thin_outline("circle", circle) is circle


def test_keeps_a_tiny_outline_whole():
    tiny = [[0, 0], [0.1, 0], [0.2, 0.1], [0.1, 0.2], [0, 0.1]]
    assert thin_outline("polygon", tiny) == tiny
