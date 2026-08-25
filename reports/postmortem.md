# Postmortem — DR Drill Lab 23

## 1. Timeline

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-08-25T04:05:02Z | Region A bị netblock | `chaos/chaos-events.jsonl:7` |
| 2026-08-25T04:05:02Z | user đầu tiên bị ảnh hưởng (+0.1s) | `reports/drill-2-withdr.jsonl:47` |
| 2026-08-25T04:05:19Z | A chuyển UNHEALTHY (+16.8s) | `reports/health-events.jsonl:5` |
| 2026-08-25T11:05:19+07:00 | runbook bắt đầu failover sau alert | `reports/failover-events.jsonl:6` |
| 2026-08-25T11:05:28+07:00 | request đầu thành công từ B (+26.3s) | `reports/drill-2-withdr.jsonl:60` |

## 2. RTO/RPO và gap analysis

- RTO mục tiêu 300s; đo được 26.3s; gap còn dư 273.7s.
- RPO mục tiêu 300s; đo được 24.05s và 12 document mất; gap còn dư 275.95s.
- Health-check detection lớn nhất: 16.8s, bằng 63.9% RTO. Floor cấu hình là 15s; timeout và pha poll tạo phần còn lại.
- Region B active-passive cần restore snapshot và GPU warm-up trước khi nhận traffic.

## 3. Root cause — 5 whys

1. User lỗi vì edge vẫn định tuyến tới A đang netblock.
2. Edge chưa đổi tuyến vì runbook chưa cutover.
3. Runbook chờ ba probe fail liên tiếp để tránh flapping.
4. B còn phải restore state và warm GPU 6.256s.
5. Active-passive tiết kiệm compute nhưng đưa detection, replication lag và warm-up vào RTO/RPO.

Điểm dễ thất bại nhất trong outage thật là snapshot thiếu hoặc sai phiên bản embedding. Runbook chặn cutover nếu `/readyz` của B chưa 200, nên lỗi này kéo dài outage nhưng không tạo double outage.

## 4. Action items

| # | Action item | Owner | Deadline | Tác động dự kiến |
|---|---|---|---|---|
| 1 | Giữ một worker B ở `full`, cảnh báo khi `/readyz` fail | ML Platform | 2026-09-01 | giảm khoảng 6.3s RTO |
| 2 | Replicate mỗi 15s, alert khi snapshot lag >30s | Data Platform | 2026-09-08 | giảm worst-case RPO khoảng 15s |
| 3 | Game day hàng tháng, kiểm tra model VERSION khi restore | SRE | 2026-09-15 | phát hiện snapshot lỗi sớm |

## 5. Câu hỏi bắt buộc

1. `interval × threshold = 5s × 3 = 15s`, bằng 57.0% RTO; detection thực tế 16.8s bằng 63.9%.
2. Interval 1s hạ floor 12s (15s xuống 3s), nhưng tải probe tăng 5 lần và nhạy với lỗi ngắn; vẫn giữ threshold 3 để hạn chế flapping.
3. Nếu A mất vĩnh viễn, `docs_lost=12` nghĩa là 12 document có ở A nhưng chưa vào snapshot; cần replay hoặc xác định dữ liệu khách hàng phải nhập lại.

Với threshold 3, interval tối đa về mặt toán học cho riêng detection trong RTO 300s là 100s; thực tế phải nhỏ hơn để dành ngân sách restore, warm-up và TTL.
