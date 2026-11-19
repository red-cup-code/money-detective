-- Трекер витрат: схема SQLite
-- Створення бази:  sqlite3 expenses.db < schema.sql

PRAGMA foreign_keys = ON;

-- Карти / рахунки
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,          -- як ви її називаєте, напр. "Costco Mastercard"
    card_type   TEXT    NOT NULL DEFAULT 'credit'
                        CHECK (card_type IN ('credit', 'debit', 'cash', 'other')),
    currency    TEXT    NOT NULL DEFAULT 'CAD',
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    split_with  TEXT,                             -- хто ділить витрати з цієї карти, напр. 'Доллі'
    split_share REAL    NOT NULL DEFAULT 0 CHECK (split_share BETWEEN 0 AND 1),  -- його частка, 0.5 = половина
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Категорії (з підтримкою підкатегорій через parent_id)
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    UNIQUE (name, parent_id)
);

-- Транзакції
-- amount_cents: сума в центах, як у виписці банку:
--   додатна  = витрата (покупка)
--   від'ємна = надходження на карту (оплата карти, повернення, кешбек)
CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    card_id      INTEGER NOT NULL REFERENCES cards(id) ON DELETE RESTRICT,
    category_id  INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    date         TEXT    NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    time         TEXT,                            -- HH:MM, якщо банк його дає
    holder       TEXT,                            -- хто платив (власник карти / додаткова карта)
    description  TEXT    NOT NULL,                -- текст із виписки
    merchant     TEXT,                            -- очищена назва, напр. "Costco"
    amount_cents INTEGER NOT NULL,
    kind         TEXT    NOT NULL DEFAULT 'purchase'
                         CHECK (kind IN ('purchase', 'refund', 'payment', 'fee', 'interest', 'transfer')),
    status       TEXT    NOT NULL DEFAULT 'posted' CHECK (status IN ('pending', 'posted')),
    orig_cents   INTEGER,                         -- сума у валюті карти, якщо вона не основна
    orig_currency TEXT,                           -- 'EUR', 'UAH' тощо; amount_cents — завжди в основній валюті
    note         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Сторінка «Надходження» (заповнює import_income.py):
--   cash_flows        — income: дохід; saving: переказ з основного на ощадний (відʼємний — забрала назад);
--                       savings_in: ці ж гроші прийшли на ощадний; invest: з ощадного в інвестиції;
--                       own_transfer: з інших своїх рахунків (не дохід, лише для довідки)
--   balances          — залишок на рахунку на кінець дня
--   statement_periods — які дні покриває кожна виписка (щоб бачити, де даних бракує)
CREATE TABLE IF NOT EXISTS cash_flows (
    id           INTEGER PRIMARY KEY,
    account      TEXT    NOT NULL,
    date         TEXT    NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    description  TEXT    NOT NULL,
    amount_cents INTEGER NOT NULL,
    kind         TEXT    NOT NULL CHECK (kind IN ('income', 'saving', 'savings_in', 'invest', 'own_transfer')),
    source       TEXT,
    UNIQUE (account, date, description, amount_cents)
);
CREATE TABLE IF NOT EXISTS balances (
    account       TEXT    NOT NULL,
    date          TEXT    NOT NULL,
    balance_cents INTEGER NOT NULL,
    PRIMARY KEY (account, date)
);
CREATE TABLE IF NOT EXISTS statement_periods (
    account   TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to   TEXT NOT NULL,
    PRIMARY KEY (account, date_from)
);

CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_card     ON transactions(card_id, date);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category_id, date);
-- Захист від дублікатів при повторному імпорті тієї ж виписки
CREATE UNIQUE INDEX IF NOT EXISTS ux_tx_dedupe
    ON transactions(card_id, date, COALESCE(time, ''), description, amount_cents);

-- Правила автокатегоризації: якщо опис містить pattern -> категорія
CREATE TABLE IF NOT EXISTS category_rules (
    id          INTEGER PRIMARY KEY,
    pattern     TEXT    NOT NULL UNIQUE,          -- напр. 'COSTCO' (пошук через LIKE '%pattern%')
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    merchant    TEXT                              -- яку назву мерчанта підставити
);

-- Повернення боргу (напр. Доллі переказала свою частину)
CREATE TABLE IF NOT EXISTS repayments (
    id           INTEGER PRIMARY KEY,
    person       TEXT    NOT NULL,
    date         TEXT    NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    for_month    TEXT    CHECK (for_month IS NULL OR for_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
                                                  -- за який місяць розрахунок, напр. '2026-11'
    note         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Помісячний розрахунок: місяць = місяць, коли банк списав оплату карти
CREATE VIEW IF NOT EXISTS v_debts_monthly AS
WITH owed AS (
    SELECT strftime('%Y-%m', t.date) AS month, c.split_with AS person,
           SUM(-t.amount_cents)                          AS paid_cents,
           ROUND(SUM(-t.amount_cents * c.split_share))   AS cents
    FROM transactions t JOIN cards c ON c.id = t.card_id
    WHERE c.split_with IS NOT NULL AND t.kind = 'payment'
    GROUP BY month, c.split_with
), back AS (
    SELECT for_month AS month, person, SUM(amount_cents) AS cents
    FROM repayments WHERE for_month IS NOT NULL GROUP BY for_month, person
)
SELECT o.month, o.person,
       o.paid_cents / 100.0                          AS card_payments,
       o.cents / 100.0                               AS share,
       COALESCE(b.cents, 0) / 100.0                  AS repaid,
       (o.cents - COALESCE(b.cents, 0)) / 100.0      AS still_owes
FROM owed o LEFT JOIN back b ON b.month = o.month AND b.person = o.person
ORDER BY o.month DESC;

-- Борг рахується від сум, які вже списано з вашого рахунку на оплату карти
-- (транзакції kind = 'payment'), а не від покупок поточного місяця.

-- Частка за кожну оплату карти
CREATE VIEW IF NOT EXISTS v_debt_by_payment AS
SELECT t.date, c.name AS card, c.split_with AS person,
       -t.amount_cents / 100.0                              AS card_payment,
       ROUND(-t.amount_cents * c.split_share) / 100.0       AS share
FROM transactions t JOIN cards c ON c.id = t.card_id
WHERE c.split_with IS NOT NULL AND t.kind = 'payment'
ORDER BY t.date DESC;

-- Скільки кожен винен загалом: частки з оплат мінус те, що вже повернув
CREATE VIEW IF NOT EXISTS v_debts AS
WITH owed AS (
    SELECT c.split_with AS person,
           ROUND(SUM(-t.amount_cents * c.split_share)) AS cents
    FROM transactions t JOIN cards c ON c.id = t.card_id
    WHERE c.split_with IS NOT NULL AND t.kind = 'payment'
    GROUP BY c.split_with
), paid AS (
    SELECT person, SUM(amount_cents) AS cents FROM repayments GROUP BY person
)
SELECT o.person,
       ROUND(o.cents) / 100.0                              AS share,
       COALESCE(p.cents, 0) / 100.0                        AS repaid,
       (ROUND(o.cents) - COALESCE(p.cents, 0)) / 100.0     AS still_owes
FROM owed o LEFT JOIN paid p ON p.person = o.person;

-- Тільки реальні витрати (без оплат карти й переказів між своїми рахунками)
CREATE VIEW IF NOT EXISTS v_expenses AS
SELECT t.id, t.date, strftime('%Y-%m', t.date) AS month,
       c.name AS card, t.holder, COALESCE(p.name, cat.name) AS category,
       CASE WHEN p.id IS NOT NULL THEN cat.name END AS subcategory,
       t.merchant, t.description,
       t.amount_cents / 100.0 AS amount, t.kind, t.status
FROM transactions t
JOIN cards c            ON c.id = t.card_id
LEFT JOIN categories cat ON cat.id = t.category_id
LEFT JOIN categories p   ON p.id = cat.parent_id
WHERE t.kind IN ('purchase', 'refund', 'fee', 'interest');

-- Витрати по місяцях і категоріях
CREATE VIEW IF NOT EXISTS v_monthly_by_category AS
SELECT month, COALESCE(category, 'Без категорії') AS category,
       ROUND(SUM(amount), 2) AS total, COUNT(*) AS tx_count
FROM v_expenses
GROUP BY month, category
ORDER BY month DESC, total DESC;

-- Витрати по місяцях і картах
CREATE VIEW IF NOT EXISTS v_monthly_by_card AS
SELECT month, card, ROUND(SUM(amount), 2) AS total, COUNT(*) AS tx_count
FROM v_expenses
GROUP BY month, card
ORDER BY month DESC, total DESC;

-- Витрати по місяцях і людях
CREATE VIEW IF NOT EXISTS v_monthly_by_holder AS
SELECT month, COALESCE(holder, '—') AS holder, ROUND(SUM(amount), 2) AS total, COUNT(*) AS tx_count
FROM v_expenses
GROUP BY month, holder
ORDER BY month DESC, total DESC;

-- Стартові категорії
INSERT OR IGNORE INTO categories (id, name) VALUES
    (1,  'Продукти'),
    (2,  'Кафе й ресторани'),
    (3,  'Транспорт'),
    (4,  'Житло'),
    (5,  'Комунальні й зв''язок'),
    (6,  'Здоров''я'),
    (7,  'Одяг і взуття'),
    (8,  'Дім і побут'),
    (9,  'Розваги'),
    (10, 'Підписки'),
    (11, 'Подорожі'),
    (12, 'Освіта'),
    (13, 'Подарунки'),
    (14, 'Комісії й відсотки'),
    (15, 'Інше');

INSERT OR IGNORE INTO categories (name, parent_id) VALUES
    ('Пальне', 3), ('Таксі', 3), ('Громадський транспорт', 3), ('Паркування', 3),
    ('Оренда', 4),
    ('Аптека', 6), ('Лікарі', 6),
    ('Книги', 9);

-- Додаткові категорії верхнього рівня (без фіксованого id, тому вставляємо лише якщо ще немає)
INSERT INTO categories (name) SELECT v.name FROM (
          SELECT 'Благодійність' AS name UNION ALL SELECT 'Краса й догляд' UNION ALL SELECT 'Допомога родині'
          UNION ALL SELECT 'Техніка' UNION ALL SELECT 'Спорт' UNION ALL SELECT 'Івенти') v
    WHERE NOT EXISTS (SELECT 1 FROM categories c WHERE c.name = v.name AND c.parent_id IS NULL);

-- Правила автокатегоризації: шукається підрядок в описі (без урахування регістру),
-- довший шаблон має пріоритет. Додавайте свої магазини сюди.
-- Категорію шукаємо за назвою (назви унікальні серед усіх рівнів).
INSERT OR IGNORE INTO category_rules (pattern, category_id, merchant)
SELECT v.pattern, c.id, v.merchant
FROM (SELECT 'COSTCO' AS pattern, 'Продукти' AS cat, 'Costco' AS merchant
      UNION ALL SELECT 'NO FRILLS',        'Продукти', 'No Frills'
      UNION ALL SELECT 'LOBLAW',           'Продукти', 'Loblaws'
      UNION ALL SELECT 'FARM BOY',         'Продукти', 'Farm Boy'
      UNION ALL SELECT 'METRO ',           'Продукти', 'Metro'
      UNION ALL SELECT 'T&T SUPERMARKET',  'Продукти', 'T&T'
      UNION ALL SELECT 'BULK BARN',        'Продукти', 'Bulk Barn'
      UNION ALL SELECT 'SILPO',            'Продукти', 'Сільпо'
      UNION ALL SELECT 'WHOLE FOODS',      'Кафе й ресторани', 'Whole Foods'
      UNION ALL SELECT 'TOO GOOD TO GO',   'Кафе й ресторани', 'Too Good To Go'
      UNION ALL SELECT 'MCDONALD',         'Кафе й ресторани', 'McDonald''s'
      UNION ALL SELECT 'TIM HORTONS',      'Кафе й ресторани', 'Tim Hortons'
      UNION ALL SELECT 'STARBUCKS',        'Кафе й ресторани', 'Starbucks'
      UNION ALL SELECT 'BALZACS',          'Кафе й ресторани', 'Balzac''s'
      UNION ALL SELECT 'UBEREATS',         'Кафе й ресторани', 'Uber Eats'
      UNION ALL SELECT 'FRESHII',          'Кафе й ресторани', 'Freshii'
      UNION ALL SELECT 'PIZZA',            'Кафе й ресторани', NULL
      UNION ALL SELECT 'SUSHI',            'Кафе й ресторани', NULL
      UNION ALL SELECT 'IKEA',             'Дім і побут', 'IKEA'
      UNION ALL SELECT 'DOLLARAMA',        'Дім і побут', 'Dollarama'
      UNION ALL SELECT 'CANADIAN TIRE',    'Дім і побут', 'Canadian Tire'
      UNION ALL SELECT 'UNIQLO',           'Одяг і взуття', 'Uniqlo'
      UNION ALL SELECT 'WINNERS',          'Одяг і взуття', 'Winners'
      UNION ALL SELECT 'ZARA',             'Одяг і взуття', 'Zara'
      UNION ALL SELECT 'SEPHORA',          'Краса й догляд', 'Sephora'
      UNION ALL SELECT 'SHOPPERS DRUG MART', 'Аптека', 'Shoppers Drug Mart'
      UNION ALL SELECT 'REXALL',           'Аптека', 'Rexall'
      UNION ALL SELECT 'TTC',              'Громадський транспорт', 'TTC'
      UNION ALL SELECT 'PRESTO',           'Громадський транспорт', 'Presto'
      UNION ALL SELECT 'UP EXPRESS',       'Громадський транспорт', 'UP Express'
      UNION ALL SELECT 'BIKE SHARE',       'Громадський транспорт', 'Bike Share Toronto'
      UNION ALL SELECT 'UBER *TRIP',       'Таксі', 'Uber'
      UNION ALL SELECT 'NETFLIX',          'Підписки', 'Netflix'
      UNION ALL SELECT 'SPOTIFY',          'Підписки', 'Spotify'
      UNION ALL SELECT 'APPLE.COM/BILL',   'Підписки', 'Apple'
      UNION ALL SELECT 'GOOGLE ONE',       'Підписки', 'Google One'
      UNION ALL SELECT 'FIZZ',             'Комунальні й зв''язок', 'Fizz'
      UNION ALL SELECT 'ROGERS',           'Комунальні й зв''язок', 'Rogers'
      UNION ALL SELECT 'HYDRO',            'Комунальні й зв''язок', 'Toronto Hydro'
      UNION ALL SELECT 'AIRBNB',           'Подорожі', 'Airbnb'
      UNION ALL SELECT 'HOTEL',            'Подорожі', NULL
      UNION ALL SELECT 'CINEPLEX',         'Розваги', 'Cineplex'
      UNION ALL SELECT 'INDIGO',           'Книги', 'Indigo'
      UNION ALL SELECT 'COURSERA',         'Освіта', 'Coursera'
      UNION ALL SELECT 'GOODLIFE',         'Спорт', 'GoodLife Fitness'
      UNION ALL SELECT 'CLASSPASS',        'Спорт', 'ClassPass'
      UNION ALL SELECT 'EVENTBRITE',       'Івенти', 'Eventbrite'
      UNION ALL SELECT 'BEST BUY',         'Техніка', 'Best Buy'
      UNION ALL SELECT 'AMAZON',           'Інше', 'Amazon'
      UNION ALL SELECT 'ETSY',             'Інше', 'Etsy'
      UNION ALL SELECT 'MONTHLY FEE',      'Комісії й відсотки', 'Банк'
      UNION ALL SELECT 'OVERDRAFT INTEREST', 'Комісії й відсотки', 'Банк') v
JOIN categories c ON c.name = v.cat;
-- Wise навмисно без правила: кожен такий переказ варто розібрати вручну
