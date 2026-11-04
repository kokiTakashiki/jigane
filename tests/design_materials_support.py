"""設計書の材料の試験で、同じ作業木を組む支え。"""

from pathlib import Path

FILES = {
    "shop/cart.py": """\
from shop.pricing import Pricing


class Cart:
    def checkout(self) -> int:
        return Pricing().total(self)
""",
    "shop/pricing.py": """\
class Pricing:
    def total(self, cart: object) -> int:
        return 1
""",
}


def work(tmp_path: Path) -> Path:
    for relative, text in FILES.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path
