#!/usr/bin/env python3
"""Імпорт CSV-виписки рахунку для сторінки «Надходження» (site/income.html).

Використання:
    python3 import_income.py main     "виписка.csv"            # основний рахунок
    python3 import_income.py savings  "виписка.csv"            # ощадний рахунок
    python3 import_income.py spending "виписка.csv" mybank     # інший формат з CSV_FORMATS

Перший аргумент — роль рахунку з config.ACCOUNTS. Колонки читаються так само, як в import_csv.py
(CSV_FORMATS); якщо у виписці є колонка із залишком — вкажіть її в "balance".
Що є доходом, а що переказом на ощадний чи в інвестиції, — вирішують правила INCOME_RULES у config.py.

Записується:
  * cash_flows        — надходження, перекази на ощадний, інвестиції;
  * balances          — залишок на кінець дня (якщо є колонка із залишком);
  * statement_periods — які дні покриває виписка (щоб бачити, де даних бракує).
Повторний імпорт безпечний: дублікати пропускаються.
"""
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from config import ACCOUNTS, CSV_FORMATS, INCOME_RULES
from import_csv import read_rows

DB = Path(__file__).with_name("expenses.db")


def main() -> None:
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in ACCOUNTS:
        sys.exit(__doc__)
    account = ACCOUNTS[sys.argv[1]]
    fmt = CSV_FORMATS[sys.argv[3] if len(sys.argv) == 4 else "default"]
    rows = list(read_rows(sys.argv[2], fmt))
    if not rows:
        sys.exit("У виписці немає операцій.")

    con = sqlite3.connect(DB)
    seen, added = Counter(), 0
    with con:
        for r in rows:
            inflow = -r["cents"]                       # гроші, що прийшли на рахунок, — додатні
            rule = next((x for x in INCOME_RULES if x[0].upper() in r["desc"].upper()), None)
            if rule:
                _, kind, source = rule
                amount = -inflow if kind in ("saving", "invest") else inflow
                if kind == "income" and inflow <= 0:
                    rule = None                        # «PAY» у витраті — це не зарплата
            if rule:
                key = (r["date"], r["desc"], amount)
                seen[key] += 1
                desc = r["desc"] if seen[key] == 1 else f"{r['desc']} ({seen[key]})"
                added += con.execute(
                    """INSERT OR IGNORE INTO cash_flows(account, date, description, amount_cents, kind, source)
                       VALUES (?, ?, ?, ?, ?, ?)""", (account, r["date"], desc, amount, kind, source)).rowcount
            if r["balance"] is not None:
                con.execute("INSERT OR REPLACE INTO balances(account, date, balance_cents) VALUES (?, ?, ?)",
                            (account, r["date"], r["balance"]))
        dates = sorted(r["date"] for r in rows)
        con.execute("INSERT OR REPLACE INTO statement_periods(account, date_from, date_to) VALUES (?, ?, ?)",
                    (account, dates[0], dates[-1]))
    print(f"{account}: додано {added} надходжень/переказів з {len(rows)} операцій ({dates[0]} — {dates[-1]}).")


if __name__ == "__main__":
    main()
