from httpkit import is_json_content_type


def test_json_with_charset_parameter() -> None:
    assert is_json_content_type("application/json; charset=utf-8")


def test_parameterized_type_is_case_insensitive() -> None:
    assert is_json_content_type("Application/JSON ; Charset=UTF-8")
