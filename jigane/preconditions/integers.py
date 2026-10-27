def non_negative_int(value: object) -> int | None:
    """0 以上の整数ならそのまま返し、そうでなければ None を返す。"""
    # bool は int の下位型なので、True を 1、False を 0 と読まないよう先に外す
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def positive_int(value: object) -> int | None:
    """1 以上の整数ならそのまま返し、そうでなければ None を返す。行番号や番号に使う。"""
    number = non_negative_int(value)
    return number if number is not None and number >= 1 else None
