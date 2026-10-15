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
    myntra = search.shape({"id": "m", "url": "https://www.myntra.com/kurta/brand/x/12345678/buy", "store": "myntra"})
    assert myntra["url"] == myntra["store_url"] and myntra["amazon_associate"] is False


def _rows(stores, pinned=()):
    return [{"id": f"{s}{i}", "store": s, "priority": i in pinned} for i, s in enumerate(stores)]


def test_lists_are_mostly_amazon_then_flipkart_without_dropping_others(monkeypatch):
    monkeypatch.setattr(settings, "amazon_share", 0.6)
    monkeypatch.setattr(settings, "flipkart_share", 0.3)
    rows = _rows(["myntra"] * 10 + ["flipkart"] * 20 + ["amazon"] * 30)
    out = search._favour_stores(rows)
    assert sorted(r["id"] for r in out) == sorted(r["id"] for r in rows)
    first = [r["store"] for r in out[:10]]
    assert (first.count("amazon"), first.count("flipkart"), first.count("myntra")) == (6, 3, 1)
    # each store keeps its own order
    assert [r["id"] for r in out if r["store"] == "myntra"] == [f"myntra{i}" for i in range(10)]


def test_pinned_deals_stay_first_and_off_switch(monkeypatch):
    monkeypatch.setattr(settings, "amazon_share", 0.6)
    monkeypatch.setattr(settings, "flipkart_share", 0.3)
    rows = _rows(["myntra", "amazon", "amazon"], pinned=(0,))
    assert search._favour_stores(rows)[0]["id"] == "myntra0"
    monkeypatch.setattr(settings, "amazon_share", 0.0)
    monkeypatch.setattr(settings, "flipkart_share", 0.0)
    rows = _rows(["flipkart", "amazon"])
    assert search._favour_stores(rows) == rows


def test_flipkart_goes_through_earnkaro_with_our_id(monkeypatch):
    monkeypatch.setattr(settings, "earnkaro_id", "5701797")
    monkeypatch.setattr(settings, "earnkaro_flipkart_retailer", "2276")
    plain = "https://www.flipkart.com/brawo-women-flip-flops/p/itmacffb5702210e?pid=SFFHPBW4SHY2NCGN"
    link = links.with_affiliate(plain)
    assert link.startswith("https://trackingv3.linkredirect.in/visitretailer/2276?id=5701797&dl=")
    assert links.unwrap(link) == plain          # the tracker names the same Flipkart page
    for other in ("https://www.myntra.com/123", "https://www.amazon.in/dp/B0X?tag=x"):
        assert "linkredirect" not in links.with_affiliate(other)
    monkeypatch.setattr(settings, "earnkaro_id", "")
    assert links.with_affiliate(plain) == plain


def test_flipkart_cards_keep_the_tracker_link_and_no_amazon_disclosure(monkeypatch):
    monkeypatch.setattr(settings, "earnkaro_id", "5701797")
    card = search.shape({"id": "f", "store": "flipkart",
                         "url": "https://www.flipkart.com/x/p/itm1?pid=A"})
    assert card["url"].startswith("https://trackingv3.linkredirect.in/visitretailer/2276?id=5701797")
    assert card["store_url"] == "https://www.flipkart.com/x/p/itm1?pid=A"
    assert card["amazon_associate"] is False


def test_amazon_product_posts_are_kept_when_wording_looks_like_a_roundup():
    from app.services import quality
    base = {"price": 499, "image_url": "https://img.example/a.jpg"}
    amazon = {**base, "title": "Upto 70% off Shoes", "raw_text": "Upto 70% off Shoes https://www.amazon.in/dp/B0ABCDEFGH",
              "url": "https://www.amazon.in/dp/B0ABCDEFGH"}
    assert quality.text_reason(amazon) is None and quality.reject_reason(amazon) is None
    flipkart = {**base, "title": "Upto 70% off Shoes", "raw_text": "Upto 70% off Shoes https://www.flipkart.com/x/p/itm123",
                "url": "https://www.flipkart.com/x/p/itm123"}
    assert quality.reject_reason(flipkart) is None     # Flipkart product pages get the same benefit of the doubt
    myntra = {**base, "title": "Upto 70% off Shoes", "raw_text": "Upto 70% off Shoes https://www.myntra.com/shoes/brand/x/12345678/buy",
              "url": "https://www.myntra.com/shoes/brand/x/12345678/buy"}
    assert quality.reject_reason(myntra) == "roundup"  # other stores keep the strict gate
    # still turned away on Amazon: a search page, a promo, no price
    search_page = {**amazon, "url": "https://www.amazon.in/s?k=shoes",
                   "raw_text": "Upto 70% off Shoes https://www.amazon.in/s?k=shoes"}
    assert quality.reject_reason(search_page) == "roundup"
    assert quality.reject_reason({**amazon, "title": "Amazon Pay Gift Card Voucher"}) == "promo"
    assert quality.reject_reason({**amazon, "price": None}) == "no_price"
