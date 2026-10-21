"""Races: the things that go wrong in a real shop with 4 PCs and a background worker."""
import threading
import time

from smartprint.server.worker import PrintWorker


def test_two_workers_never_print_the_same_job_twice(env):
    job = env.paid_job()
    env.ctx.printer.delay_s = 0.3                    # widen the race window
    results = []
    workers = [PrintWorker(env.ctx), PrintWorker(env.ctx)]
    threads = [threading.Thread(target=lambda w=w: results.append(w.run_once())) for w in workers]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert env.get(job)["state"] == "COMPLETED"
    assert len(list(env.settings.spool_dir.glob(f"{job['code']}-*"))) == 1
    assert sorted(results) == [False, True]


def test_parallel_pay_requests_create_one_live_payment(env):
    job = env.upload()
    env.configure(job)
    codes, refs = [], set()

    def go():
        r = env.client.post(f"/api/v1/jobs/{job['id']}/pay", headers={**env.h(job), "Idempotency-Key": "same"},
                            json={"method": "ONLINE"})
        codes.append(r.status_code)
        if r.status_code == 200:
            refs.add(r.json()["payment"]["reference"])

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert set(codes) <= {200, 409}                     # losers are told to retry, never a 500
    final = env.get(job)
    live = [p for p in [final["payment"]] if p["state"] == "PENDING"]
    assert len(live) == 1


def test_background_worker_thread_prints_end_to_end(env):
    env.settings.worker_enabled = True
    env.settings.worker_poll_s = 0.05
    worker = PrintWorker(env.ctx)
    worker.start()
    try:
        job = env.paid_job()
        deadline = time.time() + 10
        while time.time() < deadline and env.get(job)["state"] != "COMPLETED":
            time.sleep(0.05)
        assert env.get(job)["state"] == "COMPLETED"
        assert worker.alive
    finally:
        worker.stop()
