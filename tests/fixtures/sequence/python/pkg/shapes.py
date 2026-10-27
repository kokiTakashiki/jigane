from abc import ABCMeta
from dataclasses import dataclass
from typing import final


def make(kind: str, size: int = 1) -> "Canvas":  # noqa: ARG001 - 呼び出しの図の試験が引数の並びを見る資料で、使う必要が無い
    return Canvas(kind)


@dataclass
class Point:
    x: int
    y: int = 0


class Base:
    pass


class Canvas(Base, metaclass=ABCMeta):
    label: str = "c"
    count = 0

    def __init__(self, kind: str, scale: int = 1) -> None:
        self.kind = kind
        self.items: list[str] = []
        if scale:
            self.scale = scale

    @property
    def title(self) -> str:
        return self.kind

    @title.setter
    def title(self, value: str) -> None:
        self.kind = value

    @staticmethod
    def build(kind: str) -> "Canvas":
        return Canvas(kind)

    @classmethod
    def default(cls) -> "Canvas":
        return cls("d")

    @final
    def draw(self, target: Base, *args: int, **kwargs: int) -> None:
        pass

    class Inner:
        def run(self) -> int:
            return 1
