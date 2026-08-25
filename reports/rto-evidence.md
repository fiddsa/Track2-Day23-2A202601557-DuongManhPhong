# RTO/RPO Evidence — Lab 23

Số liệu lấy từ drill local ngày 2026-08-25, không dùng số tham khảo trong GUIDE.

## 1. Drill 1 — không có DR

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | 2026-08-25T04:00:21Z | chaos kill A | `chaos/chaos-events.jsonl:3` |
| Request fail đầu tiên | +0.2s | `ok:false` đầu sau outage | `reports/drill-1-nodr.jsonl:38` |
| Request thành công sau đó | Không có | 11 request sau outage đều lỗi tới cuối log | `reports/drill-1-nodr.jsonl:48` |
| RTO | NO_RECOVERY | timestamp loadgen | `reports/drill-1-nodr.jsonl:38` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Evidence |
|---|---:|---|
| t_outage | 0.0s | `chaos/chaos-events.jsonl:7` |
| User thấy lỗi đầu tiên | 0.1s | `reports/drill-2-withdr.jsonl:47` |
| Health check phát hiện | 16.8s | `reports/health-events.jsonl:5` |
| Snapshot restore xong | 17.1s | `reports/failover-events.jsonl:7` |
| Region phụ ready | 23.4s | `reports/failover-events.jsonl:9` |
| DNS cutover | 23.4s | `reports/failover-events.jsonl:10` |
| **RTO đo được** | **26.3s** | `reports/drill-2-withdr.jsonl:60` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---:|---:|---|
| RTO — Inference API | 26.3s | 300s | PASS |
| RPO — Vector DB | 24.05s / 12 docs | 300s | PASS |

RPO và phiên bản embedding nằm tại `reports/failover-events.jsonl:7`.

## 3. Breakdown RTO

| Thành phần | Giây | Nguồn | Cách giảm |
|---|---:|---|---|
| Health-check detection | 16.8s | floor 5.0s × 3 = 15.0s, thêm timeout/lệch pha; `reports/health-events.jsonl:5` | giảm interval/timeout, giữ threshold |
| Snapshot restore | 0.0s (làm tròn 0.1s) | `reports/failover-events.jsonl:6` → `reports/failover-events.jsonl:7` | snapshot nhỏ hơn |
| GPU pool warm-up | 6.3s | `waited_s:6.256`; `reports/failover-events.jsonl:9` | giữ warm capacity |
| DNS/LB TTL + chu kỳ request | 3.2s | cutover/ready tới request B; `reports/failover-events.jsonl:10`, `reports/drill-2-withdr.jsonl:60` | giảm TTL, tăng probe |
| **Tổng** | **26.3s** | RTO phía user | — |
