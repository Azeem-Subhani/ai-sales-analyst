"""Dummy data generator: builds data/sales.duckdb ("connector #1", plan section 5).

Run with: python -m src.data.generate

Creates the six standard tables (customers, products, orders, order_items,
inventory, forecasts) for a fake online store, 1 Oct 2024 to 30 Sep 2026,
with realistic patterns and the five planted problems the AI must find.
Everything comes from one seeded NumPy generator and one seeded Faker
instance, so the same seed always produces identical data.

Demand definition used throughout: units on orders that were not cancelled
(completed + returned). Revenue checks use completed orders only.
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from faker import Faker

# --- Output locations -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "sales.duckdb"
ISSUES_PATH = PROJECT_ROOT / "data" / "planted_issues.json"

# --- Period and size --------------------------------------------------------
SEED = 42
START_DATE = date(2024, 10, 1)
END_DATE = date(2026, 9, 30)
N_DAYS = (END_DATE - START_DATE).days + 1
FIRST_SNAPSHOT = date(2024, 10, 7)  # first Monday in the period; inventory is weekly
N_CUSTOMERS = 2000
N_WHOLESALE = 150
N_PRODUCTS = 250
TARGET_ORDERS = 70_000
TARGET_ORDER_ITEMS = 180_000

CATEGORIES = {
    # name: (SKU prefix, share of retail demand, price range, typical cost ratio)
    "Electronics": ("EL", 0.14, (25, 400), 0.70),
    "Home & Kitchen": ("HK", 0.16, (10, 150), 0.55),
    "Apparel": ("AP", 0.16, (12, 120), 0.45),
    "Beauty": ("BE", 0.12, (6, 60), 0.40),
    "Sports & Outdoors": ("SO", 0.12, (10, 200), 0.55),
    "Toys": ("TO", 0.10, (8, 80), 0.50),
    "Office": ("OF", 0.10, (3, 60), 0.50),
    "Pet Supplies": ("PS", 0.10, (5, 70), 0.50),
}
PRODUCT_NOUNS = {
    "Electronics": [
        "Wireless Earbuds",
        "Bluetooth Speaker",
        "USB-C Charger",
        "Smart Watch",
        "Webcam",
        "Power Bank",
        "Keyboard",
        "Monitor Stand",
        "Tablet Case",
    ],
    "Home & Kitchen": [
        "Chef Knife",
        "Coffee Grinder",
        "Cutting Board",
        "Storage Jars",
        "Bath Towel Set",
        "Table Lamp",
        "Frying Pan",
        "Mixing Bowls",
        "Kettle",
    ],
    "Apparel": [
        "Cotton T-Shirt",
        "Hoodie",
        "Chino Trousers",
        "Rain Jacket",
        "Wool Scarf",
        "Running Shorts",
        "Linen Shirt",
        "Knit Jumper",
        "Denim Jeans",
    ],
    "Beauty": [
        "Face Serum",
        "Hand Cream",
        "Shampoo",
        "Lip Balm",
        "Body Wash",
        "Sunscreen",
        "Face Mask",
        "Hair Oil",
        "Night Cream",
    ],
    "Sports & Outdoors": [
        "Yoga Mat",
        "Water Bottle",
        "Hiking Backpack",
        "Resistance Bands",
        "Camping Lantern",
        "Cycling Gloves",
        "Dumbbell Set",
        "Tent",
        "Headlamp",
    ],
    "Toys": [
        "Building Blocks",
        "Puzzle",
        "Plush Bear",
        "Board Game",
        "Toy Car",
        "Art Set",
        "Kite",
        "Dolls House",
        "Science Kit",
    ],
    "Office": [
        "Notebook",
        "Gel Pens",
        "Desk Organiser",
        "Stapler",
        "Sticky Notes",
        "Desk Lamp",
        "File Folders",
        "Planner",
        "Whiteboard",
    ],
    "Pet Supplies": [
        "Dog Lead",
        "Cat Toy",
        "Pet Bed",
        "Dog Treats",
        "Food Bowl",
        "Cat Litter",
        "Grooming Brush",
        "Chew Toy",
        "Pet Shampoo",
    ],
}
PRODUCT_ADJECTIVES = ["Classic", "Pro", "Eco", "Ultra", "Compact", "Premium", "Essential", "Deluxe"]
PRODUCT_SKU_START = 1001
PRICE_ENDING = 0.99
POPULARITY_SIGMA = 0.9  # spread of product popularity (lognormal)
N_MID_PERIOD_LAUNCHES = 20
N_DISCONTINUED = 5
LEAD_TIME_RANGE_DAYS = (7, 42)

COUNTRIES = {
    # country: (share of customers, regions)
    "United Kingdom": (
        0.70,
        [
            "London",
            "South East",
            "South West",
            "Midlands",
            "North West",
            "North East",
            "Yorkshire",
            "Scotland",
            "Wales",
        ],
    ),
    "Ireland": (0.08, ["Leinster", "Munster", "Connacht", "Ulster"]),
    "Germany": (0.08, ["Bavaria", "Berlin", "Hamburg", "North Rhine-Westphalia"]),
    "France": (0.07, ["Ile-de-France", "Auvergne-Rhone-Alpes", "Occitanie"]),
    "Netherlands": (0.07, ["North Holland", "South Holland", "Utrecht"]),
}
SHARE_RETAIL_SIGNED_UP_BEFORE_START = 0.4
EARLIEST_SIGNUP = date(2022, 1, 1)
RETAIL_ACTIVITY_SIGMA = 1.0  # some retail customers order far more often than others

# --- Demand patterns --------------------------------------------------------
ANNUAL_GROWTH = 0.15
MONTH_FACTOR = {
    1: 0.80,
    2: 0.90,
    3: 0.95,
    4: 0.95,
    5: 0.95,
    6: 0.95,
    7: 0.95,
    8: 0.95,
    9: 0.95,
    10: 1.00,
    11: 1.50,
    12: 1.55,
}
RETAIL_WEEKEND_FACTOR = 1.35
RETAIL_WEEKDAY_FACTOR = (7 - 2 * RETAIL_WEEKEND_FACTOR) / 5  # keeps the weekly average at 1
RETAIL_CHANNELS = (["web", "mobile", "marketplace"], [0.50, 0.35, 0.15])
WHOLESALE_CHANNELS = (["web", "mobile"], [0.85, 0.15])
RETAIL_EXTRA_QTY_MEAN = 0.35  # retail quantity = 1 + Poisson(this)
WHOLESALE_CYCLE_WEEKS = (2, 6)  # each wholesale customer reorders every 2 to 6 weeks
WHOLESALE_WEEKDAY_JITTER = 1  # orders land on the usual weekday or one either side
SHARE_WHOLESALE_SIGNED_UP_BEFORE_START = 0.85
WHOLESALE_LINES = (3, 8)  # lines per wholesale order, upper bound exclusive
WHOLESALE_QTY = (2, 11)
WHOLESALE_FAVOURITE_SHARE = (
    0.7  # share of wholesale lines from the customer's 2 favourite categories
)
STATUSES = (["completed", "cancelled", "returned"], [0.92, 0.03, 0.05])


@dataclass(frozen=True)
class Promotion:
    """A promotion window with its discount code."""

    code: str
    start: date
    end: date
    discount_pct: float


# Four promotions per year. None in July-September 2026, so they do not blur the planted problems.
PROMOTIONS = [
    Promotion("AUTUMN24", date(2024, 10, 14), date(2024, 10, 20), 0.15),
    Promotion("BLACKFRIDAY24", date(2024, 11, 27), date(2024, 12, 2), 0.25),
    Promotion("SPRING25", date(2025, 3, 20), date(2025, 3, 26), 0.15),
    Promotion("SUMMER25", date(2025, 6, 20), date(2025, 6, 26), 0.20),
    Promotion("AUTUMN25", date(2025, 10, 13), date(2025, 10, 19), 0.15),
    Promotion("BLACKFRIDAY25", date(2025, 11, 26), date(2025, 12, 1), 0.25),
    Promotion("SPRING26", date(2026, 3, 19), date(2026, 3, 25), 0.15),
    Promotion("SUMMER26", date(2026, 6, 19), date(2026, 6, 25), 0.20),
]
PROMO_DEMAND_UPLIFT = 1.6
PROMO_CODE_USAGE = 0.65  # share of retail orders in a promotion that use its code
WELCOME_CODE = "WELCOME10"
WELCOME_CODE_PCT = 0.10
WELCOME_CODE_USAGE = 0.03

# --- Planted problems (exact names and SKUs from the plan) ------------------
CHURN_CUSTOMERS = ["Harbor & Pine Co.", "Bluegate Supplies", "Kestrel Home Stores"]
CHURN_LAST_ORDER = date(2026, 8, 15)
CHURN_CYCLE_WEEKS = 2
CHURN_APPAREL_SHARE = 0.9
CHURN_SIGNUP = date(2022, 6, 1)

APPAREL_DECLINE_START = date(2026, 7, 15)
APPAREL_DECLINE_END_FACTOR = (
    0.55  # Apparel demand weight on 30 Sep 2026 vs normal (non-churn buyers)
)

STOCKOUT_PRODUCTS = 10
STOCKOUT_WINDOWS = [  # (first stockout Monday, number of weeks), all inside September 2026
    (date(2026, 9, 7), 1),
    (date(2026, 9, 7), 2),
    (date(2026, 9, 7), 3),
    (date(2026, 9, 14), 1),
    (date(2026, 9, 14), 2),
    (date(2026, 9, 21), 1),
]
STOCKOUT_POPULARITY_QUANTILE = 0.6  # pick stockout products from the more popular ones

BIASED_SKU, BIASED_NAME = "AP-1150", "Classic Denim Jacket"
BIASED_PRICE = 59.99
FORECAST_BIAS = 0.25
BIASED_POPULARITY_BOOST = 2.0  # popular enough that weekly bias is stable

PREBUILD_SKU, PREBUILD_NAME = "SB-1042", "Smart Bottle Pro"
PREBUILD_CATEGORY = "Sports & Outdoors"
PREBUILD_PRICE = 34.99
PREBUILD_LAUNCH = date(2026, 7, 1)
PREBUILD_LEAD_TIME_DAYS = 28
PREBUILD_BASE_SHARE = 0.03  # share of retail lines after launch
PREBUILD_SURGE_START = date(2026, 9, 3)  # first day of the last 4 weeks
PREBUILD_SURGE_FACTOR = 1.6
PREBUILD_COVER_DAYS = 9

# --- Forecasts and inventory ------------------------------------------------
FORECAST_WEEK_STARTS = [date(2026, 8, 3) + timedelta(weeks=w) for w in range(8)]
FORECAST_MODEL = "seeded"
FORECAST_NOISE_SD = 0.20
FORECAST_NOISE_CLIP = (-0.6, 0.8)
FORECAST_BIAS_NOISE_SD = 0.04
FORECAST_LEAD_DAYS = 7  # forecast for a week is created the Monday before
FORECAST_RUN_TIME = time(6, 0)
STOCK_COVER_WEEKS = (2.0, 6.0)
STOCK_NOISE_SIGMA = 0.25
TRAILING_WEEKS = 4
DAYS_IN_FOUR_WEEKS = 28


def day_index(d: date) -> int:
    """Return the number of days from START_DATE to d."""
    return (d - START_DATE).days


def to_date(index: int) -> date:
    """Convert a day index back into a calendar date."""
    return START_DATE + timedelta(days=int(index))


def slugify(text: str) -> str:
    """Make a lowercase, email-safe version of a name."""
    return re.sub(r"[^a-z0-9]+", ".", text.lower()).strip(".")


def random_day_between(rng: np.random.Generator, first: date, last: date) -> int:
    """Pick a random day index between two dates (inclusive). May be negative."""
    return int(rng.integers(day_index(first), day_index(last) + 1))


# --- Products ---------------------------------------------------------------
def build_products(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the products table plus internal metadata (popularity, discontinue day)."""
    names = list(CATEGORIES)
    counts = [N_PRODUCTS // len(names)] * len(names)
    for i in range(N_PRODUCTS % len(names)):
        counts[i] += 1

    rows = []
    for category, count in zip(names, counts, strict=True):
        prefix, _, (low, high), cost_ratio = CATEGORIES[category]
        combos = [f"{a} {n}" for a in PRODUCT_ADJECTIVES for n in PRODUCT_NOUNS[category]]
        chosen = rng.choice(len(combos), size=count, replace=False)
        for k, combo_index in enumerate(chosen):
            # Lognormal prices inside the category range, so cheap items are more common.
            price = float(np.clip(low * np.exp(rng.normal(1.0, 0.7)), low, high))
            rows.append(
                {
                    "sku": f"{prefix}-{PRODUCT_SKU_START + k}",
                    "name": combos[combo_index],
                    "category": category,
                    "unit_price": round(np.floor(price) + PRICE_ENDING, 2),  # e.g. 24.99
                    "cost_ratio": float(np.clip(rng.normal(cost_ratio, 0.05), 0.25, 0.9)),
                }
            )

    products = pd.DataFrame(rows)
    _plant_special_product(products, "Apparel", BIASED_SKU, BIASED_NAME, BIASED_PRICE)
    _plant_special_product(products, PREBUILD_CATEGORY, PREBUILD_SKU, PREBUILD_NAME, PREBUILD_PRICE)

    products.insert(0, "product_id", np.arange(1, len(products) + 1))
    products["unit_cost"] = (products["unit_price"] * products.pop("cost_ratio")).round(2)
    products["lead_time_days"] = rng.integers(
        LEAD_TIME_RANGE_DAYS[0], LEAD_TIME_RANGE_DAYS[1] + 1, len(products)
    )

    meta = _product_metadata(rng, products)
    products["launch_date"] = [to_date(i) for i in meta["launch_day"]]
    is_prebuild = products["sku"] == PREBUILD_SKU
    products.loc[is_prebuild, "lead_time_days"] = PREBUILD_LEAD_TIME_DAYS
    columns = [
        "product_id",
        "sku",
        "name",
        "category",
        "unit_price",
        "unit_cost",
        "launch_date",
        "lead_time_days",
    ]
    return products[columns], meta


def _plant_special_product(
    products: pd.DataFrame, category: str, sku: str, name: str, price: float
) -> None:
    """Replace the last generated product in a category with a planted product."""
    row = products.index[products["category"] == category][-1]
    products.loc[row, ["sku", "name", "unit_price"]] = [sku, name, price]


def _product_metadata(rng: np.random.Generator, products: pd.DataFrame) -> pd.DataFrame:
    """Choose popularity, launch day and discontinue day for each product."""
    n = len(products)
    popularity = rng.lognormal(0.0, POPULARITY_SIGMA, n)
    launch_day = np.array(
        [random_day_between(rng, EARLIEST_SIGNUP, date(2024, 9, 1)) for _ in range(n)]
    )
    discontinue_day = np.full(n, N_DAYS)  # N_DAYS means "still on sale"

    is_biased = (products["sku"] == BIASED_SKU).to_numpy()
    is_prebuild = (products["sku"] == PREBUILD_SKU).to_numpy()
    apparel = (products["category"] == "Apparel").to_numpy()
    popularity[is_biased] = np.quantile(popularity[apparel], 0.9) * BIASED_POPULARITY_BOOST
    launch_day[is_prebuild] = day_index(PREBUILD_LAUNCH)

    ordinary = np.flatnonzero(~is_biased & ~is_prebuild)
    picked = rng.choice(ordinary, size=N_MID_PERIOD_LAUNCHES + N_DISCONTINUED, replace=False)
    for i in picked[:N_MID_PERIOD_LAUNCHES]:
        launch_day[i] = random_day_between(rng, date(2024, 11, 1), date(2026, 5, 31))
    for i in picked[N_MID_PERIOD_LAUNCHES:]:
        discontinue_day[i] = random_day_between(rng, date(2025, 3, 1), date(2026, 3, 31))

    return pd.DataFrame(
        {
            "product_id": products["product_id"],
            "popularity": popularity,
            "launch_day": launch_day,
            "discontinue_day": discontinue_day,
        }
    )


def choose_stockouts(
    rng: np.random.Generator, products: pd.DataFrame, meta: pd.DataFrame
) -> dict[int, tuple[date, int]]:
    """Pick 10 established, popular, non-Apparel products and their September stockout."""
    threshold = meta["popularity"].quantile(STOCKOUT_POPULARITY_QUANTILE)
    eligible = (
        (meta["popularity"] >= threshold)
        & (meta["launch_day"] < 0)
        & (meta["discontinue_day"] == N_DAYS)
        & (products["category"] != "Apparel")
        & ~products["sku"].isin([BIASED_SKU, PREBUILD_SKU])
    )
    candidates = products.loc[eligible, "product_id"].to_numpy()
    chosen = rng.choice(candidates, size=STOCKOUT_PRODUCTS, replace=False)
    windows = rng.integers(0, len(STOCKOUT_WINDOWS), STOCKOUT_PRODUCTS)
    return {int(p): STOCKOUT_WINDOWS[w] for p, w in zip(sorted(chosen), windows, strict=True)}


def availability_matrix(meta: pd.DataFrame, stockouts: dict[int, tuple[date, int]]) -> np.ndarray:
    """Return a (day x product) True/False array: can the product be sold that day?"""
    days = np.arange(N_DAYS)[:, None]
    available = (days >= meta["launch_day"].to_numpy()) & (
        days < meta["discontinue_day"].to_numpy()
    )
    for product_id, (first_monday, weeks) in stockouts.items():
        first = day_index(first_monday)
        available[first : first + 7 * weeks, product_id - 1] = False  # sales capped at zero
    return available


# --- Customers --------------------------------------------------------------
def build_customers(rng: np.random.Generator, fake: Faker) -> pd.DataFrame:
    """Build the customers table: 1,850 retail and 150 wholesale customers."""
    country_names = list(COUNTRIES)
    country_shares = [COUNTRIES[c][0] for c in country_names]
    rows = []

    taken_names = set(CHURN_CUSTOMERS)
    for i in range(N_WHOLESALE):
        if i < len(CHURN_CUSTOMERS):
            name, country, signup = CHURN_CUSTOMERS[i], "United Kingdom", CHURN_SIGNUP
        else:
            name = fake.company()
            while name in taken_names:
                name = fake.company()
            country = str(rng.choice(country_names, p=country_shares))
            if rng.random() < SHARE_WHOLESALE_SIGNED_UP_BEFORE_START:
                signup_day = random_day_between(rng, EARLIEST_SIGNUP, START_DATE - timedelta(1))
            else:
                signup_day = random_day_between(rng, START_DATE, date(2025, 9, 30))
            signup = to_date(signup_day)
        taken_names.add(name)
        rows.append((name, "wholesale", country, signup))

    n_retail = N_CUSTOMERS - N_WHOLESALE
    for _ in range(n_retail):
        if rng.random() < SHARE_RETAIL_SIGNED_UP_BEFORE_START:
            signup_day = random_day_between(rng, EARLIEST_SIGNUP, START_DATE - timedelta(1))
        else:
            # Later signups slightly more likely, matching business growth.
            signup_day = int(N_DAYS * np.sqrt(rng.random()))
            signup_day = min(signup_day, N_DAYS - 1)
        country = str(rng.choice(country_names, p=country_shares))
        rows.append((fake.name(), "retail", country, to_date(signup_day)))

    customers = pd.DataFrame(rows, columns=["name", "customer_type", "country", "signup_date"])
    customers = customers.iloc[rng.permutation(len(customers))].reset_index(drop=True)
    customers.insert(0, "customer_id", np.arange(1, len(customers) + 1))
    customers["region"] = [str(rng.choice(COUNTRIES[c][1])) for c in customers["country"]]
    customers["email"] = [
        f"{slugify(n)}.{cid}@example.com"
        for n, cid in zip(customers["name"], customers["customer_id"], strict=True)
    ]
    columns = ["customer_id", "name", "email", "customer_type", "country", "region", "signup_date"]
    return customers[columns]


# --- Demand shape -----------------------------------------------------------
def promo_for_day(day: int) -> Promotion | None:
    """Return the promotion running on a day index, if any."""
    d = to_date(day)
    return next((p for p in PROMOTIONS if p.start <= d <= p.end), None)


def retail_day_factors() -> np.ndarray:
    """Relative retail order volume per day: growth x season x weekday x promotion."""
    factors = np.empty(N_DAYS)
    for day in range(N_DAYS):
        d = to_date(day)
        growth = (1 + ANNUAL_GROWTH) ** (day / 365)
        weekday = RETAIL_WEEKEND_FACTOR if d.weekday() >= 5 else RETAIL_WEEKDAY_FACTOR
        promo = PROMO_DEMAND_UPLIFT if promo_for_day(day) else 1.0
        factors[day] = growth * MONTH_FACTOR[d.month] * weekday * promo
    return factors


def apparel_factor(day: int) -> float:
    """Apparel retail demand multiplier: 1.0, then a linear decline from mid-July 2026."""
    start = day_index(APPAREL_DECLINE_START)
    if day < start:
        return 1.0
    progress = (day - start) / (N_DAYS - 1 - start)
    return 1.0 - progress * (1.0 - APPAREL_DECLINE_END_FACTOR)


def prebuild_share(day: int) -> float:
    """Share of retail lines going to Smart Bottle Pro on a given day."""
    if day < day_index(PREBUILD_LAUNCH):
        return 0.0
    surge = PREBUILD_SURGE_FACTOR if day >= day_index(PREBUILD_SURGE_START) else 1.0
    return PREBUILD_BASE_SHARE * surge


def retail_product_probabilities(
    products: pd.DataFrame, meta: pd.DataFrame, available: np.ndarray
) -> np.ndarray:
    """Return a (day x product) array of retail purchase probabilities."""
    category_share = products["category"].map({c: v[1] for c, v in CATEGORIES.items()})
    category_size = products.groupby("category")["category"].transform("size")
    base = (
        meta["popularity"]
        / meta.groupby(products["category"])["popularity"].transform("mean")
        * category_share
        / category_size
    ).to_numpy()
    is_apparel = (products["category"] == "Apparel").to_numpy()
    prebuild = int(np.flatnonzero(products["sku"] == PREBUILD_SKU)[0])

    probs = np.zeros((N_DAYS, len(products)))
    for day in range(N_DAYS):
        weights = base * available[day]
        weights[is_apparel] *= apparel_factor(day)
        weights[prebuild] = 0.0
        share = prebuild_share(day)
        probs[day] = weights / weights.sum() * (1 - share)
        probs[day, prebuild] = share
    return probs


# --- Orders -----------------------------------------------------------------
def wholesale_schedule(
    rng: np.random.Generator, first_day: int, last_day: int, cycle_weeks: int
) -> list[int]:
    """Order days for one wholesale customer: every few weeks, always on a weekday.

    Each customer has a usual weekday (Mon-Fri); each order lands on it or a neighbour.
    """
    usual_weekday = int(rng.integers(0, 5))
    week_start = first_day - to_date(first_day).weekday()  # Monday of the first week
    week_start += 7 * int(rng.integers(0, cycle_weeks))
    days = []
    while True:
        jitter = int(rng.integers(-WHOLESALE_WEEKDAY_JITTER, WHOLESALE_WEEKDAY_JITTER + 1))
        day = week_start + int(np.clip(usual_weekday + jitter, 0, 4))
        if day > last_day:
            return days
        if day >= first_day:
            days.append(day)
        week_start += 7 * cycle_weeks


def build_wholesale_orders(
    rng: np.random.Generator,
    customers: pd.DataFrame,
    products: pd.DataFrame,
    meta: pd.DataFrame,
    available: np.ndarray,
) -> tuple[list[dict], list[dict]]:
    """Generate wholesale orders and lines. Churn customers stop after mid-August 2026."""
    categories = products["category"].to_numpy()
    popularity = meta["popularity"].to_numpy().copy()
    popularity[products["sku"] == PREBUILD_SKU] = 0.0  # new product: retail only
    category_names = list(CATEGORIES)

    orders, lines = [], []
    wholesale = customers[customers["customer_type"] == "wholesale"]
    for customer in wholesale.itertuples():
        churner = customer.name in CHURN_CUSTOMERS
        if churner:
            favourites, favourite_share = ["Apparel"], CHURN_APPAREL_SHARE
            cycle, last_day = CHURN_CYCLE_WEEKS, day_index(CHURN_LAST_ORDER)
        else:
            favourites = list(rng.choice(category_names, size=2, replace=False))
            favourite_share = WHOLESALE_FAVOURITE_SHARE
            cycle = int(rng.integers(WHOLESALE_CYCLE_WEEKS[0], WHOLESALE_CYCLE_WEEKS[1] + 1))
            last_day = N_DAYS - 1
        first_day = max(0, day_index(customer.signup_date))
        in_favourites = np.isin(categories, favourites)
        is_apparel = categories == "Apparel"

        for day in wholesale_schedule(rng, first_day, last_day, cycle):
            # Wholesale baskets follow retail seasonality; wholesale growth comes from new accounts.
            scale = MONTH_FACTOR[to_date(day).month]
            order_ref = len(orders)
            orders.append(
                {
                    "ref": order_ref,
                    "customer_id": customer.customer_id,
                    "day": day,
                    "segment": "wholesale",
                }
            )
            for _ in range(int(rng.integers(*WHOLESALE_LINES))):
                pool = in_favourites if rng.random() < favourite_share else ~in_favourites
                weights = popularity * available[day] * pool
                if not churner:  # the Apparel decline hits all buyers, not just retail
                    weights = np.where(is_apparel, weights * apparel_factor(day), weights)
                if weights.sum() == 0:
                    continue
                product = int(rng.choice(len(weights), p=weights / weights.sum()))
                qty = max(1, round(int(rng.integers(*WHOLESALE_QTY)) * scale))
                lines.append({"ref": order_ref, "product_index": product, "quantity": qty})
    return orders, lines


def build_retail_orders(
    rng: np.random.Generator,
    customers: pd.DataFrame,
    probs: np.ndarray,
    n_orders_target: int,
    n_lines_target: int,
    ref_offset: int,
) -> tuple[list[dict], list[dict]]:
    """Generate retail orders day by day, scaled to hit the target counts on average."""
    retail = customers[customers["customer_type"] == "retail"].sort_values(
        ["signup_date", "customer_id"]
    )
    signup_days = np.array([day_index(d) for d in retail["signup_date"]])
    customer_ids = retail["customer_id"].to_numpy()
    activity = np.cumsum(rng.lognormal(0.0, RETAIL_ACTIVITY_SIGMA, len(retail)))

    factors = retail_day_factors()
    expected_orders = factors / factors.sum() * n_orders_target
    extra_lines_mean = max(0.0, n_lines_target / n_orders_target - 1)

    orders, lines = [], []
    for day in range(N_DAYS):
        n_orders = int(rng.poisson(expected_orders[day]))
        signed_up = int(np.searchsorted(signup_days, day, side="right"))
        if n_orders == 0 or signed_up == 0:
            continue
        # Weighted pick among customers who have signed up by this day.
        picks = np.searchsorted(
            activity, rng.random(n_orders) * activity[signed_up - 1], side="right"
        )
        n_lines = 1 + rng.poisson(extra_lines_mean, n_orders)
        products = rng.choice(probs.shape[1], size=int(n_lines.sum()), p=probs[day])
        quantities = 1 + rng.poisson(RETAIL_EXTRA_QTY_MEAN, len(products))

        cursor = 0
        for k in range(n_orders):
            order_ref = ref_offset + len(orders)
            orders.append(
                {
                    "ref": order_ref,
                    "customer_id": int(customer_ids[picks[k]]),
                    "day": day,
                    "segment": "retail",
                }
            )
            for j in range(cursor, cursor + n_lines[k]):
                lines.append(
                    {
                        "ref": order_ref,
                        "product_index": int(products[j]),
                        "quantity": int(quantities[j]),
                    }
                )
            cursor += n_lines[k]
    return orders, lines


def finalise_orders(
    rng: np.random.Generator, order_rows: list[dict], line_rows: list[dict], products: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add channel, status and discount codes, sort by date and assign final IDs."""
    orders = pd.DataFrame(order_rows).sort_values(["day", "customer_id", "ref"])
    orders["order_id"] = np.arange(1, len(orders) + 1)

    is_retail = (orders["segment"] == "retail").to_numpy()
    channel = np.where(
        is_retail,
        rng.choice(RETAIL_CHANNELS[0], size=len(orders), p=RETAIL_CHANNELS[1]),
        rng.choice(WHOLESALE_CHANNELS[0], size=len(orders), p=WHOLESALE_CHANNELS[1]),
    )
    status = rng.choice(STATUSES[0], size=len(orders), p=STATUSES[1])

    codes, pcts = [], []
    for day, retail_order, roll in zip(
        orders["day"], is_retail, rng.random(len(orders)), strict=True
    ):
        promo = promo_for_day(day)
        if retail_order and promo and roll < PROMO_CODE_USAGE:
            codes.append(promo.code)
            pcts.append(promo.discount_pct)
        elif retail_order and not promo and roll < WELCOME_CODE_USAGE:
            codes.append(WELCOME_CODE)
            pcts.append(WELCOME_CODE_PCT)
        else:
            codes.append(None)
            pcts.append(0.0)
    orders["channel"], orders["status"] = channel, status
    orders["discount_code"], orders["discount_pct"] = codes, pcts
    orders["order_date"] = [to_date(d) for d in orders["day"]]

    lines = pd.DataFrame(line_rows)
    # A product drawn twice in one order becomes one line with the combined quantity.
    lines = lines.groupby(["ref", "product_index"], as_index=False, sort=False)["quantity"].sum()
    lines = lines.merge(orders[["ref", "order_id", "discount_pct"]], on="ref")
    lines["product_id"] = lines["product_index"] + 1
    lines["unit_price"] = products["unit_price"].to_numpy()[lines["product_index"]]
    lines = lines.sort_values(["order_id", "product_id"]).reset_index(drop=True)
    lines.insert(0, "order_item_id", np.arange(1, len(lines) + 1))

    orders_out = orders[
        ["order_id", "customer_id", "order_date", "channel", "status", "discount_code"]
    ].reset_index(drop=True)
    items_out = lines[
        ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "discount_pct"]
    ]
    return orders_out, items_out


# --- Inventory and forecasts ------------------------------------------------
def weekly_demand(orders: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    """Units per product per Monday-starting week, from non-cancelled orders."""
    demand = demand_lines(orders, items)
    dates = pd.to_datetime(demand["order_date"])
    demand = demand.assign(week_start=(dates - pd.to_timedelta(dates.dt.weekday, "D")).dt.date)
    return demand.groupby(["product_id", "week_start"], as_index=False)["quantity"].sum()


def snapshot_dates() -> list[date]:
    """Weekly inventory snapshot dates (Mondays) inside the period."""
    count = (END_DATE - FIRST_SNAPSHOT).days // 7 + 1
    return [FIRST_SNAPSHOT + timedelta(weeks=w) for w in range(count)]


def build_inventory(
    rng: np.random.Generator,
    products: pd.DataFrame,
    meta: pd.DataFrame,
    weekly: pd.DataFrame,
    stockouts: dict[int, tuple[date, int]],
    prebuild_recent_units: int,
) -> pd.DataFrame:
    """Weekly stock snapshots. Stockout products show on_hand = 0 in their window."""
    snapshots = snapshot_dates()
    demand = weekly.pivot(index="week_start", columns="product_id", values="quantity")
    demand = demand.reindex(index=snapshots, columns=products["product_id"]).fillna(0)
    trailing = demand.rolling(TRAILING_WEEKS, min_periods=1).mean()  # recent weekly demand
    lead_weeks = products.set_index("product_id")["lead_time_days"] / 7
    cover = rng.uniform(*STOCK_COVER_WEEKS, len(products))
    prebuild_id = int(products.loc[products["sku"] == PREBUILD_SKU, "product_id"].iloc[0])

    rows = []
    for p_index, product_id in enumerate(products["product_id"]):
        first_day, end_day = meta.loc[p_index, ["launch_day", "discontinue_day"]]
        stockout_snaps = set()
        if product_id in stockouts:
            first_monday, weeks = stockouts[product_id]
            stockout_snaps = {first_monday + timedelta(weeks=w) for w in range(weeks)}
        for snap in snapshots:
            if not first_day <= day_index(snap) < end_day:
                continue  # no stock record before launch or after discontinuation
            weekly_units = max(trailing.at[snap, product_id], 1.0)
            noise = rng.lognormal(0.0, STOCK_NOISE_SIGMA)
            on_hand = max(1, round(weekly_units * cover[p_index] * noise))
            reorder_point = weekly_units * lead_weeks[product_id]
            on_order = round(weekly_units * lead_weeks[product_id] * rng.uniform(0.5, 1.5))
            on_order = on_order if on_hand < reorder_point else 0
            if snap in stockout_snaps:
                on_hand, on_order = 0, max(on_order, round(weekly_units * cover[p_index]))
            rows.append((product_id, snap, on_hand, on_order))

    inventory = pd.DataFrame(rows, columns=["product_id", "snapshot_date", "on_hand", "on_order"])
    _plant_low_cover(inventory, prebuild_id, prebuild_recent_units)
    return inventory


def demand_lines(orders: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    """Order lines with their order date, excluding cancelled orders (the demand definition)."""
    lines = items.merge(orders[["order_id", "order_date", "status"]], on="order_id")
    return lines[lines["status"] != "cancelled"]


def last_four_weeks_units(lines: pd.DataFrame, product_id: int) -> tuple[int, int]:
    """Units for a product in the last 28 days and the 28 days before."""
    rows = lines[lines["product_id"] == product_id]
    recent_start = END_DATE - timedelta(days=DAYS_IN_FOUR_WEEKS - 1)
    prior_start = recent_start - timedelta(days=DAYS_IN_FOUR_WEEKS)
    recent = rows.loc[rows["order_date"] >= recent_start, "quantity"].sum()
    prior = rows.loc[
        (rows["order_date"] >= prior_start) & (rows["order_date"] < recent_start), "quantity"
    ].sum()
    return int(recent), int(prior)


def _plant_low_cover(inventory: pd.DataFrame, prebuild_id: int, recent_units: int) -> None:
    """Set Smart Bottle Pro's latest stock to about 9 days of recent demand, none on order."""
    daily_rate = recent_units / DAYS_IN_FOUR_WEEKS
    latest = inventory.index[inventory["product_id"] == prebuild_id][-1]
    inventory.loc[latest, ["on_hand", "on_order"]] = [round(daily_rate * PREBUILD_COVER_DAYS), 0]


def build_forecasts(
    rng: np.random.Generator,
    products: pd.DataFrame,
    meta: pd.DataFrame,
    weekly: pd.DataFrame,
    stockouts: dict[int, tuple[date, int]],
) -> pd.DataFrame:
    """Seeded forecasts for Aug-Sep 2026 with realistic error; AP-1150 runs ~25% high."""
    demand = weekly.pivot(index="week_start", columns="product_id", values="quantity").fillna(0)
    biased_id = int(products.loc[products["sku"] == BIASED_SKU, "product_id"].iloc[0])

    rows = []
    for week in FORECAST_WEEK_STARTS:
        created_at = datetime.combine(week - timedelta(days=FORECAST_LEAD_DAYS), FORECAST_RUN_TIME)
        for p_index, product_id in enumerate(products["product_id"]):
            first_day, end_day = meta.loc[p_index, ["launch_day", "discontinue_day"]]
            if not first_day <= day_index(week) < end_day:
                continue
            actual = demand.at[week, product_id] if week in demand.index else 0.0
            if _in_stockout(product_id, week, stockouts):
                # The forecaster does not know about the stockout; use normal recent demand.
                history = demand.loc[demand.index < week, product_id].tail(TRAILING_WEEKS)
                actual = history.mean()
            if product_id == biased_id:
                error = rng.normal(FORECAST_BIAS, FORECAST_BIAS_NOISE_SD)
            else:
                error = float(np.clip(rng.normal(0.0, FORECAST_NOISE_SD), *FORECAST_NOISE_CLIP))
            forecast = round(max(0.0, actual * (1 + error)), 1)
            rows.append((product_id, week, forecast, FORECAST_MODEL, created_at))
    return pd.DataFrame(
        rows, columns=["product_id", "week_start", "forecast_qty", "model", "created_at"]
    )


def _in_stockout(product_id: int, week: date, stockouts: dict[int, tuple[date, int]]) -> bool:
    """True if the product was out of stock during the week starting on this Monday."""
    if product_id not in stockouts:
        return False
    first_monday, weeks = stockouts[product_id]
    return first_monday <= week < first_monday + timedelta(weeks=weeks)


# --- Planted issue descriptions ---------------------------------------------
def describe_planted_issues(
    tables: dict[str, pd.DataFrame], stockouts: dict[int, tuple[date, int]]
) -> list[dict]:
    """Describe each planted problem with the entities and measured values (for tests)."""
    products, orders, items = tables["products"], tables["orders"], tables["order_items"]
    sku_of = products.set_index("product_id")["sku"]
    name_of = products.set_index("product_id")["name"]
    lines = items.merge(orders, on="order_id").merge(
        products[["product_id", "category"]], on="product_id"
    )
    lines["net"] = lines["quantity"] * lines["unit_price"] * (1 - lines["discount_pct"])
    completed = lines[lines["status"] == "completed"]

    def apparel_revenue(month: int) -> float:
        in_month = completed["order_date"].map(lambda d: (d.year, d.month) == (2026, month))
        return round(
            float(completed.loc[in_month & (completed["category"] == "Apparel"), "net"].sum()), 2
        )

    prebuild_id = int(products.loc[products["sku"] == PREBUILD_SKU, "product_id"].iloc[0])
    recent, prior = last_four_weeks_units(demand_lines(orders, items), prebuild_id)
    latest_stock = tables["inventory"][tables["inventory"]["product_id"] == prebuild_id].iloc[-1]

    jul, sep = apparel_revenue(7), apparel_revenue(9)
    return [
        {
            "id": 1,
            "problem": "Wholesale churn",
            "detail": "Three Apparel-focused wholesale customers stop ordering after mid-August 2026.",
            "customers": CHURN_CUSTOMERS,
            "last_order_on_or_before": CHURN_LAST_ORDER.isoformat(),
            "main_category": "Apparel",
        },
        {
            "id": 2,
            "problem": "Category decline",
            "detail": "Apparel net revenue (completed orders) falls about 30% from July to September 2026.",
            "category": "Apparel",
            "july_2026_net_revenue": jul,
            "september_2026_net_revenue": sep,
            "change_pct": round((sep - jul) / jul * 100, 1),
        },
        {
            "id": 3,
            "problem": "Stockouts",
            "detail": "10 products have on_hand = 0 for 1 to 3 weeks in September 2026; no sales in those weeks.",
            "products": [
                {
                    "sku": sku_of[p],
                    "name": name_of[p],
                    "stockout_start": start.isoformat(),
                    "stockout_end": (start + timedelta(weeks=weeks, days=-1)).isoformat(),
                    "weeks": weeks,
                }
                for p, (start, weeks) in stockouts.items()
            ],
        },
        {
            "id": 4,
            "problem": "Forecast bias",
            "detail": "Seeded forecasts for Classic Denim Jacket run about 25% above actual demand.",
            "sku": BIASED_SKU,
            "name": BIASED_NAME,
            "weeks": [w.isoformat() for w in FORECAST_WEEK_STARTS],
            "expected_bias_pct": round(FORECAST_BIAS * 100),
        },
        {
            "id": 5,
            "problem": "Pre-build need",
            "detail": (
                "Smart Bottle Pro launched July 2026; demand up about 60% in the last 4 weeks "
                "and stock covers about 9 days with nothing on order."
            ),
            "sku": PREBUILD_SKU,
            "name": PREBUILD_NAME,
            "launch_date": PREBUILD_LAUNCH.isoformat(),
            "units_last_28_days": recent,
            "units_prior_28_days": prior,
            "latest_snapshot": latest_stock["snapshot_date"].isoformat(),
            "latest_on_hand": int(latest_stock["on_hand"]),
            "latest_on_order": int(latest_stock["on_order"]),
            "lead_time_days": PREBUILD_LEAD_TIME_DAYS,
        },
    ]


# --- Orchestration ----------------------------------------------------------
def build_tables(seed: int = SEED) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """Build all six tables and the planted-issue descriptions in memory."""
    rng = np.random.default_rng(seed)
    fake = Faker("en_GB")
    fake.seed_instance(seed)

    products, meta = build_products(rng)
    stockouts = choose_stockouts(rng, products, meta)
    available = availability_matrix(meta, stockouts)
    customers = build_customers(rng, fake)

    w_orders, w_lines = build_wholesale_orders(rng, customers, products, meta, available)
    probs = retail_product_probabilities(products, meta, available)
    r_orders, r_lines = build_retail_orders(
        rng,
        customers,
        probs,
        n_orders_target=TARGET_ORDERS - len(w_orders),
        n_lines_target=TARGET_ORDER_ITEMS - len(w_lines),
        ref_offset=len(w_orders),
    )
    orders, items = finalise_orders(rng, w_orders + r_orders, w_lines + r_lines, products)

    weekly = weekly_demand(orders, items)
    prebuild_id = int(products.loc[products["sku"] == PREBUILD_SKU, "product_id"].iloc[0])
    prebuild_recent, _ = last_four_weeks_units(demand_lines(orders, items), prebuild_id)
    tables = {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": items,
        "inventory": build_inventory(rng, products, meta, weekly, stockouts, prebuild_recent),
        "forecasts": build_forecasts(rng, products, meta, weekly, stockouts),
    }
    return tables, describe_planted_issues(tables, stockouts)


# Standard SQL table definitions, so the schema can move to PostgreSQL later.
SCHEMA_SQL = """
CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    name          VARCHAR NOT NULL,
    email         VARCHAR NOT NULL,
    customer_type VARCHAR NOT NULL CHECK (customer_type IN ('retail', 'wholesale')),
    country       VARCHAR NOT NULL,
    region        VARCHAR NOT NULL,
    signup_date   DATE NOT NULL
);
CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,
    sku            VARCHAR NOT NULL UNIQUE,
    name           VARCHAR NOT NULL,
    category       VARCHAR NOT NULL,
    unit_price     DECIMAL(10, 2) NOT NULL,
    unit_cost      DECIMAL(10, 2) NOT NULL,
    launch_date    DATE NOT NULL,
    lead_time_days INTEGER NOT NULL
);
CREATE TABLE orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers (customer_id),
    order_date    DATE NOT NULL,
    channel       VARCHAR NOT NULL CHECK (channel IN ('web', 'mobile', 'marketplace')),
    status        VARCHAR NOT NULL CHECK (status IN ('completed', 'cancelled', 'returned')),
    discount_code VARCHAR
);
CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders (order_id),
    product_id    INTEGER NOT NULL REFERENCES products (product_id),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    unit_price    DECIMAL(10, 2) NOT NULL,
    discount_pct  DECIMAL(4, 3) NOT NULL
);
CREATE TABLE inventory (
    product_id    INTEGER NOT NULL REFERENCES products (product_id),
    snapshot_date DATE NOT NULL,
    on_hand       INTEGER NOT NULL,
    on_order      INTEGER NOT NULL,
    PRIMARY KEY (product_id, snapshot_date)
);
CREATE TABLE forecasts (
    product_id   INTEGER NOT NULL REFERENCES products (product_id),
    week_start   DATE NOT NULL,
    forecast_qty DOUBLE NOT NULL,
    model        VARCHAR NOT NULL,
    created_at   TIMESTAMP NOT NULL
);
"""
TABLE_ORDER = ["customers", "products", "orders", "order_items", "inventory", "forecasts"]


def write_database(tables: dict[str, pd.DataFrame], db_path: Path) -> None:
    """Write the tables to a fresh DuckDB file (built in a temp file, then swapped in)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".duckdb.tmp")
    tmp_path.unlink(missing_ok=True)
    with duckdb.connect(str(tmp_path)) as con:
        con.execute(SCHEMA_SQL)
        for name in TABLE_ORDER:  # parents before children, so foreign keys are satisfied
            con.register("frame", tables[name])
            con.execute(f"INSERT INTO {name} SELECT * FROM frame")  # name from TABLE_ORDER only
            con.unregister("frame")
    os.replace(tmp_path, db_path)


def print_summary(db_path: Path) -> None:
    """Print row counts, date range and total net revenue from the written database."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        print(f"Database written to {db_path}")
        for name in TABLE_ORDER:
            count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            print(f"  {name:<12} {count:>8,} rows")
        first, last = con.execute("SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()
        revenue = con.execute(
            """
            SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct))
            FROM order_items oi JOIN orders o USING (order_id)
            WHERE o.status = 'completed'
            """
        ).fetchone()[0]
    print(f"  order dates  {first} to {last}")
    print(f"  net revenue  {revenue:,.2f} (completed orders)")


def main() -> None:
    """Generate the database and planted-issue file, then print a summary."""
    tables, issues = build_tables(SEED)
    write_database(tables, DB_PATH)
    ISSUES_PATH.write_text(json.dumps(issues, indent=2), encoding="utf-8")
    print_summary(DB_PATH)
    print(f"Planted issues written to {ISSUES_PATH}")


if __name__ == "__main__":
    main()
