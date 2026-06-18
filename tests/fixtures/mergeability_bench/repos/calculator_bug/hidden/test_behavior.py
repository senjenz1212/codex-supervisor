from app.calculator import add


def test_adds_positive_integers():
    assert add(2, 3) == 5


def test_adds_negative_integers():
    assert add(-4, -6) == -10
