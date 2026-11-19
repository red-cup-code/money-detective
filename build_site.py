#!/usr/bin/env python3
"""Збирає сайт «Справа про зниклі гроші» з expenses.db.

Налаштування (імʼя, місто) — у config.py.

Використання:
    python3 build_site.py

Бере шаблон site/template.html, вбудовує в нього дані з бази
і записує сторінки:
  * site/index.html   — уся справа;
  * site/income.html  — надходження, залишки на кінець місяця і скільки відкладено
                        (шаблон site/income-template.html, дані з cash_flows / balances).
Запускайте після кожного імпорту виписки.
"""
import json
import re
import sqlite3
import string
from datetime import date, datetime, timedelta
from pathlib import Path

from config import ACCOUNTS, CITY, INCOME_ACCOUNTS, OWNER

MAIN, SAVINGS = ACCOUNTS["main"], ACCOUNTS["savings"]

ROOT = Path(__file__).parent
DB = ROOT / "expenses.db"
TEMPLATE = ROOT / "site" / "template.html"
OUT = ROOT / "site" / "index.html"
INCOME_TEMPLATE = ROOT / "site" / "income-template.html"
OUT_INCOME = ROOT / "site" / "income.html"


def pretty(desc: str) -> str:
    """'MARTINO'S PIZZA INC.' -> 'Martino's Pizza Inc.'"""
    return string.capwords(desc.lower()) if desc.isupper() else desc


def month_end(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    return (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).isoformat()


def income_data(con) -> dict:
    """Помісячно: що надійшло (за джерелами), скільки відкладено, залишок на рахунках на кінець місяця."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'cash_flows'").fetchone():
        return {"months": [], "flows": [], "accounts": INCOME_ACCOUNTS, "main": MAIN, "savings": SAVINGS}
    periods = {}
    for acc, a, b in con.execute("SELECT account, date_from, date_to FROM statement_periods"):
        periods.setdefault(acc, []).append((a, b))

    def covered(acc: str, day: str) -> bool:
        return any(a <= day <= b for a, b in periods.get(acc, []))

    def full_month(acc: str, ym: str) -> bool:
        """Виписки покривають місяць (виписка може закінчитись 30-го — 1–2 дні без даних не рахуємо)."""
        d, end, gaps = date.fromisoformat(ym + "-01"), date.fromisoformat(month_end(ym)), 0
        while d <= end:
            gaps += not covered(acc, d.isoformat())
            d += timedelta(days=1)
        return gaps <= 2

    # Куди пішли гроші з основного рахунку на ощадний: кожен переказ шукаємо серед депозитів на ощадному
    # (та сама сума, ±7 днів). Не знайшли, хоча виписка ощадного рахунку ці дні покриває, — отже,
    # гроші пішли напряму в інвестиції. Немає виписки ощадного рахунку — не знаємо куди.
    savings_in = [[d, c, False] for d, c in con.execute(
        "SELECT date, amount_cents FROM cash_flows WHERE kind = 'savings_in' ORDER BY date")]
    days = lambda a, b: abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    inv_by = {}          # місяць -> {куди: сума}
    add = lambda ym, k, v: inv_by.setdefault(ym, {}).__setitem__(k, inv_by.get(ym, {}).get(k, 0) + v / 100)
    for d, c in con.execute("SELECT date, amount_cents FROM cash_flows WHERE kind = 'saving' ORDER BY date"):
        hit = next((w for w in savings_in if not w[2] and w[1] == c and days(w[0], d) <= 7), None)
        if hit:
            hit[2] = True                                   # лягло на Chequing — це ще не інвестиція
        elif covered(SAVINGS, d):
            add(d[:7], "direct", c)
        else:
            add(d[:7], "unknown", c)
    for d, desc, c in con.execute("SELECT date, description, amount_cents FROM cash_flows WHERE kind = 'invest'"):
        m = re.search(r"to (Non-registered|RRSP|TFSA|FHSA)", desc)
        add(d[:7], m[1] if m else "direct", c)

    all_days = [d for ps in periods.values() for p in ps for d in p]
    first, last = min(all_days)[:7], max(all_days)[:7]
    months, ym = [], first
    while ym <= last:
        flows = con.execute("""SELECT kind, source, SUM(amount_cents) FROM cash_flows
                               WHERE strftime('%Y-%m', date) = ? GROUP BY kind, source""", (ym,)).fetchall()
        income = {src: c / 100 for kind, src, c in flows if kind == "income"}
        tot = lambda k: sum(c for kind, _, c in flows if kind == k) / 100
        to_sav, savings_in, invest, own_in = tot("saving"), tot("savings_in"), tot("invest"), tot("own_transfer")
        end = month_end(ym)
        prev_end = (date.fromisoformat(ym + "-01") - timedelta(days=1)).isoformat()

        def bal_at(acc, day):
            r = con.execute("""SELECT date, balance_cents FROM balances WHERE account = ? AND date <= ?
                               ORDER BY date DESC LIMIT 1""", (acc, day)).fetchone()
            return {"v": r[1] / 100, "asof": r[0], "exact": covered(acc, day)} if r else None

        bal = {acc: bal_at(acc, end) for acc in INCOME_ACCOUNTS}
        start = {acc: bal_at(acc, prev_end) for acc in INCOME_ACCOUNTS}
        full = {acc: full_month(acc, ym) for acc in INCOME_ACCOUNTS}
        # Реально відкладено = зміна залишків + інвестиції − гроші, що прийшли з інших своїх рахунків.
        exact = (full[MAIN] and full[SAVINGS]
                 and all(b and b["exact"] for b in [*bal.values(), *start.values()]))
        if exact:
            delta = sum(bal[a]["v"] for a in INCOME_ACCOUNTS) - sum(start[a]["v"] for a in INCOME_ACCOUNTS)
            invested = sum(v for k, v in inv_by.get(ym, {}).items() if k != "unknown")
            saved, method = delta + invested - own_in, "exact"
        elif full[MAIN]:
            delta = invested = None
            saved, method = to_sav - own_in, "approx"      # лише за переказами з основного рахунку на ощадний
        else:
            delta = invested = saved = method = None
        months.append({
            "m": ym, "income": income, "toSav": to_sav, "ownIn": own_in,
            "delta": delta, "invested": invested,
            "invest": {k: round(v, 2) for k, v in inv_by.get(ym, {}).items()},
            "saved": round(saved, 2) if saved is not None else None, "method": method,
            "balance": bal, "full": full,
        })
        y, m = map(int, ym.split("-"))
        ym = f"{y + m // 12}-{m % 12 + 1:02d}"
    # Місяць, де немає жодної виписки, не показуємо
    months = [x for x in months if any(x["full"].values()) or x["income"]]
    flows = [{"d": d, "acc": acc, "desc": desc, "a": c / 100, "k": k, "src": src}
             for d, acc, desc, c, k, src in con.execute(
                 "SELECT date, account, description, amount_cents, kind, source FROM cash_flows ORDER BY date DESC, id DESC")]
    return {"months": months, "flows": flows, "accounts": INCOME_ACCOUNTS, "main": MAIN, "savings": SAVINGS}


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    tx = [
        {
            "d": r["date"], "t": r["time"], "h": r["holder"], "c": r["card"],
            "cat": r["category"], "sub": r["subcategory"],
            "m": r["merchant"] or pretty(r["description"]), "desc": r["description"],
            "a": r["amount_cents"] / 100, "k": r["kind"],
            "s": r["split_share"],          # частка, яку покриває інша людина (0 — особиста карта)
            "o": r["orig_cents"] / 100 if r["orig_cents"] is not None else None,   # сума у валюті карти
            "oc": r["orig_currency"],
        }
        for r in con.execute("""
            SELECT t.date, t.time, t.holder, c.name AS card,
                   COALESCE(p.name, cat.name, 'Без категорії') AS category,
                   CASE WHEN p.id IS NOT NULL THEN cat.name END AS subcategory,
                   t.merchant, t.description, t.amount_cents, t.kind, c.split_share,
                   t.orig_cents, t.orig_currency
            FROM transactions t
            JOIN cards c ON c.id = t.card_id
            LEFT JOIN categories cat ON cat.id = t.category_id
            LEFT JOIN categories p   ON p.id = cat.parent_id
            WHERE t.kind IN ('purchase', 'refund', 'fee', 'interest')
            ORDER BY t.date DESC, t.time DESC""")
    ]

    debt_monthly = [
        {"month": r["month"], "person": r["person"], "paid": r["card_payments"],
         "share": r["share"], "repaid": r["repaid"], "owes": r["still_owes"]}
        for r in con.execute("SELECT * FROM v_debts_monthly ORDER BY month")
    ]
    payments = [
        {"d": r["date"], "card": r["card"], "paid": r["card_payment"], "share": r["share"]}
        for r in con.execute("SELECT * FROM v_debt_by_payment")
    ]
    repayments = [
        {"d": r["date"], "person": r["person"], "a": r["amount_cents"] / 100,
         "month": r["for_month"], "note": r["note"]}
        for r in con.execute("SELECT * FROM repayments ORDER BY date DESC")
    ]
    split_cards = [r[0] for r in con.execute(
        "SELECT name FROM cards WHERE split_with IS NOT NULL ORDER BY id")]
    # Покупки, які банк ще не списав з рахунку (орієнтовно: покупки мінус оплати)
    pending = [
        {"card": r["name"], "a": r["cents"] / 100, "share": r["share"]}
        for r in con.execute("""
            SELECT c.name, c.split_share AS share,
                   SUM(CASE WHEN t.kind IN ('purchase','refund','fee','interest') THEN t.amount_cents ELSE 0 END)
                 + SUM(CASE WHEN t.kind = 'payment' THEN t.amount_cents ELSE 0 END) AS cents
            FROM cards c JOIN transactions t ON t.card_id = c.id
            WHERE c.split_with IS NOT NULL
            GROUP BY c.id HAVING cents > 0""")
    ]

    data = {
        "owner": OWNER,
        "city": CITY,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tx": tx,
        "debtMonthly": debt_monthly,
        "payments": payments,
        "repayments": repayments,
        "splitCards": split_cards,
        "splitWith": con.execute(
            "SELECT split_with FROM cards WHERE split_with IS NOT NULL LIMIT 1").fetchone()[0]
            if split_cards else None,
        "pending": pending,
    }

    html = TEMPLATE.read_text(encoding="utf-8")
    if "/*__DATA__*/null" not in html:
        raise SystemExit("У шаблоні немає мітки /*__DATA__*/null")

    def write(out: Path, d: dict) -> None:
        payload = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
        out.write_text(html.replace("/*__DATA__*/null", payload), encoding="utf-8")

    write(OUT, data)
    print(f"Готово: {OUT.relative_to(ROOT)} · {len(tx)} витрат · {len(payments)} списань")
    inc = {"owner": OWNER, "generated": data["generated"], **income_data(con)}
    page = INCOME_TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(inc, ensure_ascii=False).replace("</", "<\\/")
    OUT_INCOME.write_text(page.replace("/*__DATA__*/null", payload), encoding="utf-8")
    print(f"Готово: {OUT_INCOME.relative_to(ROOT)} · {len(inc['months'])} міс. · {len(inc['flows'])} надходжень/переказів")


if __name__ == "__main__":
    main()
