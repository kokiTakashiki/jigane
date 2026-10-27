def required[T](value: T | None) -> T:
    """None でないと呼び出し側が保証している値を取り出し、型を絞る。"""
    if value is None:
        raise AssertionError("None でないはずの値が None でした")
    return value
