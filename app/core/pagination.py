from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ValidationError


@dataclass(frozen=True, slots=True)
class Page:
    number: int = 1
    size: int = 20

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValidationError("页码必须从 1 开始")
        if not 1 <= self.size <= 100:
            raise ValidationError("每页数量必须在 1 到 100 之间")

    @property
    def offset(self) -> int:
        return (self.number - 1) * self.size


def page_result(*, total: int, page: Page, rows: list[dict]) -> dict:
    return {
        "total": total,
        "page": page.number,
        "size": page.size,
        "pages": (total + page.size - 1) // page.size,
        "data": rows,
    }
