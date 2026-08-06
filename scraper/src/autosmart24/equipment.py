"""Turns AutoScout's equipment list into the handful of options that move price.

A car expert picked these from the catalogue of 142 options seen across the
market. They are stored as columns because the BI regresses price on them; the
full list is kept alongside, raw, because `raw_detail` was dropped in migration
0006 and re-deriving anything from the site costs a full re-scrape -- at the
measured detail rate, days of it, and sold listings can never be re-read at
all. Keeping the raw list makes a tenth option a migration instead.

Two traps live in the source data, which is why matching happens here and not
in the BI's queries:

- Alloy wheels appear either as "Cerchi in lega" or with the rim size --
  "Cerchi in lega (17\\")" -- and 95 of the 357 cars in the sample carried ONLY
  the sized label. Equality alone loses more than a quarter of them.
- "Fari LED" and "Fari full-LED" are independent labels, not a hierarchy: of
  the sample, 266 had the first, 173 the second, and only 128 both. Neither
  implies the other, so both are stored and neither is derived.

Absent is not the same as unknown. A listing whose detail page was never read
has no equipment list, and every option is None -- not False. Saying "no
sunroof" about a car nobody looked at would be indistinguishable, downstream,
from having looked.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Option:
    """`labels` match exactly; `prefixes` match the start of a label."""

    column: str
    labels: tuple[str, ...]
    prefixes: tuple[str, ...] = field(default=())

    def present_in(self, equipment: list[str]) -> bool:
        if any(lab in equipment for lab in self.labels):
            return True
        return any(e.startswith(p) for p in self.prefixes for e in equipment)


OPTIONS: tuple[Option, ...] = (
    Option("has_sunroof", ("Tettuccio apribile",)),
    Option("has_panoramic_roof", ("Tetto panoramico",)),
    Option("has_leather_interior", ("Interni in pelle",)),
    Option("has_heated_seats", ("Sedili riscaldati",)),
    Option("has_electric_seats", ("Regolazione elettrica sedili",)),
    Option("has_parking_camera", ("Telecamera per parcheggio assistito",)),
    Option("has_full_led_headlights", ("Fari full-LED",)),
    Option("has_led_headlights", ("Fari LED",)),
    # The sized variants are the reason this option needs a prefix at all.
    Option("has_alloy_wheels", ("Cerchi in lega",), ("Cerchi in lega (",)),
)

COLUMNS: tuple[str, ...] = tuple(o.column for o in OPTIONS)


def extract(vehicle: dict) -> list[str] | None:
    """Flatten AutoScout's four equipment categories into one sorted list.

    None means the page carried no equipment block at all. An empty list means
    it carried one and the seller listed nothing -- 24 of 487 sampled cars, a
    real answer rather than a missing one.
    """
    block = vehicle.get("equipment")
    if not isinstance(block, dict):
        return None
    found = {
        item["id"]
        for category in block.values()
        if isinstance(category, list)
        for item in category
        if isinstance(item, dict) and item.get("id")
    }
    return sorted(found)


def derive(equipment: list[str] | None) -> dict[str, bool | None]:
    """One boolean per option, or all None when nothing was ever read."""
    if equipment is None:
        return {o.column: None for o in OPTIONS}
    return {o.column: o.present_in(equipment) for o in OPTIONS}
