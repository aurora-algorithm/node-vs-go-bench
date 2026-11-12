-- One schema for both APIs: orders and their 10 line items, generated from formulas.
CREATE TABLE orders (
  id          BIGINT PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  status      TEXT   NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL
);
CREATE TABLE order_items (
  order_id    BIGINT  NOT NULL REFERENCES orders(id),
  line        INT     NOT NULL,
  sku         TEXT    NOT NULL,
  qty         INT     NOT NULL,
  price_cents BIGINT  NOT NULL,
  PRIMARY KEY (order_id, line)
);

INSERT INTO orders
SELECT id,
       (id * 7919) % 50000 + 1,
       (ARRAY['NEW','PAID','SHIPPED','DELIVERED','RETURNED'])[(id % 5) + 1],
       TIMESTAMPTZ '2026-01-01 00:00:00+00' + make_interval(mins => (id % 525600)::int)
FROM generate_series(1, :orders) AS id;

INSERT INTO order_items
SELECT o.id, l,
       'SKU-' || lpad(((o.id * 31 + l) % 10000)::text, 5, '0'),
       ((o.id + l) % 5) + 1,
       ((o.id * 7 + l * 13) % 5000) + 99
FROM generate_series(1, :orders) AS o(id), generate_series(0, 9) AS l;

ANALYZE;
