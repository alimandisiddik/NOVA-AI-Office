from app.security import is_authorized_user


def test_configured_user_is_authorized() -> None:
    assert is_authorized_user(123456789, 123456789)


def test_other_user_is_unauthorized() -> None:
    assert not is_authorized_user(987654321, 123456789)
