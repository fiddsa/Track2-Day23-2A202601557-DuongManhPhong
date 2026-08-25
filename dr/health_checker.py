"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl

CÂU HỎI PHẢI TRẢ LỜI TRƯỚC KHI VIẾT (ghi câu trả lời vào reports/postmortem.md):
  interval=5s, threshold=3 -> sớm nhất bạn có thể phát hiện outage là bao nhiêu giây?
  Con số đó nằm TRONG RTO của bạn. Muốn RTO 5 phút thì được phép chọn interval bao nhiêu?
"""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """TODO: trả về (ready, reason). Timeout PHẢI có — netblock làm request treo mãi."""
    try:
        response = httpx.get(f"{URL[region]}/readyz", timeout=timeout)
        if response.status_code == 200:
            return True, "ready"
        try:
            reasons = response.json().get("reasons", [])
            reason = ",".join(str(x) for x in reasons) or f"http_{response.status_code}"
        except (ValueError, AttributeError):
            reason = f"http_{response.status_code}"
        return False, reason
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """TODO: vòng lặp poll + phát hiện transition + ghi JSONL."""
    if interval <= 0 or timeout <= 0 or threshold < 1 or duration < 0:
        raise ValueError("interval/timeout/threshold must be positive and duration non-negative")
    out.parent.mkdir(parents=True, exist_ok=True)
    # The monitor starts from the operational assumption that both regions are
    # healthy; startup observations are not transitions and should not flood the log.
    states = {r: "HEALTHY" for r in URL}
    failures = {r: 0 for r in URL}
    started = time.monotonic()
    with out.open("a", encoding="utf-8") as log:
        while time.monotonic() - started < duration:
            cycle_started = time.monotonic()
            for region in URL:
                ready, reason = probe(region, timeout)
                failures[region] = 0 if ready else failures[region] + 1
                new_state = "HEALTHY" if ready else (
                    "UNHEALTHY" if failures[region] >= threshold else states[region]
                )
                if new_state is not None and new_state != states[region]:
                    event = {
                        "ts": time.time(), "region": region, "event": "state_change",
                        "to": new_state, "reason": reason, "consecutive_fails": failures[region],
                        "interval_s": interval, "threshold": threshold,
                    }
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                    log.flush()
                    states[region] = new_state
            remaining = interval - (time.monotonic() - cycle_started)
            if remaining > 0:
                time.sleep(min(remaining, max(0, duration - (time.monotonic() - started))))
    return states


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))
