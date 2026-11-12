# Node vs Bun vs Go: the same orders API

Code and per-run results behind the "Should you rewrite your Node API in Go? Node vs Bun vs Go, same API" Short.

## What is tested
One endpoint, `GET /orders/{id}`: read one order and its 10 line items from Postgres with two queries, add up the
total and return JSON. Ids are random (10000-99999) per request.

| | TypeScript on Node and on Bun (`apps/ts/server.ts`) | Go (`apps/go`) |
|---|---|---|
| HTTP | Express 5.1.0, or Fastify 5.6.1 (`FRAMEWORK=fastify`) | `net/http` |
| Postgres | node-postgres (`pg`) 8.16.3, plain queries, or named queries (`PREPARED=1`) | pgx v5.11.0, `pgxpool` |
| JSON | `res.json` / Fastify's serializer | `encoding/json` |
| Processes | 4 workers (`node:cluster`), one per core | one process |

The same `server.ts` runs on both runtimes: Node strips the types itself, and Bun runs the `node_modules` npm installed
for Node (`apps/ts/Dockerfile.node`, `Dockerfile.bun`). The Go server also has endpoints the Short does not test
(`/orders` pages, `/sort`, `/resize`); `bench/run.py` only loads `/orders/{id}`.

The steps in the Short, as variants in `bench/run.py`:

| Variant | What changes |
|---|---|
| `ts-node-4`, `ts-bun-4` | out of the box: Express, plain queries |
| `ts-node-4-prepared`, `ts-bun-4-prepared` | each query gets a name, so node-postgres prepares it once per connection |
| `ts-node-4-fastify-prepared`, `ts-bun-4-fastify-prepared` | named queries, and Fastify instead of Express |
| `go-4` | net/http and pgx at their defaults |

## Setup
From `results/session3-i7/env.json`:
- **Machine:** Intel i7-13700F, RTX 4070 PC, Windows 11 + Docker, host networking.
- **Versions:** Node v24.22.2 (`node:24-trixie-slim`), Bun 1.4.4 (`oven/bun:1.4.4-debian`), go1.27.3 (`golang:1.27.3-trixie`),
  Postgres 18, oha 1.16.0.
- **CPUs:** each API container gets logical CPUs 0, 2, 4, 6 (`--cpuset-cpus`), Postgres 8-13, oha 14-19.
- **Database:** 100,000 orders with 10 items each (`db/schema.sql`), `shared_buffers=1GB`. Connection pools add up to
  64 on both sides: 16 per Node or Bun worker, 64 in Go's pool.
- **Load:** oha, 64 connections, 10 s warm-up, 30 s measured. For memory, 10 s warm-up, then 60 s at a fixed
  1,000 req/s (`-q 1000 --latency-correction`).
- **Memory:** the container's cgroup memory (`memory.current` minus `inactive_file`), sampled every 2 s; each run keeps
  the peak. For Node and Bun that is all 4 workers together.
- **Runs:** 5 rounds; within each round the variants run one after another, in an order that rotates every round.

## Results (median of 5 rounds)
Every number is in `results/session3-i7/summary.json`, made from the per-run files by `bench/summary.py`.

**Requests per second, 64 connections**

| Variant | Node | Bun | Go |
|---|---|---|---|
| Out of the box (Express, plain queries) | 22,156.4 | 23,834.5 | 31,962.8 |
| Named queries | 25,966.6 | 28,077.1 | |
| Named queries + Fastify | 28,919.5 | 29,298.3 | |

Go is 44% ahead of Node out of the box, and 11% ahead of Node and 9% ahead of Bun after both steps. Named queries add
17% on Node and 18% on Bun.

**Memory at 1,000 req/s, both steps applied (MB)**

| Node | Bun | Go |
|---|---|---|
| 332.8 | 157.2 | 19.7 |

p50 and p99 latency and each round's req/s are in the same file; `session.log` has one line per run.

## Reproduce
On Linux with Docker, Python 3, oha and `taskset` on the host. From the repo root:
```bash
HOST_NET=1 API_CPUS4=0,2,4,6 PG_CPUS=8-13 OHA_CPUS=14-19 python3 bench/run.py final results/my-session
python3 bench/summary.py results/my-session
```
`bench/run.py smoke <dir>` runs one short round. `PARTS=capacity` or `PARTS=ram` runs one test, and `ONLY_VARIANTS`
takes a comma-separated list.

## Settings people may argue about
- Out of the box, node-postgres sends each query unnamed, so Postgres parses and plans it on every call. pgx caches
  prepared statements per connection by default.
- Node and Bun run 4 processes with `node:cluster`, each with its own heap. Go runs one process; its runtime uses the 4
  CPUs of the container.
- The Node and Bun workers keep their runtime's default heap settings; Go runs with GOGC 100.
- Express and Fastify run with their default settings. Neither side uses a JSON schema or a faster JSON encoder.
- oha, Postgres and the API share one machine, each on its own CPUs.
