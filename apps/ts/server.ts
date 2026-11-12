import cluster from 'node:cluster';
import pg from 'pg';
import type { QueryConfig } from 'pg';

pg.types.setTypeParser(pg.types.builtins.INT8, Number);

type Order = { id: number; customer_id: number; status: string; created_at: Date };
type Item = { line: number; sku: string; qty: number; price_cents: number };
type QueryName = 'order' | 'items';

const WORKERS = Number(process.env.WORKERS);
const PREPARED = process.env.PREPARED === '1';
const QUERY: Record<QueryName, string> = {
  order: 'SELECT id, customer_id, status, created_at FROM orders WHERE id = $1',
  items: 'SELECT line, sku, qty, price_cents FROM order_items WHERE order_id = $1 ORDER BY line',
};

const serve = async () => {
  const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL, max: Math.ceil(Number(process.env.POOL_SIZE) / WORKERS) });
  pool.on('error', (err) => console.error('pool error', err.message));
  const run = <T extends pg.QueryResultRow>(name: QueryName, values: unknown[]) => {
    const query: QueryConfig = PREPARED ? { name, text: QUERY[name], values } : { text: QUERY[name], values };
    return pool.query<T>(query);
  };
  const oneOrder = async (id: number) => {
    const order = await run<Order>('order', [id]);
    if (order.rowCount === 0) return null;
    const items = await run<Item>('items', [id]);
    const total_cents = items.rows.reduce((sum, it) => sum + it.qty * it.price_cents, 0);
    return { ...order.rows[0], items: items.rows, total_cents };
  };

  if (process.env.FRAMEWORK === 'fastify') {
    const { default: Fastify } = await import('fastify');
    const app = Fastify();
    app.get('/health', async () => 'ok');
    app.get<{ Params: { id: string } }>('/orders/:id', async (req, reply) => {
      const order = await oneOrder(Number(req.params.id));
      return order ?? reply.code(404).send('order not found');
    });
    await app.listen({ port: 8000, host: '0.0.0.0' });
    return;
  }
  // Each framework loads only when used, so the unused one doesn't count towards this variant's RAM.
  const { default: express } = await import('express');
  const app = express();
  app.get('/health', (_req, res) => { res.json('ok'); });
  app.get('/orders/:id', async (req, res) => {
    try {
      const order = await oneOrder(Number(req.params.id));
      if (!order) return void res.status(404).send('order not found');
      res.json(order);
    } catch (err) {
      console.error(req.url, (err as Error).message);
      res.status(500).send('server error');
    }
  });
  app.listen(8000);
};

if (cluster.isPrimary && WORKERS > 1) {
  for (let i = 0; i < WORKERS; i++) cluster.fork();
} else {
  serve().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
