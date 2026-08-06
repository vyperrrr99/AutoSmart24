from pathlib import Path

from autosmart24.scraping.next_data import extract_next_data
from autosmart24.scraping.detail_mapper import extract_dealer, map_detail_listing, _parse_weight_kg

FIXTURES = Path(__file__).parent / "fixtures"


def _listing_details() -> dict:
    html = (FIXTURES / "detail_fiat_grande_panda.html").read_text(encoding="utf-8")
    data = extract_next_data(html)
    return data["props"]["pageProps"]["listingDetails"]


def test_map_detail_listing_extracts_full_fields():
    ld = _listing_details()
    mapped = map_detail_listing(ld)

    assert mapped["id"] == ld["id"]
    assert mapped["brand"] == "Fiat"
    assert mapped["model"] == "Grande Panda"
    assert mapped["price"] == 13990
    assert mapped["power_kw"] == 74
    assert mapped["power_cv"] == 101
    assert mapped["displacement_ccm"] == 1199
    assert mapped["body_type"] == "Berlina"
    assert mapped["num_seats"] == 5
    assert mapped["province"] == "TO"
    assert mapped["seller_type"] == "Dealer"
    assert mapped["source_status"] == "Active"
    assert mapped["first_registration"].isoformat() == "2026-04-01"
    assert mapped["created_at_source"].year == 2026
    assert mapped["url"].startswith("https://www.autoscout24.it/annunci/")


def test_map_detail_listing_handles_missing_city_gracefully():
    ld = {
        "id": "zzz",
        "identifier": {},
        "vehicle": {},
        "location": {},
        "seller": {},
        "prices": {},
        "price": {},
        "webPage": "https://www.autoscout24.it/annunci/zzz",
        "status": "Active",
        "createdTimestampWithOffset": None,
    }
    mapped = map_detail_listing(ld)

    assert mapped["city"] is None
    assert mapped["province"] is None
    assert mapped["created_at_source"] is None
    assert mapped["dealer"] is None


def test_map_detail_listing_keeps_full_province_name_when_no_short_sigla_segment():
    """For provincial-capital listings the site sometimes omits a separate
    short "sigla" segment, so the last "Comune - Provincia - Sigla" part is
    a full province name (e.g. "Campobasso", 10 chars) rather than a 2-letter
    abbreviation. This crashed a production sweep against the old
    VARCHAR(8) province column (widened to VARCHAR(64) to fix it). The
    mapper itself must keep taking parts[-1] verbatim, without truncating or
    rejecting values longer than 8 chars -- that's now a legitimate value."""
    ld = {
        "id": "zzz2",
        "identifier": {},
        "vehicle": {},
        "location": {"city": "Campobasso - Campobasso - Campobasso"},
        "seller": {},
        "prices": {},
        "price": {},
        "webPage": "https://www.autoscout24.it/annunci/zzz2",
        "status": "Active",
        "createdTimestampWithOffset": None,
    }
    mapped = map_detail_listing(ld)

    assert mapped["province"] == "Campobasso"
    assert len(mapped["province"]) > 8


def test_map_detail_listing_extracts_new_structured_fields():
    ld = _listing_details()
    mapped = map_detail_listing(ld)

    assert mapped["had_accident"] is False
    assert mapped["has_full_service_history"] is False
    assert mapped["gears"] == 6
    assert mapped["drive_train"] == "Anteriore"
    assert mapped["cylinders"] == 3
    assert mapped["weight_kg"] == 1159
    assert mapped["co2_emissions_g_km"] is None
    assert mapped["fuel_consumption_combined"] is None
    assert mapped["fuel_consumption_urban"] is None
    assert mapped["fuel_consumption_extra_urban"] is None
    assert mapped["emission_class"] == "Euro 6d"
    assert mapped["upholstery"] == "Altro"
    assert mapped["upholstery_color"] is None
    assert mapped["is_conditional_price"] is True
    assert mapped["interaction_count"] == 10670
    assert mapped["favorites_count"] == 193
    assert mapped["new_driver_suitable"] is True


def test_map_detail_listing_extracts_dealer_info_for_a_dealer_seller():
    ld = _listing_details()
    mapped = map_detail_listing(ld)

    assert mapped["dealer"] == {
        "id": 46936034,
        "company_name": "Puntocar di Tarantino Andrea - Bricherasio",
        "ratings_stars": 5,
        "ratings_count": 25,
        "recommend_percentage": 92,
    }


def test_map_detail_listing_dealer_is_none_for_missing_seller_info():
    ld = {
        "id": "zzz",
        "identifier": {},
        "vehicle": {},
        "location": {},
        "seller": {},
        "prices": {},
        "price": {},
        "webPage": "https://www.autoscout24.it/annunci/zzz",
        "status": "Active",
        "createdTimestampWithOffset": None,
    }
    mapped = map_detail_listing(ld)

    assert mapped["dealer"] is None
    assert mapped["had_accident"] is None
    assert mapped["gears"] is None
    assert mapped["is_conditional_price"] is None


def test_extract_dealer_returns_none_for_private_seller():
    ld = {"seller": {"type": "Private", "isDealer": False}}
    assert extract_dealer(ld) is None


def test_extract_dealer_returns_none_when_dealer_has_no_id():
    ld = {"seller": {"isDealer": True}}
    assert extract_dealer(ld) is None


def test_extract_dealer_extracts_ratings_for_a_real_dealer():
    ld = {
        "seller": {"id": 999, "isDealer": True, "companyName": "Auto Test Srl"},
        "ratings": {"ratingsStars": 4.5, "ratingsCount": 30, "recommendPercentage": 88},
    }
    assert extract_dealer(ld) == {
        "id": 999,
        "company_name": "Auto Test Srl",
        "ratings_stars": 4.5,
        "ratings_count": 30,
        "recommend_percentage": 88,
    }


def test_parse_weight_kg_handles_thousands_separator_and_none():
    assert _parse_weight_kg("1.159 kg") == 1159
    assert _parse_weight_kg("800 kg") == 800
    assert _parse_weight_kg(None) is None
    assert _parse_weight_kg("") is None


def test_map_detail_listing_extracts_paint_and_equipment():
    """The four colour fields AutoScout publishes are not interchangeable.

    `bodyColorRaw` is the generic English name we already stored; the finish
    and the manufacturer's own name for it are separate fields, and on a
    special paint they are what the colour is actually worth.
    """
    ld = _listing_details()
    ld["vehicle"].update({
        "bodyColorRaw": "Green",
        "bodyColor": "Verde",
        "bodyColorOriginal": "Verde Salvia Metallizzato",
        "paintType": "Metallizzato",
        "equipment": {
            "comfortAndConvenience": [
                {"id": "Tettuccio apribile"}, {"id": "Volante in pelle"},
            ],
            "extras": [{"id": 'Cerchi in lega (19")'}],
            "safetyAndSecurity": [{"id": "Fari full-LED"}],
        },
    })

    mapped = map_detail_listing(ld)

    assert mapped["body_color"] == "Green", "il campo esistente non cambia significato"
    assert mapped["paint_type"] == "Metallizzato"
    assert mapped["body_color_original"] == "Verde Salvia Metallizzato"
    assert mapped["equipment"] == [
        'Cerchi in lega (19")', "Fari full-LED", "Tettuccio apribile", "Volante in pelle",
    ]
    assert mapped["has_sunroof"] is True
    assert mapped["has_full_led_headlights"] is True
    assert mapped["has_alloy_wheels"] is True, "solo la misura, senza l'etichetta semplice"
    assert mapped["has_leather_interior"] is False, "il volante in pelle non sono gli interni"
    assert mapped["has_led_headlights"] is False, "full-LED non implica LED"


def test_map_detail_listing_leaves_equipment_unknown_when_absent():
    """Una pagina senza blocco equipaggiamenti non dice "nessun optional":
    non dice nulla. False qui sarebbe un fatto mai osservato."""
    ld = _listing_details()
    ld["vehicle"].pop("equipment", None)

    mapped = map_detail_listing(ld)

    assert mapped["equipment"] is None
    assert mapped["has_sunroof"] is None
    assert mapped["has_alloy_wheels"] is None
