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
