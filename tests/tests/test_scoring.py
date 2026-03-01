from car_watcher import compute_deal_score, compute_seller_quality, is_great_deal, Listing


def test_deal_score_rewards_discounted_price_and_low_mileage():
    listing = {"title": "2021 Mazda CX-5 Touring", "price": 22000, "mileage": 42000}
    cfg = {"target_price": 26000, "max_mileage": 60000}
    assert compute_deal_score(listing, cfg) >= 70


def test_seller_quality_penalizes_risky_terms():
    listing = {
        "title": "2017 SUV salvage rebuilt",
        "description": "sold as-is no title",
        "seller_type": "private",
    }
    score, reason = compute_seller_quality(listing)
    assert score < 40
    assert "risk" in reason


def test_is_great_deal_by_score_threshold():
    listing = Listing(
        search_name="test",
        listing_id="1",
        title="X",
        price=20000,
        mileage=10000,
        url="https://example.com",
        seller_type=None,
        seller_quality_score=80,
        seller_quality_reason="ok",
        deal_score=75,
    )
    assert is_great_deal(listing, {"min_deal_score": 70})
