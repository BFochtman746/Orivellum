from mathutil import add


def test_add_integers():
    assert add(1, 2) == 3


def test_add_floats():
    assert add(1.5, 2.5) == 4.0


def test_add_negative():
    assert add(-1, 1) == 0
