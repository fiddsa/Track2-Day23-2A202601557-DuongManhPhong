"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr.health_checker import probe  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """TODO: ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    now = time.time()
    event = {"ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
             "step": n, "name": name, **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps(event, ensure_ascii=False), flush=True)
    return event


def confirm(auto: bool, msg: str) -> bool:
    """TODO: auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    return True if auto else input(f"{msg} [y/N] ").strip().lower() in {"y", "yes"}


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """TODO: 7 bước ở trên."""
    started = time.time()
    checks = []
    for attempt in range(3):
        p_ok, p_reason = probe(primary, 2.0)
        t_ok, t_reason = probe(target, 2.0)
        checks.append({"primary_ready": p_ok, "primary_reason": p_reason,
                       "target_ready": t_ok, "target_reason": t_reason})
        if p_ok:
            break
        if attempt < 2:
            time.sleep(1.0)
    outage_confirmed = len(checks) == 3 and all(not c["primary_ready"] for c in checks)
    step(1, "xac_nhan_outage", confirmed=outage_confirmed, probes=checks)
    if not outage_confirmed:
        return {"ok": False, "error": "outage_not_confirmed", "probes": checks}
    if not confirm(auto, f"Region {primary} outage confirmed. Fail over to {target}?"):
        step(2, "thong_bao_incident", confirmed=False, operator_notified_at=time.time())
        return {"ok": False, "error": "operator_declined"}

    chaos_log = pathlib.Path("chaos/chaos-events.jsonl")
    outage_ts = None
    if chaos_log.exists():
        for line in chaos_log.read_text().splitlines():
            event = json.loads(line)
            if event.get("action") == "kill" and event.get("region") == primary:
                outage_ts = event.get("ts")
    notified = time.time()
    step(2, "thong_bao_incident", confirmed=True, outage_ts=outage_ts,
         operator_notified_at=notified,
         notification_delay_s=None if outage_ts is None else round(notified - outage_ts, 3))

    # A manual confirmation must not race ahead of the anti-flap monitor.  Wait
    # for its thresholded transition when the checker is running for the drill.
    health_log = pathlib.Path("reports/health-events.jsonl")
    detect_deadline = time.monotonic() + 20.0
    while outage_ts is not None and time.monotonic() < detect_deadline:
        detected = False
        if health_log.exists():
            for line in health_log.read_text().splitlines():
                event = json.loads(line)
                if (event.get("region") == primary and event.get("to") == "UNHEALTHY"
                        and event.get("ts", 0) >= outage_ts):
                    detected = True
                    break
        if detected:
            break
        time.sleep(0.25)

    result = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", ok=result.get("ok", False), failover_result=result)
    state = result.get("state", {})
    restore = result.get("restore", {})
    step(4, "verify_state_replica", ok=bool(state.get("weights") and state.get("count", 0) > 0),
         vector_count=state.get("count"), weights=state.get("weights"),
         rpo_seconds=restore.get("rpo_seconds"), docs_lost=restore.get("docs_lost"))
    step(5, "dns_cutover", ok=result.get("ok", False), active_region=result.get("cutover"))
    if not result.get("ok"):
        step(7, "post_incident", ok=False, elapsed_s=round(time.time() - started, 3),
             measure_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl")
        return result

    latencies, errors = [], 0
    for _ in range(10):
        t0 = time.monotonic()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", timeout=3.0)
            if response.status_code != 200:
                errors += 1
        except httpx.HTTPError:
            errors += 1
        latencies.append((time.monotonic() - t0) * 1000)
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    step(6, "verify_golden_signals", requests=10, p95_latency_ms=round(p95, 2),
         error_rate=errors / 10, ok=errors == 0)
    elapsed = round(time.time() - started, 3)
    step(7, "post_incident", ok=errors == 0, elapsed_s=elapsed,
         measure_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return {**result, "golden_signals": {"p95_latency_ms": round(p95, 2),
                                          "error_rate": errors / 10}, "elapsed_s": elapsed}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
