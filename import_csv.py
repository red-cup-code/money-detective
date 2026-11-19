#!/usr/bin/env python3
"""Імпорт CSV-виписки картки в expenses.db.

Використання:
    python3 import_csv.py "виписка.csv" "Карта 1"            # формат "default" з config.py
    python3 import_csv.py "виписка.csv" "Карта 2" mybank     # інший формат з CSV_FORMATS

Які колонки читати і як записана дата — налаштовується в config.py (CSV_FORMATS).
У базі витрата — додатна сума, повернення на карту — відʼємна.

Що вважається витратою, а що ні:
  * звичайні покупки                               -> purchase (витрата)
  * повернення на карту                            -> refund (зменшує витрати)
  * оплата кредитки (PAYMENT_WORDS)                -> payment (не витрата)
  * переказ людині з RECIPIENT_CATEGORY            -> витрата її категорії (родина, подарунки…)
  * переказ від RENT_MIN_CENTS                     -> оренда (Житло → Оренда)
  * інший переказ (TRANSFER_WORDS)                 -> transfer (не рахується, але зберігається)

Повторний імпорт тієї ж виписки безпечний: дублікати пропускаються.
"""
import csv
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from config import CSV_FORMATS, OWNER, PAYMENT_WORDS, RECIPIENT_CATEGORY, RENT_MIN_CENTS, TRANSFER_WORDS

DB = Path(__file__).with_name("expenses.db")


def cents(text: str) -> int:
    return int(Decimal(text.replace("$", "").replace(",", "").replace(" ", "").strip()) * 100)


def read_rows(path: str, fmt: dict):
    """Рядки виписки: дата, час, опис, хто платив, сума (витрата > 0), залишок."""
    with open(path, newline="", encoding=fmt["encoding"]) as f:
        for r in csv.DictReader(f, delimiter=fmt["delimiter"]):
            raw_date = (r.get(fmt["date"]) or "").strip()
            if not raw_date:
                continue
            amount = cents(r[fmt["amount"]])

            def col(k):
                return ((r.get(fmt[k]) or "").strip() or None) if fmt.get(k) else None

            yield {
                "date": datetime.strptime(raw_date, fmt["date_format"]).date().isoformat(),
                "time": col("time"),
                "desc": r[fmt["description"]].strip(),
                "holder": col("holder"),
                "cents": -amount if fmt["expense_is_negative"] else amount,
                "balance": cents(col("balance")) if col("balance") else None,
            }


def main() -> None:
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    csv_path, card_name = sys.argv[1], sys.argv[2]
    fmt = CSV_FORMATS[sys.argv[3] if len(sys.argv) == 4 else "default"]

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("INSERT OR IGNORE INTO cards(name) VALUES (?)", (card_name,))
    card_id = con.execute("SELECT id FROM cards WHERE name = ?", (card_name,)).fetchone()[0]
    rules = con.execute(
        "SELECT upper(pattern), category_id, merchant FROM category_rules ORDER BY length(pattern) DESC").fetchall()
    cat_ids = {name: cid for cid, name in con.execute("SELECT id, name FROM categories ORDER BY parent_id IS NULL")}

    added = skipped = 0
    seen, uncategorized = Counter(), set()
    with con:
        for r in read_rows(csv_path, fmt):
            desc, up, amount = r["desc"], r["desc"].upper(), r["cents"]
            key = (r["date"], r["time"], desc, amount)
            seen[key] += 1
            if seen[key] > 1:                      # дві однакові операції в один момент
                desc = f"{desc} ({seen[key]})"
            category_id = merchant = note = None
            who = next((k for k in RECIPIENT_CATEGORY if r["desc"].startswith(k)), None)
            if any(w in up for w in PAYMENT_WORDS):
                kind = "payment"
            elif who:
                kind = "purchase"
                cat_name, merchant = RECIPIENT_CATEGORY[who]
                category_id, merchant = cat_ids[cat_name], merchant or r["desc"]
            elif any(w in up for w in TRANSFER_WORDS):
                if amount >= RENT_MIN_CENTS:
                    kind, category_id, merchant, note = "purchase", cat_ids["Оренда"], "Оренда", "переказ за оренду"
                else:
                    kind = "transfer"
            else:
                kind = "purchase" if amount > 0 else "refund"
                for pattern, cat, merch in rules:
                    if pattern in up:
                        category_id, merchant = cat, merch
                        break
                else:
                    uncategorized.add(r["desc"])
            cur = con.execute(
                """INSERT OR IGNORE INTO transactions
                   (card_id, category_id, date, time, holder, description, merchant, amount_cents, kind, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (card_id, category_id, r["date"], r["time"], r["holder"] or OWNER, desc, merchant, amount, kind, note))
            added += cur.rowcount
            skipped += 1 - cur.rowcount

    print(f"Карта: {card_name}. Додано: {added}, пропущено дублікатів: {skipped}.")
    if uncategorized:
        print("Без категорії (додайте правила в schema.sql):", ", ".join(sorted(uncategorized)))


if __name__ == "__main__":
    main()
