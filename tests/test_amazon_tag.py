from app.config import settings
from app.services import links, search


def test_tag_goes_on_amazon_india_only(monkeypatch):
    monkeypatch.setattr(settings, "amazon_tag", "ours-21")
    assert links.with_amazon_tag("https://www.amazon.in/dp/B0X") == "https://www.amazon.in/dp/B0X?tag=ours-21"
    assert links.with_amazon_tag("https://amazon.in/dp/B0X?th=1&tag=theirs-21") == "https://amazon.in/dp/B0X?th=1&tag=ours-21"
    for other in ("https://www.flipkart.com/p/itm1", "https://www.myntra.com/123", "https://amazon.com/dp/B0X", ""):
        assert links.with_amazon_tag(other) == other


def test_no_tag_configured_means_plain_links(monkeypatch):
    monkeypatch.setattr(settings, "amazon_tag", "")
    assert links.with_amazon_tag("https://www.amazon.in/dp/B0X") == "https://www.amazon.in/dp/B0X"


def test_cards_flag_amazon_deals_for_the_disclosure(monkeypatch):
    monkeypatch.setattr(settings, "amazon_tag", "ours-21")
    amazon = search.shape({"id": "a", "url": "https://www.amazon.in/dp/B0X?tag=theirs-21", "store": "amazon"})
    assert amazon["url"].endswith("tag=ours-21") and "tag=" not in amazon["store_url"]
    assert amazon["amazon_associate"] is True
    flipkart = search.shape({"id": "f", "url": "https://www.flipkart.com/p/itm1", "store": "flipkart"})
    assert flipkart["url"] == flipkart["store_url"] and flipkart["amazon_associate"] is False


def _rows(stores, pinned=()):
    return [{"id": f"{s}{i}", "store": s, "priority": i in pinned} for i, s in enumerate(stores)]


def test_lists_are_mostly_amazon_without_dropping_others(monkeypatch):
    monkeypatch.setattr(settings, "amazon_share", 0.8)
    rows = _rows(["flipkart"] * 5 + ["amazon"] * 20)
    out = search._favour_amazon(rows)
    assert sorted(r["id"] for r in out) == sorted(r["id"] for r in rows)
    assert [r["store"] for r in out[:10]].count("amazon") == 8
    # each store keeps its own order
    assert [r["id"] for r in out if r["store"] == "flipkart"] == [f"flipkart{i}" for i in range(5)]


def test_pinned_deals_stay_first_and_off_switch(monkeypatch):
    monkeypatch.setattr(settings, "amazon_share", 0.8)
    rows = _rows(["myntra", "amazon", "amazon"], pinned=(0,))
    assert search._favour_amazon(rows)[0]["id"] == "myntra0"
    monkeypatch.setattr(settings, "amazon_share", 0.0)
    rows = _rows(["flipkart", "amazon"])
    assert search._favour_amazon(rows) == rows


def test_amazon_product_posts_are_kept_when_wording_looks_like_a_roundup():
    from app.services import quality
    base = {"price": 499, "image_url": "https://img.example/a.jpg"}
    amazon = {**base, "title": "Upto 70% off Shoes", "raw_text": "Upto 70% off Shoes https://www.amazon.in/dp/B0ABCDEFGH",
              "url": "https://www.amazon.in/dp/B0ABCDEFGH"}
    assert quality.text_reason(amazon) is None and quality.reject_reason(amazon) is None
    flipkart = {**base, "title": "Upto 70% off Shoes", "raw_text": "Upto 70% off Shoes https://www.flipkart.com/x/p/itm123",
                "url": "https://www.flipkart.com/x/p/itm123"}
    assert quality.reject_reason(flipkart) == "roundup"
    # still turned away on Amazon: a search page, a promo, no price
    search_page = {**amazon, "url": "https://www.amazon.in/s?k=shoes",
                   "raw_text": "Upto 70% off Shoes https://www.amazon.in/s?k=shoes"}
    assert quality.reject_reason(search_page) == "roundup"
    assert quality.reject_reason({**amazon, "title": "Amazon Pay Gift Card Voucher"}) == "promo"
    assert quality.reject_reason({**amazon, "price": None}) == "no_price"
