"""Seed a throwaway DB with a demo user, channels, deals, history and alerts.

Run with DB_PATH and SECRET_KEY set (never against a real DB); prints a
session cookie line `COOKIE <value>` for the signed-in demo user.
"""
import os
import random
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import auth, db  # noqa: E402
from app.services import parser, store  # noqa: E402

random.seed(7)
db.connect()
now = time.time()

TG_USER = 555001
db.upsert("users", {
    "telegram_id": TG_USER, "username": "tanush", "first_name": "Tanush",
    "phone": "+910000000000", "session_enc": "", "created_at": now, "last_login_at": now,
}, conflict="telegram_id")
uid = db.query_one("SELECT id FROM users WHERE telegram_id=?", (TG_USER,))["id"]

CHANNELS = [
    (9001, "lootdealsindia", "Loot Deals India", 182000),
    (9002, "amazonloot", "Amazon Loot Offers", 96500),
    (9003, "fkdeals", "Flipkart Deals Hub", 74200),
    (9004, "fashionloot", "Fashion Loot Zone", 51800),
    (9005, "techdealz", "Tech Dealz", 38900),
]
for tg, un, title, members in CHANNELS:
    db.upsert("channels", {"tg_id": tg, "username": un, "title": title, "participants": members,
                           "last_message_id": 100, "last_fetched_at": now, "source_user_id": uid,
                           "active": 1}, conflict="tg_id")
    cid = db.query_one("SELECT id FROM channels WHERE tg_id=?", (tg,))["id"]
    db.execute("INSERT OR REPLACE INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?,?,1,?)",
               (uid, cid, now))

COLORS = ["#2563eb", "#db2777", "#059669", "#d97706", "#7c3aed", "#0891b2", "#dc2626", "#4b5563"]


def img(label, i):
    c = COLORS[i % len(COLORS)]
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 {300 + (i % 3) * 100}'>"
           f"<rect width='100%' height='100%' fill='{c}'/>"
           f"<text x='50%' y='50%' font-family='Arial' font-size='34' fill='white' text-anchor='middle'>"
           f"{label}</text></svg>")
    return "data:image/svg+xml;utf8," + quote(svg)


POSTS = [
    ("boAt Rockerz 450 Bluetooth On Ear Headphones with Mic\n₹1,299 (MRP ₹2,990) 56% OFF\nhttps://www.amazon.in/dp/B07PR1CL3S", "boAt", 1),
    ("Anouk Women Printed Anarkali Kurta with Dupatta\n₹649 ₹2,199 (70% off)\nSize: S, M, L, XL\nhttps://www.myntra.com/kurtas/anouk/x/12345678/buy", "Kurta", 4),
    ("Ambrane 20000mAh Power Bank 20W Fast Charging\nDeal Price: Rs. 999\nMRP: Rs 2499\nUse code SAVE100\nhttps://www.flipkart.com/p/itm1?pid=ACCFXYZ123", "Ambrane", 3),
    ("Campus Men's Running Sports Shoes\n@899 only (was 1999)\nhttps://www.flipkart.com/p/itmshoe?pid=SHOABCD123", "Campus", 3),
    ("Prestige Omega Deluxe Granite Non Stick Kadai 240mm\n₹749 (MRP ₹1,895) 60% off\nhttps://www.amazon.in/dp/B08XYZ1234", "Prestige", 2),
    ("Mamaearth Vitamin C Face Wash 100ml\n₹199 only\nhttps://www.nykaa.com/mamaearth/p/998877", "Mamaearth", 4),
    ("Samsung Galaxy M35 5G (Moonlight Blue, 8GB RAM, 128GB Storage)\n₹14,999 MRP ₹24,499 39% off\nhttps://www.amazon.in/dp/B0D2M35ABC", "Samsung", 5),
    ("Noise ColorFit Pro 5 Smart Watch AMOLED\n₹2,799 MRP ₹6,999\nhttps://www.amazon.in/dp/B0CNOISE55", "Noise", 5),
    ("Philips HL7756 750W Mixer Grinder 3 Jars\n₹3,249 (MRP ₹5,795)\nhttps://www.flipkart.com/p/itmmix?pid=MIXPHIL756", "Philips", 3),
    ("Levi's Men Slim Fit Jeans 511\n₹1,199 MRP ₹3,299 64% off\nhttps://www.myntra.com/jeans/levis/x/22334455/buy", "Levis", 4),
    ("American Tourister 68cm Medium Check-in Trolley Bag\n₹2,899 MRP ₹8,200\nhttps://www.amazon.in/dp/B0ATTROL68", "AT Trolley", 2),
    ("Lakme 9 to 5 Primer + Matte Lipstick Combo\n₹349 MRP ₹900\nhttps://www.nykaa.com/lakme-combo/p/551122", "Lakme", 4),
    ("Tata Sampann Unpolished Toor Dal 1kg\n₹162 MRP ₹220\nhttps://www.amazon.in/dp/B0TATADAL1", "Tata", 1),
    ("Fire-Boltt Ninja Call Pro Plus Smartwatch\n₹1,099 MRP ₹9,999 89% OFF lowest ever\nhttps://www.flipkart.com/p/itmfb?pid=SMWFBNINJA", "Fire-Boltt", 3),
    ("Wildcraft 45L Laptop Backpack Rain Cover\n₹1,049 (MRP ₹2,799)\nhttps://www.amazon.in/dp/B0WILDBP45", "Wildcraft", 1),
    ("Puma Unisex Smashic Sneakers White\n₹1,499 MRP ₹4,999 70% off\nhttps://www.myntra.com/casual-shoes/puma/x/998811/buy", "Puma", 4),
    ("Atomic Habits by James Clear Paperback\n₹399 MRP ₹799\nhttps://www.amazon.in/dp/1847941834", "Book", 2),
    ("Boldfit Yoga Mat 6mm Anti Slip with Strap\n₹299 MRP ₹1,499\nhttps://www.amazon.in/dp/B0BOLDYOGA", "Yoga Mat", 1),
    ("Himalaya Baby Gift Pack Large\n₹449 MRP ₹650\nhttps://www.flipkart.com/p/itmbaby?pid=BABYHIMGFT", "Himalaya", 3),
    ("Realme Buds T300 TWS Earbuds 40dB ANC\n₹1,799 MRP ₹3,999 55% off\nhttps://www.flipkart.com/p/itmrb?pid=ACCREALT30", "Realme", 5),
    ("Pigeon Electric Kettle 1.5L Stainless Steel\n₹499 MRP ₹1,195\nhttps://www.amazon.in/dp/B0PIGEONKT", "Pigeon", 2),
    ("Allen Solly Men Polo T-Shirt Pack of 2\n₹799 MRP ₹1,998 60% off\nhttps://www.myntra.com/tshirts/allen-solly/x/776655/buy", "Allen Solly", 4),
    ("Logitech MX Master 3S Wireless Mouse\n₹7,495 MRP ₹10,995\nhttps://www.amazon.in/dp/B0B11LJ69K", "Logitech", 5),
    ("Duracell Ultra AA Batteries Pack of 8\n₹299\nhttps://www.amazon.in/dp/B0DURACELL", "", 1),
]

ids = []
for i, (text, label, ch) in enumerate(POSTS):
    tg, _, title, _ = CHANNELS[ch - 1]
    posted = now - random.randint(10, 60 * 60 * 30)
    deal = parser.parse_message(text, channel_id=tg, channel_title=title, message_id=1000 + i,
                                posted_at=posted, image_url=img(label, i) if label else "", ttl_hours=96)
    if not deal:
        print("skipped", text[:40])
        continue
    deal_id = store.save_deal(deal)
    ids.append(deal_id)
    # the same product reposted in other channels -> trending
    if i % 4 == 0:
        for extra in range(1, 1 + (i % 3) + 2):
            ch2 = CHANNELS[(ch - 1 + extra) % len(CHANNELS)]
            again = parser.parse_message(text, channel_id=ch2[0], channel_title=ch2[2], message_id=2000 + i * 10 + extra,
                                         posted_at=posted + extra * 300, image_url=img(label, i) if label else "", ttl_hours=96)
            if again:
                store.save_deal(again)

# price history so charts and all-time-low flags have something to show
for row in db.query("SELECT product_key, price, store FROM deals"):
    if not row["price"]:
        continue
    base = row["price"]
    pts = []
    for d in range(12, 0, -1):
        pts.append((base * random.choice([1.0, 1.08, 1.15, 1.22, 1.3]), now - d * 86400 * 1.5))
    for p, at in pts:
        db.execute("INSERT INTO price_history (product_key, price, store, seen_at) VALUES (?,?,?,?)",
                   (row["product_key"], round(p), row["store"], at))
db.execute("UPDATE deals SET is_lowest=1 WHERE rowid % 3 = 0")
store.rescore_all()

db.execute("INSERT INTO watchlists (user_id, query, filters, notify, created_at) VALUES (?,?,?,?,?)",
           (uid, "power bank", '{"max_price": 1500}', 1, now))
db.execute("INSERT INTO watchlists (user_id, query, filters, notify, created_at) VALUES (?,?,?,?,?)",
           (uid, "running shoes", '{"min_discount": 50, "category": "Footwear"}', 0, now))
db.set_meta("ingest_last_run", str(now - 120))

print("deals:", db.query_one("SELECT COUNT(*) c FROM deals")["c"])
print("COOKIE", auth.create_session(TG_USER))
