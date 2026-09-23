"""Clicks through the DealRadar website like a user, on phone, tablet and desktop.

Run:  .venv/bin/python -m pytest tests/e2e -v            (Chrome opens on screen)
      E2E_HEADLESS=1 .venv/bin/python -m pytest tests/e2e (no window)
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import DESKTOP, PHONE, TABLET

VIEWPORTS = [pytest.param(PHONE, id="phone"), pytest.param(TABLET, id="tablet"),
             pytest.param(DESKTOP, id="desktop")]


def deals_loaded(page, at_least=20):
    expect(page.locator("#deal-grid .deal").nth(at_least - 1)).to_be_visible()


def search_input(page):
    return page.locator("#search-input")


# ---------------------------------------------------------------- login
@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_visitors_land_on_deals_without_login(open_page, viewport):
    page, errors = open_page(viewport, signed_in=False)
    deals_loaded(page)
    expect(page.locator("#view-login")).to_be_hidden()
    expect(page.locator('[data-nav="channels"]').first).to_be_hidden()
    assert not errors, errors


# ---------------------------------------------------------------- home
@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_home_layout(open_page, viewport):
    page, errors = open_page(viewport)
    deals_loaded(page)
    expect(page.locator(".vh-stats b").first).to_have_text("25")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"horizontal overflow of {overflow}px"
    if viewport["width"] >= 1024:
        expect(page.locator(".topnav")).to_be_hidden()
        expect(page.locator("#bottomnav")).to_be_hidden()
        expect(page.locator("#filters")).to_be_visible()
    else:
        expect(page.locator("#bottomnav")).to_be_visible()
        expect(page.locator("#btn-filters-toggle")).to_be_visible()
    columns = page.evaluate("getComputedStyle(document.getElementById('deal-grid')).gridTemplateColumns.split(' ').length")
    assert columns >= 2
    assert not errors, errors


# ---------------------------------------------------------------- search
def test_instant_search_tolerates_typos(open_page):
    page, _ = open_page(DESKTOP)
    deals_loaded(page)
    search_input(page).click()
    search_input(page).press_sequentially("headphnes", delay=40)
    option = page.locator('#suggest [data-kind="deal"]').first
    expect(option).to_contain_text("Rockerz")
    expect(search_input(page)).to_have_attribute("aria-expanded", "true")


def test_search_keyboard_opens_deal(open_page):
    page, _ = open_page(DESKTOP)
    deals_loaded(page)
    search_input(page).click()
    search_input(page).press_sequentially("shoes", delay=40)
    expect(page.locator('#suggest [data-kind="deal"]').first).to_be_visible()
    deal_id = page.locator('#suggest [data-kind="deal"]').first.get_attribute("data-value")
    while page.evaluate("document.getElementById(document.getElementById('search-input').getAttribute('aria-activedescendant')||'x')?.dataset.kind") != "deal":
        search_input(page).press("ArrowDown")
    search_input(page).press("Enter")
    title = page.request.get(f"/api/deals/{deal_id}").json()["title"]
    expect(page.locator("#modal-body h2")).to_have_text(title)
    page.keyboard.press("Escape")
    expect(page.locator("#modal")).to_be_hidden()


def test_search_results_are_grouped_by_category(open_page):
    page, _ = open_page(DESKTOP)
    deals_loaded(page)
    search_input(page).fill("pack")
    search_input(page).press("Enter")
    expect(page.locator("#results-summary")).to_contain_text("4 deals")
    expect(page.locator("#deal-grid mark").first).to_have_text(re.compile("pack", re.I))
    chips = page.locator("#result-cats button")
    expect(chips.first).to_be_visible()
    assert chips.count() >= 3
    chips.nth(1).click()
    expect(page.locator("#results-summary")).not_to_contain_text("4 deals")
    assert page.locator("#deal-grid .deal").count() < 4


def test_clearing_search_returns_to_browsing(open_page):
    page, _ = open_page(PHONE)
    deals_loaded(page)
    search_input(page).fill("kurta")
    search_input(page).press("Enter")
    expect(page.locator("#deal-grid .deal")).to_have_count(1)
    page.locator("#search-clear").click()
    deals_loaded(page)


def test_range_prices_are_labelled(open_page):
    page, _ = open_page(PHONE)
    deals_loaded(page)
    search_input(page).fill("peter england")
    search_input(page).press("Enter")
    card = page.locator("#deal-grid .deal").first
    expect(card.locator(".price-now")).to_have_text(re.compile(r"From\s*₹509"))
    expect(card.locator(".price-off")).to_have_text("Up to 70%")


# ---------------------------------------------------------------- browse controls
def test_sort_tabs_and_view_toggle(open_page):
    page, _ = open_page(DESKTOP)
    deals_loaded(page)
    page.locator('#sort-tabs [data-sort="discount"]').click()
    expect(page.locator("#f-sort")).to_have_value("discount")
    expect(page.locator("#grid-title")).to_contain_text("Biggest discounts")
    offs = [int(re.sub(r"\D", "", t)) for t in page.locator("#deal-grid .price-off").all_inner_texts()[:6]]
    assert offs == sorted(offs, reverse=True), offs

    page.locator('.viewtoggle [data-view="list"]').click()
    expect(page.locator("#deal-grid")).to_have_class(re.compile(r"\blist\b"))
    page.reload()
    page.wait_for_selector("#boot", state="detached")
    expect(page.locator("#deal-grid")).to_have_class(re.compile(r"\blist\b"))
    page.locator('.viewtoggle [data-view="grid"]').click()


def test_filter_sheet_on_phone(open_page):
    page, _ = open_page(PHONE)
    deals_loaded(page)
    page.locator("#btn-filters-toggle").click()
    expect(page.locator("#filters")).to_have_class(re.compile(r"\bopen\b"))
    page.locator("#f-stores .facet").first.click()
    expect(page.locator("#btn-filters-apply")).to_have_text(re.compile(r"Show \d+ deals"))
    assert "24" not in page.locator("#btn-filters-apply").inner_text()
    page.locator("#btn-filters-apply").click()
    expect(page.locator("#filters")).not_to_have_class(re.compile(r"\bopen\b"))
    expect(page.locator("#filters-badge")).to_have_text("1")
    expect(page.locator("#active-filters .chip")).to_have_count(1)
    page.locator("#active-filters .chip").click()
    deals_loaded(page)


# ---------------------------------------------------------------- deal detail
@pytest.mark.parametrize("viewport", [pytest.param(PHONE, id="phone"), pytest.param(DESKTOP, id="desktop")])
def test_deal_detail_and_deep_link(open_page, server, viewport):
    page, errors = open_page(viewport)
    deal = page.request.get("/api/deals?q=shoes").json()["results"][0]
    page.goto(f"/?deal={deal['id']}")
    expect(page.locator("#modal-body h2")).to_have_text(deal["title"])
    expect(page.locator(".price-chart svg")).to_be_visible()
    expect(page.locator(".detail-cta a.btn-primary")).to_have_attribute("href", re.compile("flipkart|myntra|amazon"))
    expect(page.locator("#btn-price-history")).to_have_attribute("href", re.compile(r"^https://buyhatke\.com/https://"))
    assert "deal=" not in page.url
    page.keyboard.press("Escape")
    expect(page.locator("#modal")).to_be_hidden()
    assert not errors, errors


# ---------------------------------------------------------------- theme
def test_dark_mode_follows_the_system(open_page):
    page, _ = open_page(PHONE, color_scheme="dark")
    deals_loaded(page)
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(8, 11, 18)", bg
