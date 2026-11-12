#!/usr/bin/env python3
"""One orders API in TypeScript, the same file on Node and on Bun, against the same API in Go.

usage: bench/run.py smoke|final <results dir>
  capacity  closed-loop req/s per variant, rivals alternating
  ram       memory while serving the same fixed traffic
Cores come from API_CPUS4, API_CPUS1, PG_CPUS, OHA_CPUS; HOST_NET=1 uses host networking (Linux only).
"""
import json, os, subprocess, sys, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODE, OUT = sys.argv[1], Path(sys.argv[2]).resolve()
SMOKE = MODE == "smoke"
ENV = dict(line.strip().split("=", 1) for line in (ROOT / "versions.env").read_text().splitlines() if "=" in line)
HOST_NET = os.environ.get("HOST_NET") == "1"
NET, PG, APP = "nvg", "nvg-db", "nvg-app"
API_PORT, PG_PORT = (8000, 55433) if HOST_NET else (18000, 5432)
CPUS4, CPUS1 = os.environ.get("API_CPUS4", ""), os.environ.get("API_CPUS1", "")
PG_CPUS, OHA_CPUS = os.environ.get("PG_CPUS", ""), os.environ.get("OHA_CPUS", "")
WARM_S, MEASURE_S = (3, 8) if SMOKE else (10, 30)
PARTS = os.environ.get("PARTS", "capacity,ram").split(",")
ROUNDS = 1 if SMOKE else 5
CONNECTIONS = int(os.environ.get("CONNECTIONS", "64"))
BUILD_ATTEMPTS = 5
# One connection per in-flight request, so the pool never caps either language; 12 did on the i7.
POOL_SIZE = os.environ.get("POOL_SIZE", str(CONNECTIONS))
ONLY_ENDPOINTS = os.environ.get("ONLY_ENDPOINTS", "").split(",") if os.environ.get("ONLY_ENDPOINTS") else None
ONLY_VARIANTS = os.environ.get("ONLY_VARIANTS", "").split(",") if os.environ.get("ONLY_VARIANTS") else None
FIXED_RATE = 500 if SMOKE else 1000
RAM_S = 6 if SMOKE else 60
ENDPOINTS = {"one_order": "/orders/[1-9][0-9]{4}"}
GO, TS_NODE, TS_BUN = "nvg-go", "nvg-ts-node", "nvg-ts-bun"
PREPARED = {"PREPARED": "1"}
FASTIFY = {"FRAMEWORK": "fastify"}
VARIANTS = {
    "go-4": (GO, CPUS4, {}),
    "ts-node-4": (TS_NODE, CPUS4, {"WORKERS": "4"}),
    "ts-node-4-prepared": (TS_NODE, CPUS4, {"WORKERS": "4", **PREPARED}),
    "ts-node-4-fastify-prepared": (TS_NODE, CPUS4, {"WORKERS": "4", **FASTIFY, **PREPARED}),
    "ts-bun-4": (TS_BUN, CPUS4, {"WORKERS": "4"}),
    "ts-bun-4-prepared": (TS_BUN, CPUS4, {"WORKERS": "4", **PREPARED}),
    "ts-bun-4-fastify-prepared": (TS_BUN, CPUS4, {"WORKERS": "4", **FASTIFY, **PREPARED}),
}
CAPACITY = {"one_order": ["ts-node-4", "ts-node-4-prepared", "ts-node-4-fastify-prepared", "ts-bun-4", "ts-bun-4-prepared", "ts-bun-4-fastify-prepared", "go-4"]}
RAM = ["ts-node-4-fastify-prepared", "ts-bun-4-fastify-prepared", "go-4"]


def sh(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def rm(*names):
    subprocess.run(["docker", "rm", "-f", *names], capture_output=True)


def cpuset(cpus):
    return ["--cpuset-cpus", cpus] if cpus else []


def net_args(port_map=None):
    if HOST_NET:
        return ["--network", "host"]
    return ["--network", NET, *(["-p", port_map] if port_map else [])]


def db_url():
    host = "127.0.0.1" if HOST_NET else PG
    return f"postgres://postgres:bench@{host}:{PG_PORT}/bench"


def build():
    for image, ctx, arg, extra in ((GO, "apps/go", "GO_IMAGE", []),
                                   (TS_NODE, "apps/ts", "NODE_IMAGE", ["-f", str(ROOT / "apps/ts/Dockerfile.node")]),
                                   (TS_BUN, "apps/ts", "BUN_IMAGE", ["--build-arg", f"NODE_IMAGE={ENV['NODE_IMAGE']}", "-f", str(ROOT / "apps/ts/Dockerfile.bun")])):
        for attempt in range(BUILD_ATTEMPTS):
            try:
                sh("docker", "build", "-q", "-t", image, "--build-arg", f"{arg}={ENV[arg]}", *extra, str(ROOT / ctx))
                break
            except subprocess.CalledProcessError as err:
                print("build failed", image, err.stderr[-400:], flush=True)
                if attempt == BUILD_ATTEMPTS - 1:
                    raise
                time.sleep(30)


def start_db():
    rm(PG, APP)
    if not HOST_NET:
        subprocess.run(["docker", "network", "create", NET], capture_output=True)
    port_args = ["-c", f"port={PG_PORT}"] if HOST_NET else []
    sh("docker", "run", "-d", "--name", PG, *net_args(), *cpuset(PG_CPUS), "-e", "POSTGRES_PASSWORD=bench", "-e", "POSTGRES_DB=bench",
       ENV["POSTGRES_IMAGE"], "-c", "shared_buffers=1GB", "-c", "max_connections=400", *port_args)
    ready = ["docker", "exec", PG, "pg_isready", "-U", "postgres", "-d", "bench", "-h", "127.0.0.1", "-p", str(PG_PORT)]
    for _ in range(120):
        if subprocess.run(ready, capture_output=True).returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("postgres did not start")
    subprocess.run(["docker", "exec", "-i", PG, "psql", "-q", "-v", "ON_ERROR_STOP=1", "-v", "orders=100000", "-U", "postgres", "-d", "bench", "-p", str(PG_PORT)],
                   input=(ROOT / "db/schema.sql").read_text(), text=True, check=True, capture_output=True)


def start_app(name):
    image, cpus, env = VARIANTS[name]
    rm(APP)
    pairs = {"DATABASE_URL": db_url(), "POOL_SIZE": POOL_SIZE, **env}
    env_args = [a for k, v in pairs.items() for a in ("-e", f"{k}={v}")]
    sh("docker", "run", "-d", "--name", APP, *net_args(f"127.0.0.1:{API_PORT}:8000"), *cpuset(cpus), *env_args, image)
    for _ in range(300):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/health", timeout=1).read()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"{name} did not start: " + subprocess.run(["docker", "logs", APP], capture_output=True, text=True).stderr[-400:])


def rss_mb():
    rows = sh("docker", "top", APP, "-eo", "pid,rss").splitlines()[1:]
    return round(sum(int(r.split()[1]) for r in rows) / 1024, 1)


# Summed RSS counts pages shared between Node's processes once per process; the cgroup counts each page once.
def mem_mb():
    cid = sh("docker", "inspect", "-f", "{{.Id}}", APP).strip()
    base = next(p for p in (Path(f"/sys/fs/cgroup/system.slice/docker-{cid}.scope"), Path(f"/sys/fs/cgroup/docker/{cid}")) if p.exists())
    stat = dict(line.split() for line in (base / "memory.stat").read_text().splitlines())
    return round((int((base / "memory.current").read_text()) - int(stat["inactive_file"])) / 1048576, 1)


def oha(path, seconds, rate=None):
    prefix = ["taskset", "-c", OHA_CPUS] if OHA_CPUS else []
    rate_args = ["-q", str(rate), "--latency-correction"] if rate else []
    raw = sh(*prefix, "oha", "-z", f"{seconds}s", "-c", str(CONNECTIONS), *rate_args, "--no-tui", "--output-format", "json", "--rand-regex-url", f"http://127.0.0.1:{API_PORT}{path}")
    return json.loads(raw)


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.samples, self.mem, self.done = [], [], threading.Event()

    def run(self):
        while not self.done.wait(2):
            self.samples.append(rss_mb())
            self.mem.append(mem_mb())


def cpu_pct(container):
    return sh("docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", container).strip()


def summary(data):
    pct = data["latencyPercentiles"]
    if pct["p50"] is None:
        raise RuntimeError("no request finished in the window; lengthen it")
    return dict(rps=data["summary"]["requestsPerSec"], p50_ms=pct["p50"] * 1000, p99_ms=pct["p99"] * 1000, codes=data["statusCodeDistribution"])


def save(kind, name, endpoint, round_no, cell):
    bad = {c: n for c, n in cell.get("codes", {}).items() if c != "200"}
    record = dict(kind=kind, variant=name, endpoint=endpoint, round=round_no, non_200=bad, **cell)
    (OUT / f"{kind}-{name}-{endpoint}-{round_no}.json").write_text(json.dumps(record))
    print(kind, name, endpoint, round_no, {k: (round(v, 1) if isinstance(v, float) else v) for k, v in cell.items() if k != "codes"}, "non200", bad, flush=True)


def capacity_cell(name, endpoint, round_no):
    start_app(name)
    idle, idle_mem = rss_mb(), mem_mb()
    path = ENDPOINTS[endpoint]
    oha(path, WARM_S)
    sampler = Sampler()
    sampler.start()
    result = {}
    worker = threading.Thread(target=lambda: result.update(summary(oha(path, MEASURE_S))))
    worker.start()
    time.sleep(MEASURE_S / 2)
    result_cpu = dict(pg_cpu=cpu_pct(PG), app_cpu=cpu_pct(APP))
    worker.join()
    sampler.done.set()
    save("capacity", name, endpoint, round_no, dict(result, idle_rss_mb=idle, peak_rss_mb=max(sampler.samples, default=idle), idle_mem_mb=idle_mem, peak_mem_mb=max(sampler.mem, default=idle_mem), **result_cpu))


def ram_cell(name, round_no):
    start_app(name)
    idle, idle_mem = rss_mb(), mem_mb()
    oha(ENDPOINTS["one_order"], WARM_S)
    sampler = Sampler()
    sampler.start()
    result = summary(oha(ENDPOINTS["one_order"], RAM_S, FIXED_RATE))
    sampler.done.set()
    save("ram", name, "one_order", round_no, dict(result, rate=FIXED_RATE, idle_rss_mb=idle, samples=sampler.samples, peak_rss_mb=max(sampler.samples, default=idle), idle_mem_mb=idle_mem, mem_samples=sampler.mem, peak_mem_mb=max(sampler.mem, default=idle_mem)))


def rotate(names, by):
    if not names:
        return []
    by %= len(names)
    return names[by:] + names[:by]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    build()
    (OUT / "env.json").write_text(json.dumps({**ENV, "mode": MODE, "pool_size": POOL_SIZE, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cpus": [CPUS4, CPUS1, PG_CPUS, OHA_CPUS],
                                              "node": sh("docker", "run", "--rm", TS_NODE, "node", "-v").strip(), "bun": sh("docker", "run", "--rm", TS_BUN, "bun", "--version").strip(),
                                              "go": sh("docker", "run", "--rm", "--entrypoint", "go", GO, "version").strip(), "oha": sh("oha", "--version").strip()}))
    start_db()
    try:
        for round_no in range(1, ROUNDS + 1):
            for endpoint, names in (CAPACITY.items() if "capacity" in PARTS else ()):
                if ONLY_ENDPOINTS and endpoint not in ONLY_ENDPOINTS:
                    continue
                for name in rotate([n for n in names if not ONLY_VARIANTS or n in ONLY_VARIANTS], round_no - 1):
                    capacity_cell(name, endpoint, round_no)
            for name in (rotate(RAM, round_no - 1) if "ram" in PARTS else ()):
                ram_cell(name, round_no)
    finally:
        rm(APP, PG)
        if not HOST_NET:
            subprocess.run(["docker", "network", "rm", NET], capture_output=True)


if __name__ == "__main__":
    main()
