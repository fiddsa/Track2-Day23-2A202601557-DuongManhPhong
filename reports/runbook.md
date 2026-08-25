# Runbook — Region chính down

Phạm vi: bare mode local, primary A, target B. Chạy từ root repository.

| # | Bước | Lệnh copy-paste | Biết là xong khi | Owner |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python3 chaos/kill_region.py status` | `a.ready=false` qua 3 probe; `b.alive=true` | SRE on-call |
| 2 | Mở incident, bấm giờ | `python3 dr/runbook.py --primary a --target b --backend fs` | nhập `y`; step 2 có trong `reports/runbook-run.jsonl` | Incident Commander |
| 3 | Restore state | `tail -f reports/failover-events.jsonl` | có `2_restore_snapshot`; RPO, docs_lost, version khác null | Data Platform |
| 4 | Scale và chờ ready | `curl -sf http://127.0.0.1:8002/readyz` | HTTP 200, `ready:true`, vectors > 0 | ML Platform |
| 5 | DNS/LB cutover | `curl -s http://127.0.0.1:8080/edge/state` | sau TTL, `active_region:b`; log có `5_dns_cutover` | SRE on-call |
| 6 | Golden signals | `tail -2 reports/runbook-run.jsonl` | step 6: 10 requests, p95 <100ms, error_rate=0 | Service owner |
| 7 | Đo RTO, postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, warnings rỗng, `rto_verdict:PASS` | Incident Commander |

Không chạy riêng snapshot get, ghi pool state hoặc sửa `edge/active_region`: runbook gọi quy trình 5 bước đúng một lần và chỉ cutover sau readiness.

## Rollback về Region A

Chỉ rollback khi nguyên nhân ở A đã xử lý, state A mới ít nhất bằng B, `/readyz` trả 200 ba lần và 10 golden requests không lỗi. Incident Commander phê duyệt; SRE on-call chạy runbook với `--primary b --target a`. Dừng nếu A mất readiness, error rate >1%, p95 >100ms hoặc replication lag >30s; giữ traffic ở B để tránh flap hai chiều.
