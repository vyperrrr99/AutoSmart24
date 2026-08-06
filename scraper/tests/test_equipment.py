"""The options a car expert says move price, read out of AutoScout's list.

Every case here is taken from the real catalogue, not invented: the labels are
the ones the site actually publishes, including the two shapes that defeat a
naive match.
"""
from __future__ import annotations

from autosmart24.equipment import COLUMNS, derive, extract


def _vehicle(*voci):
    return {"equipment": {"comfortAndConvenience": [{"id": v} for v in voci]}}


def test_the_plain_label_is_found():
    assert derive(["Tettuccio apribile"])["has_sunroof"] is True


def test_alloy_wheels_are_found_when_only_the_rim_size_is_published():
    """95 of the 357 cars in the sample carried ONLY the sized label. Matching
    on equality alone silently loses more than a quarter of them."""
    assert derive(['Cerchi in lega (17")'])["has_alloy_wheels"] is True
    assert derive(['Cerchi in lega (20")'])["has_alloy_wheels"] is True
    assert derive(["Cerchi in lega"])["has_alloy_wheels"] is True


def test_steel_wheels_are_not_alloy():
    """The prefix must not be so loose that it swallows the opposite fact."""
    assert derive(["Cerchioni in acciaio"])["has_alloy_wheels"] is False


def test_led_and_full_led_are_independent():
    """266 cars had "Fari LED", 173 had "Fari full-LED", only 128 both. Neither
    label implies the other, so neither may be derived from the other."""
    solo_full = derive(["Fari full-LED"])
    assert solo_full["has_full_led_headlights"] is True
    assert solo_full["has_led_headlights"] is False

    solo_led = derive(["Fari LED"])
    assert solo_led["has_led_headlights"] is True
    assert solo_led["has_full_led_headlights"] is False


def test_daytime_led_lights_are_neither():
    """"Luci diurne LED" is a third, much cheaper option on 28% of cars."""
    d = derive(["Luci diurne LED"])
    assert d["has_led_headlights"] is False
    assert d["has_full_led_headlights"] is False


def test_leather_steering_wheel_is_not_a_leather_interior():
    """"Volante in pelle" is on 56% of cars and worth nothing; "Interni in
    pelle" is on 13% and is one of the strongest signals in the catalogue."""
    assert derive(["Volante in pelle"])["has_leather_interior"] is False


def test_heated_steering_wheel_is_not_heated_seats():
    assert derive(["Volante riscaldato"])["has_heated_seats"] is False


def test_rear_seat_adjustment_is_not_the_front_one():
    """"Regolazione elettrica del sedile posteriore" is a different option."""
    assert derive(["Regolazione elettrica del sedile posteriore"])["has_electric_seats"] is False


def test_park_distance_control_is_not_a_camera():
    """Sensors are on 78% of cars, the camera on 46%. Conflating them would
    make the more valuable option look nearly universal."""
    assert derive(["Park Distance Control"])["has_parking_camera"] is False


def test_a_car_never_enriched_is_unknown_not_unequipped():
    """The distinction the price model depends on: no page was ever read, so
    nothing can be said. False here would be a fact we never observed."""
    d = derive(None)
    assert set(d) == set(COLUMNS)
    assert all(v is None for v in d.values())


def test_an_empty_list_means_the_seller_listed_nothing():
    """24 of 487 sampled cars carried an equipment block with no items. That
    is an answer, not a gap."""
    d = derive([])
    assert all(v is False for v in d.values())


def test_every_option_is_reported_every_time():
    d = derive(["Tettuccio apribile"])
    assert set(d) == set(COLUMNS)
    assert len(COLUMNS) == 9


# --- lettura dal JSON grezzo ------------------------------------------------

def test_extract_flattens_the_four_categories():
    v = {"equipment": {
        "comfortAndConvenience": [{"id": "Bracciolo"}],
        "safetyAndSecurity": [{"id": "Fari LED"}],
        "extras": [{"id": "Cerchi in lega"}],
        "entertainmentAndMedia": [{"id": "Bluetooth"}],
    }}
    assert extract(v) == ["Bluetooth", "Bracciolo", "Cerchi in lega", "Fari LED"]


def test_extract_returns_none_when_the_block_is_absent():
    assert extract({}) is None
    assert extract({"equipment": None}) is None


def test_extract_returns_empty_when_the_block_is_present_but_bare():
    assert extract({"equipment": {"comfortAndConvenience": []}}) == []


def test_extract_survives_malformed_items():
    v = {"equipment": {"extras": [{"id": "Cerchi in lega"}, {}, "spazzatura", {"id": None}]}}
    assert extract(v) == ["Cerchi in lega"]


def test_extract_deduplicates():
    """The same option can appear in two categories."""
    v = {"equipment": {"comfortAndConvenience": [{"id": "Fari LED"}],
                       "safetyAndSecurity": [{"id": "Fari LED"}]}}
    assert extract(v) == ["Fari LED"]


def test_extraction_and_derivation_agree_end_to_end():
    d = derive(extract(_vehicle("Tettuccio apribile", 'Cerchi in lega (19")')))
    assert d["has_sunroof"] is True
    assert d["has_alloy_wheels"] is True
    assert d["has_panoramic_roof"] is False
