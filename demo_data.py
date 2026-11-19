#!/usr/bin/env python3
"""Створює expenses.db із вигаданими демо-даними, щоб сайт не був порожнім.

Використання:
    python3 demo_data.py          # створює expenses.db (якщо його ще немає)
    python3 demo_data.py --force  # перезаписує наявний expenses.db
    python3 build_site.py         # збирає сайт із бази

Усі люди, суми й покупки тут вигадані. Коли будете готові працювати зі своїми
виписками — видаліть expenses.db, створіть порожню базу (sqlite3 expenses.db < schema.sql)
та імпортуйте виписки (import_csv.py / import_income.py).
"""
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from config import ACCOUNTS, OWNER

ROOT = Path(__file__).parent
DB = ROOT / "expenses.db"
PARTNER = "Доллі"                       # з ким діляться спільні карти
START, END = date(2026, 6, 1), date(2026, 11, 15)
rnd = random.Random(2026)

CARDS = [  # назва, тип, валюта, з ким ділимо, частка
    ("Карта 1", "credit", "CAD", None, 0),
    ("Карта 2", "debit", "CAD", None, 0),
    ("Карта 3 (спільна)", "credit", "CAD", PARTNER, 0.5),
]

# Звичайні витрати: (опис у виписці, магазин, категорія, мін, макс, разів на тиждень, карти, час)
DAILY = [
    ("NO FRILLS #3121 TORONTO", "No Frills", "Продукти", 12, 45, 1.2, ["Карта 3 (спільна)"], "day"),
    ("COSTCO WHOLESALE W535", "Costco", "Продукти", 40, 110, 0.2, ["Карта 3 (спільна)"], "day"),
    ("FARM BOY #29 TORONTO", "Farm Boy", "Продукти", 8, 30, 0.5, ["Карта 3 (спільна)"], "day"),
    ("T&T SUPERMARKET #021", "T&T", "Продукти", 10, 35, 0.3, ["Карта 3 (спільна)"], "day"),
    ("TIM HORTONS #2241", "Tim Hortons", "Кафе й ресторани", 2, 6, 1.0, ["Карта 1"], "morning"),
    ("STARBUCKS 04512", "Starbucks", "Кафе й ресторани", 4, 8, 0.6, ["Карта 1"], "morning"),
    ("BALZACS COFFEE", "Balzac's", "Кафе й ресторани", 4, 7, 0.4, ["Карта 1"], "morning"),
    ("PIZZA NOVA #112", None, "Кафе й ресторани", 12, 28, 0.2, ["Карта 3 (спільна)"], "evening"),
    ("SUSHI SHOP QUEEN ST", None, "Кафе й ресторани", 14, 35, 0.2, ["Карта 3 (спільна)", "Карта 1"], "evening"),
    ("UBEREATS TORONTO", "Uber Eats", "Кафе й ресторани", 14, 30, 0.3, ["Карта 1"], "night"),
    ("PRESTO AUTOLOAD", "Presto", "Громадський транспорт", 10, 20, 0.3, ["Карта 1"], "morning"),
    ("UBER *TRIP HELP.UBER.COM", "Uber", "Таксі", 8, 18, 0.25, ["Карта 1"], "night"),
    ("SHOPPERS DRUG MART #0812", "Shoppers Drug Mart", "Аптека", 5, 25, 0.25, ["Карта 1", "Карта 3 (спільна)"], "day"),
    ("DOLLARAMA #1288", "Dollarama", "Дім і побут", 3, 15, 0.25, ["Карта 3 (спільна)"], "day"),
    ("IKEA ETOBICOKE", "IKEA", "Дім і побут", 20, 70, 0.06, ["Карта 3 (спільна)"], "day"),
    ("CANADIAN TIRE #142", "Canadian Tire", "Дім і побут", 10, 45, 0.1, ["Карта 3 (спільна)"], "day"),
    ("UNIQLO EATON CENTRE", "Uniqlo", "Одяг і взуття", 20, 60, 0.1, ["Карта 1"], "day"),
    ("WINNERS #217", "Winners", "Одяг і взуття", 15, 50, 0.1, ["Карта 1"], "day"),
    ("SEPHORA CANADA", "Sephora", "Краса й догляд", 15, 45, 0.08, ["Карта 1"], "day"),
    ("INDIGO BOOKS #921", "Indigo", "Книги", 12, 30, 0.08, ["Карта 1"], "day"),
    ("CINEPLEX SCOTIABANK", "Cineplex", "Розваги", 12, 25, 0.12, ["Карта 3 (спільна)"], "evening"),
    ("EVENTBRITE *MEETUP", "Eventbrite", "Івенти", 10, 30, 0.06, ["Карта 1"], "evening"),
    ("AMAZON.CA*2K4", "Amazon", "Інше", 10, 40, 0.2, ["Карта 1"], "night"),
]
# Щомісячні: (день, опис, магазин, категорія, сума, карта)
MONTHLY = [
    (1, "SEND E-TFR ***R7Q", "Оренда", "Оренда", 450.00, "Карта 2"),
    (3, "GOODLIFE FITNESS", "GoodLife Fitness", "Спорт", 29.99, "Карта 1"),
    (5, "NETFLIX.COM", "Netflix", "Підписки", 9.99, "Карта 3 (спільна)"),
    (8, "TORONTO HYDRO", "Toronto Hydro", "Комунальні й зв'язок", 32.00, "Карта 3 (спільна)"),
    (12, "SPOTIFY P2A9", "Spotify", "Підписки", 6.99, "Карта 1"),
    (18, "GOOGLE ONE", "Google One", "Підписки", 3.15, "Карта 1"),
    (20, "FIZZ MOBILE", "Fizz", "Комунальні й зв'язок", 25.00, "Карта 1"),
    (25, "Transfer out to Raymond W.", "Реймонд", "Допомога родині", 60.00, "Карта 2"),
]
TIMES = {"morning": (7, 10), "day": (11, 19), "evening": (18, 22), "night": (22, 25)}


def when(slot):
    a, b = TIMES[slot]
    h = rnd.randrange(a, b) % 24
    return f"{h:02d}:{rnd.randrange(60):02d}"


def days(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def main() -> None:
    if DB.exists() and "--force" not in sys.argv:
        sys.exit(f"{DB.name} вже існує. Щоб перезаписати демо-даними: python3 demo_data.py --force")
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    for name, typ, cur, split, share in CARDS:
        con.execute("INSERT INTO cards(name, card_type, currency, split_with, split_share) VALUES (?, ?, ?, ?, ?)",
                    (name, typ, cur, split, share))
    card_id = {n: i for i, n in con.execute("SELECT id, name FROM cards")}
    cat_id = {n: i for i, n in con.execute("SELECT id, name FROM categories ORDER BY parent_id IS NULL")}
    split_cards = {c[0] for c in CARDS if c[3]}

    tx = []

    def add(d, t, card, desc, merchant, cat, cents, kind="purchase", holder=OWNER):
        tx.append((card_id[card], cat_id.get(cat), d.isoformat(), t, holder, desc, merchant, cents, kind))

    for d in days(START, END):
        for day, desc, merchant, cat, amount, card in MONTHLY:
            if d.day == day:
                add(d, None, card, desc, merchant, cat, round(amount * 100))
        for desc, merchant, cat, lo, hi, per_week, cards, slot in DAILY:
            if rnd.random() < per_week / 7:
                card = rnd.choice(cards)
                holder = rnd.choice([OWNER, OWNER, PARTNER]) if card in split_cards else OWNER
                add(d, when(slot), card, desc, merchant, cat, round(rnd.uniform(lo, hi) * 100), holder=holder)
    # Кілька незвичних епізодів
    add(date(2026, 9, 14), "14:22", "Карта 1", "BEST BUY #946", "Best Buy", "Техніка", 129_99)
    add(date(2026, 9, 21), "10:05", "Карта 1", "AMAZON.CA*2K4 RETURN", "Amazon", "Інше", -18_50, kind="refund")
    add(date(2026, 7, 9), "16:40", "Карта 1", "COURSERA.ORG", "Coursera", "Освіта", 49_00)
    add(date(2026, 10, 2), "12:00", "Карта 2", "FONDACIYA DOPOMOGY", "Фонд Допомоги", "Благодійність", 20_00)
    add(date(2026, 11, 7), "13:10", "Карта 1", "WINNERS #217", "Winners", "Одяг і взуття", 119_99)
    add(date(2026, 11, 9), "15:35", "Карта 3 (спільна)", "IKEA ETOBICOKE", "IKEA", "Дім і побут", 89_00)
    add(date(2026, 11, 12), "19:20", "Карта 3 (спільна)", "CINEPLEX SCOTIABANK", "Cineplex", "Розваги", 31_50)
    add(date(2026, 10, 17), None, "Карта 1", "PURCHASE INTEREST", "Банк", "Комісії й відсотки", 3_12, kind="interest")

    # Оплата спільних карт: 3-го числа банк списує все, що накупили за минулий місяць
    for card in split_cards:
        cid = card_id[card]
        by_month = {}
        for r in tx:
            if r[0] == cid and r[8] in ("purchase", "refund"):
                by_month[r[2][:7]] = by_month.get(r[2][:7], 0) + r[7]
        for ym, cents in sorted(by_month.items()):
            y, m = map(int, ym.split("-"))
            pay = date(y + m // 12, m % 12 + 1, 3)
            if pay <= END:
                tx.append((cid, None, pay.isoformat(), None, OWNER, "PAYMENT - THANK YOU", None, -cents, "payment"))

    con.executemany("""INSERT INTO transactions (card_id, category_id, date, time, holder, description, merchant,
                       amount_cents, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", tx)

    # Доллі повертає свою половину; за останній місяць — лише частину
    owed = con.execute("SELECT month, share FROM v_debts_monthly ORDER BY month").fetchall()
    for i, (ym, share) in enumerate(owed):
        last = i == len(owed) - 1
        amount = 60.0 if last else share
        d = date.fromisoformat(f"{ym}-05") + timedelta(days=rnd.randrange(0, 6))
        con.execute("INSERT INTO repayments(person, date, amount_cents, for_month, note) VALUES (?, ?, ?, ?, ?)",
                    (PARTNER, min(d, END).isoformat(), round(amount * 100), ym, "e-transfer"))

    # Надходження, перекази на ощадний рахунок, інвестиції й залишки (сторінка «Надходження»)
    MAIN, SAVINGS, SPENDING = ACCOUNTS["main"], ACCOUNTS["savings"], ACCOUNTS["spending"]
    flows, bal = [], {MAIN: 1400_00, SAVINGS: 800_00, SPENDING: 90_00}
    balances = {(a, "2026-05-31"): v for a, v in bal.items()}

    def flow(acc, d, desc, cents, kind, source, delta):
        flows.append((acc, d.isoformat(), desc, cents, kind, source))
        bal[acc] += delta
        balances[(acc, d.isoformat())] = bal[acc]

    m = START
    while m <= date(2026, 10, 1):
        end = month_end(m)
        for day in (15, end.day):
            flow(MAIN, m.replace(day=day), "NORTHWIND LTD PAY", 780_00, "income", "Зарплата", 780_00)
        if m.month in (7, 9):
            c = rnd.randrange(25_00, 60_00)
            flow(MAIN, m.replace(day=22), "HEALTH INSURANCE CLAIM", c, "income", "Страхові виплати", c)
        if m.month in (6, 8, 10):
            c = rnd.randrange(20_00, 70_00)
            flow(MAIN, m.replace(day=11), "E-TRANSFER ***K2P", c, "income", "Перекази від людей", c)
        to_sav = rnd.choice([150_00, 200_00, 250_00])
        flow(MAIN, m.replace(day=16), "Transfer to savings", to_sav, "saving", "На ощадний рахунок", -to_sav)
        flow(SAVINGS, m.replace(day=17), "Deposit from main account", to_sav, "savings_in", "З основного рахунку", to_sav)
        inv = rnd.choice([80_00, 100_00, 120_00])
        flow(SAVINGS, m.replace(day=20), "Transfer to TFSA", inv, "invest", "Інвестиції", -inv)
        interest = rnd.randrange(1_00, 4_00)
        flow(SAVINGS, end, "Interest", interest, "income", "Відсотки й кешбек", interest)
        cashback = rnd.randrange(1_00, 5_00)
        flow(SPENDING, end, "Cash back", cashback, "income", "Відсотки й кешбек", cashback)
        # решта руху по рахунках — витрати й оплата карток
        bal[MAIN] -= rnd.randrange(1150_00, 1350_00)
        bal[SAVINGS] -= rnd.randrange(40_00, 90_00)
        bal[SPENDING] -= rnd.randrange(0, 10_00)
        for acc in bal:
            balances[(acc, end.isoformat())] = bal[acc]
            first = date(2026, 5, 30) if m == START else m
            con.execute("INSERT INTO statement_periods(account, date_from, date_to) VALUES (?, ?, ?)",
                        (acc, first.isoformat(), end.isoformat()))
        m = end + timedelta(days=1)

    con.executemany("INSERT INTO cash_flows(account, date, description, amount_cents, kind, source) VALUES (?, ?, ?, ?, ?, ?)", flows)
    con.executemany("INSERT OR REPLACE INTO balances(account, date, balance_cents) VALUES (?, ?, ?)",
                    [(a, d, v) for (a, d), v in balances.items()])
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"Готово: {DB.name} · {n} демо-операцій, {len(flows)} надходжень/переказів. Тепер: python3 build_site.py")


if __name__ == "__main__":
    main()
