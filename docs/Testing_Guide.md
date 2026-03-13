# 🧪 Hướng Dẫn Test Coverage — Tài Liệu Chuyên Nghiệp

> **Tài liệu này áp dụng cho mọi dự án**: IoT Gateway, Web API, Mobile Backend, Embedded, v.v.

---

## 1. Test Coverage là gì?

**Test Coverage** (độ phủ kiểm thử) là tỷ lệ phần trăm code được **tự động chạy qua** khi toàn bộ bộ test được thực thi.

```
Coverage = (Số dòng code được test chạy qua) / (Tổng số dòng) × 100%
```

| Coverage | Ý nghĩa |
|---|---|
| 0% | Không có test — chỉ biết lỗi khi người dùng báo |
| 30–50% | Cơ bản — test happy path, thiếu edge cases |
| 60–80% | Trung bình — đủ cho nhiều dự án internal |
| **80–90%** | **Tốt** — mức mục tiêu cho production software |
| 90–100% | Rất tốt — yêu cầu với critical systems (tài chính, y tế) |

> ⚠️ **Coverage cao ≠ code đúng.** Test phải kiểm tra đúng behavior, không chỉ chạy qua dòng code.

---

## 2. Tại sao Test Coverage quan trọng?

### 🔴 Không có test — Hậu quả thực tế

| Tình huống | Không có test | Có test |
|---|---|---|
| Sửa một hàm | Không biết đã làm hỏng gì | Test đỏ ngay — biết chính xác hỏng đâu |
| Nâng cấp package | Deploy → production crash | CI chặn trước khi deploy |
| Developer mới vào dự án | Sợ sửa code cũ | Tự tin sửa vì có safety net |
| Customer report bug | Debug mất 2–3 ngày | Viết test reproduce → fix → done |
| Code review | Review code trong đầu | Chạy test → kết quả khách quan |

### ✅ Lợi ích cụ thể

1. **Phát hiện regression sớm** — Bug được phát hiện trong vòng giây, không phải sau khi deploy
2. **Tự tin refactor** — Có test → có thể viết lại code tốt hơn mà không sợ hỏng logic
3. **Documentation sống** — Test = tài liệu sử dụng hàm, không bao giờ outdated
4. **CI/CD Gate** — Không pass test → không được deploy
5. **Tiết kiệm thời gian** — Debug thủ công tốn 10× thời gian so với test tự động

---

## 3. Các loại Test trong dự án chuyên nghiệp

```
         ┌─────────────────────────────────────┐
         │                                     │
         │        E2E / UI Tests               │  ← Ít nhất, chạy chậm
         │       (Playwright, Selenium)        │
         │                                     │
         ├─────────────────────────────────────┤
         │                                     │
         │      Integration Tests              │  ← Trung bình
         │  (Test API endpoint + DB thật)      │
         │                                     │
         ├─────────────────────────────────────┤
         │                                     │
         │         Unit Tests                  │  ← Nhiều nhất, chạy nhanh
         │    (Test từng hàm riêng lẻ)        │
         │                                     │
         └─────────────────────────────────────┘
                    Testing Pyramid
```

### 3.1 Unit Tests (Test đơn vị)

Test từng hàm/class riêng lẻ, **mock** các dependency.

- **Mục đích**: Đảm bảo logic từng hàm đúng
- **Tốc độ**: Rất nhanh (< 1ms/test)
- **Tỷ lệ đề xuất**: 70% tổng số test

```python
# Ví dụ: test hàm evaluate_condition trong anomaly_engine
def test_gt_threshold():
    assert _evaluate_condition(30.0, "gt:25") is True
    assert _evaluate_condition(20.0, "gt:25") is False
```

### 3.2 Integration Tests (Test tích hợp)

Test nhiều components kết hợp — thường dùng database thật, API thật.

- **Mục đích**: Đảm bảo các module hoạt động đúng khi kết hợp
- **Tốc độ**: Trung bình (10–500ms/test)
- **Tỷ lệ đề xuất**: 20% tổng số test

```python
# Ví dụ: test endpoint API thật với DB thật
async def test_create_mapping_api(async_client, db):
    resp = await async_client.post("/api/mappings", json={...})
    assert resp.status_code == 200
    # Verify in DB
    row = db.execute("SELECT * FROM mappings WHERE ...").fetchone()
    assert row is not None
```

### 3.3 E2E Tests (Test đầu cuối)

Test toàn bộ flow từ UI đến backend như người dùng thực.

- **Mục đích**: Đảm bảo user flow không bị hỏng
- **Tốc độ**: Chậm (1–30s/test)
- **Tỷ lệ đề xuất**: 10% tổng số test

```javascript
// Ví dụ: Playwright test
test('User can login and see dashboard', async ({ page }) => {
    await page.goto('http://localhost:8080');
    await page.fill('#username', 'admin');
    await page.fill('#password', 'admin123');
    await page.click('button[type="submit"]');
    await expect(page.locator('.dashboard')).toBeVisible();
});
```

### 3.4 Performance Tests (Test hiệu năng)

Kiểm tra hệ thống có chịu tải được không.

- **Tools**: `locust`, `k6`, `wrk`, `ab` (Apache Bench)
- **Câu hỏi**: "1000 user đồng thời thì response time bao nhiêu?"

### 3.5 Security Tests (Test bảo mật)

- **SAST**: Phân tích code tĩnh — `bandit` (Python), `semgrep`
- **Dependency check**: `safety check`, `npm audit`
- **Penetration testing**: `OWASP ZAP`

---

## 4. Tools theo Tech Stack

### Python (Backend)

| Tool | Mục đích | Lệnh |
|---|---|---|
| `pytest` | Test runner chính | `pytest` |
| `pytest-asyncio` | Test async/await | `@pytest.mark.asyncio` |
| `pytest-cov` | Đo coverage | `pytest --cov=backend` |
| `httpx` | Test FastAPI endpoints | `AsyncClient(app=app)` |
| `unittest.mock` | Mock dependency | `MagicMock(), AsyncMock()` |
| `factory_boy` | Tạo fake data | `UserFactory.create()` |
| `faker` | Random test data | `Faker().name()` |
| `bandit` | Security scan | `bandit -r backend/` |

```bash
# Cài đặt
pip install pytest pytest-asyncio pytest-cov httpx

# Chạy tests với coverage
pytest --cov=backend --cov-report=html

# Xem report trong browser
open htmlcov/index.html
```

### JavaScript/TypeScript (Frontend & Node.js)

| Tool | Mục đích |
|---|---|
| `Jest` | Test runner cho React, Node |
| `Vitest` | Nhanh hơn Jest, cho Vite projects |
| `React Testing Library` | Test React components |
| `Playwright` | E2E browser testing |
| `Cypress` | E2E với giao diện đẹp |
| `MSW (Mock Service Worker)` | Mock HTTP requests |

```bash
# Chạy tests
npm test

# Coverage
npm test -- --coverage

# E2E
npx playwright test
```

### Embedded / C/C++ (Arduino, ESP32, ROS2)

| Tool | Mục đích |
|---|---|
| `Unity` | Lightweight C unit testing |
| `Google Test (gtest)` | C++ unit testing |
| `CppUTest` | Embedded C++ testing |
| `Hardware In Loop (HIL)` | Test trên phần cứng thật |

---

## 5. Quy trình viết test chuyên nghiệp

### 5.1 Quy tắc AAA (Arrange–Act–Assert)

```python
def test_history_record():
    # Arrange: chuẩn bị dữ liệu
    store = HistoryStore(db_path=":memory:")
    store.init()

    # Act: thực hiện hành động cần test
    store.record("temp-sensor", 25.5)

    # Assert: kiểm tra kết quả
    row = store._conn.execute("SELECT value FROM point_history").fetchone()
    assert row[0] == pytest.approx(25.5)
```

### 5.2 Test các trường hợp quan trọng

Với mỗi hàm, cần test:

| Trường hợp | Ví dụ |
|---|---|
| **Happy path** | Input đúng → kết quả đúng |
| **Edge case** | Input rỗng, None, 0, chuỗi trống |
| **Error case** | File không tồn tại, DB lỗi, timeout |
| **Boundary** | Limit trên/dưới (max_records, max_size) |
| **Concurrency** | 10 thread ghi cùng lúc → không deadlock |

### 5.3 Test naming convention

```python
# Format: test_[tên_hàm]_[điều_kiện]_[kết_quả_mong_đợi]
def test_load_corrupt_json_falls_back_to_defaults():
def test_record_when_conn_none_does_not_raise():
def test_circuit_breaker_opens_after_3_failures():
```

---

## 6. CI/CD Integration

Tích hợp test vào pipeline để **tự động chặn code hỏng**:

### GitHub Actions (`.github/workflows/test.yml`)

```yaml
name: Tests

on:
  push:
    branches: [main, develop]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }

      - name: Install dependencies
        run: pip install -r requirements.txt -r requirements-dev.txt

      - name: Run tests
        run: pytest --cov=backend --cov-fail-under=70

      - name: Upload coverage report
        uses: codecov/codecov-action@v4
```

> Khi Pull Request không đạt coverage 70% → CI tự động từ chối merge.

---

## 7. Coverage Targets theo loại dự án

| Loại dự án | Target Coverage | Ghi chú |
|---|---|---|
| **Prototype / Demo** | > 20% | Ít nhất test các flow chính |
| **Internal Tool** | > 50% | Test các hàm business logic |
| **Production API** | > 70% | Bắt buộc với CI/CD |
| **Financial / IoT critical** | > 85% | Safety-critical code |
| **Medical / Automotive** | > 95% | Yêu cầu bởi ISO 26262, IEC 62443 |

---

## 8. Áp dụng cho dự án BACnet-MQTT Gateway

### Cấu trúc thư mục tests/

```
tests/
├── conftest.py               ← Fixtures dùng chung
├── test_config_manager.py    ← Corrupt config, atomic save
├── test_anomaly_engine.py    ← Rule logic, state machine
├── test_history_store.py     ← SQLite, ring buffer, thread safety
├── test_mqtt_service.py      ← Topic routing, wildcard
├── test_scheduler_service.py ← Cron parsing, BACnet guard
└── test_webhook_service.py   ← Circuit breaker, retry logic
```

### Chạy Test

```bash
# Cài test dependencies
pip install -r requirements-dev.txt

# Chạy tất cả test
pytest

# Chỉ xem coverage, không mở report
pytest --cov=backend --cov-report=term-missing

# Mở HTML report
pytest --cov=backend --cov-report=html && xdg-open htmlcov/index.html

# Chỉ chạy test của một module
pytest tests/test_anomaly_engine.py -v

# Chạy một test cụ thể
pytest tests/test_config_manager.py::TestConfigLoad::test_load_valid_config -v
```

### Coverage hiện tại (sau khi viết tests)

| Module | Tests | Mức độ phủ |
|---|---|---|
| `config_manager.py` | 8 tests | Load, save, fallback, per-entry validation |
| `anomaly_engine.py` | 10 tests | Condition eval, CRUD, state machine |
| `history_store.py` | 10 tests | Init, record, ring buffer, thread safety |
| `mqtt_service.py` | 7 tests | Routing, wildcard, publish |
| `scheduler_service.py` | 5 tests | Guard, cron, loop restart |
| `webhook_service.py` | 4 tests | Circuit breaker, 4xx abort |

---

## 9. Anti-patterns cần tránh

| Anti-pattern | Vấn đề | Giải pháp |
|---|---|---|
| Test phụ thuộc vào nhau | Test A fail → Test B fail không rõ lý do | Mỗi test độc lập, setup riêng |
| Test quá rộng (1 test, nhiều assert) | Khó biết cái gì fail | Mỗi test kiểm tra đúng 1 behavior |
| Mock quá nhiều | Test pass nhưng code thật lỗi | Giảm mock, dùng integration test |
| Không test error paths | Crash khi production gặp bad data | Luôn test `None`, exception, timeout |
| Test chỉ có happy path | 90% bug là ở edge cases | Bắt buộc test các trường hợp xấu |
| Magic numbers trong test | `assert result == 42` — tại sao 42? | Dùng constants hoặc comment rõ |

---

## 10. Tóm tắt — Checklist cho mọi dự án

- [ ] Cài `pytest` + `pytest-cov` vào `requirements-dev.txt`
- [ ] Tạo thư mục `tests/` với `conftest.py`
- [ ] Viết test cho mọi hàm **business logic quan trọng**
- [ ] Đặt mục tiêu coverage ≥ 70%
- [ ] Tích hợp CI/CD (GitHub Actions, GitLab CI)
- [ ] Mỗi PR phải pass test mới được merge
- [ ] Test error paths: None, empty, corrupt data, missing files
- [ ] Review test kỹ như review code production
