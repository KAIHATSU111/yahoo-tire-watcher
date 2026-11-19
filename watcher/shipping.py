from dataclasses import dataclass
from typing import Literal


def yen(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass(frozen=True)
class ShippingQuote:
    """A confirmed rate for the lot and destination, with an explicit billing unit."""

    prefecture: str
    amount: int
    unit: Literal["lot", "tire", "parcel"]
    parcel_count: int | None = None

    def total(self, prefecture: str, tire_count: int) -> int | None:
        if self.prefecture != prefecture or yen(self.amount) is None or type(tire_count) is not int or tire_count < 1:
            return None
        if self.unit == "lot":
            return self.amount
        if self.unit == "tire":
            return self.amount * tire_count
        if self.unit == "parcel" and type(self.parcel_count) is int and self.parcel_count > 0:
            return self.amount * self.parcel_count
        return None
