# Ghi Chú Kỹ Thuật & Tư Duy Xử Lý Dự Án

> Tài liệu này ghi lại các kỹ thuật, khó khăn và cách giải quyết trong quá trình phát triển BACnet-MQTT Gateway — với mục tiêu để bạn hiểu **bản chất** thay vì chỉ biết cách làm.

---

## Mục Lục

1. [Tư Duy: Cách Đọc Hiểu Một Dự Án Lạ](#1-tư-duy-cách-đọc-hiểu-một-dự-án-lạ)
2. [Vấn Đề Race Condition — Đồng Thời Giết Chết Hệ Thống](#2-vấn-đề-race-condition)
3. [Blocking vs Non-Blocking — Sự Khác Biệt Sống Còn](#3-blocking-vs-non-blocking)
4. [Class Variable vs Instance Variable — Bẫy Tinh Vi Nhất](#4-class-variable-vs-instance-variable)
5. [Thread Safety trong SQLite](#5-thread-safety-trong-sqlite)
6. [Broadcast Response Misrouting — Bug Ở Tầng Protocol](#6-broadcast-response-misrouting)
7. [Tư Duy Debug: Từ Triệu Chứng Đến Nguyên Nhân Gốc](#7-tư-duy-debug)
8. [Lazy Initialization — Khởi Động Muộn Cố Ý](#8-lazy-initialization)
9. [Index-Based Fallback — Chiến Lược Dự Phòng](#9-index-based-fallback)
10. [RAM Guardian — Hệ Thống Tự Bảo Vệ](#10-ram-guardian)

---

## 1. Tư Duy: Cách Đọc Hiểu Một Dự Án Lạ

### Vấn đề
Khi tiếp nhận một codebase mới, cảm giác choáng ngợp là bình thường. Câu hỏi đầu tiên thường là: *"Bắt đầu từ đâu?"*

### Phương pháp tiếp cận theo tầng

```
Tầng 1: Kiến trúc tổng quan
├── Dự án này LÀMGÌ? (đọc README, docker-compose)
├── Các thành phần chính là gì? (liệt kê files)
└── Dữ liệu đi theo hướng nào? (trace data flow)

Tầng 2: Entry point
├── Ứng dụng khởi động từ đâu? (main.py, lifespan)
├── Các service được khởi tạo theo thứ tự nào?
└── Background tasks là gì?

Tầng 3: Core logic
├── File nào "làm việc thực sự"? (gateway_engine.py)
├── Vòng lặp chính ở đâu? (_polling_loop)
└── Dữ liệu được transform như thế nào?

Tầng 4: Deep dive
└── Chỉ đọc sâu vào file cần fix/hiểu
```

### Bài học
> **Không bao giờ đọc code từ đầu đến cuối tuần tự.** Đọc theo chiều rộng trước (tất cả files, mỗi file vài dòng), sau đó mới đọc sâu vào điểm cần quan tâm. Não người xử lý tốt hơn khi có bức tranh tổng thể trước.

---

## 2. Vấn Đề Race Condition

### Triệu chứng
Hệ thống hoạt động tốt khi có 2-3 points được map; khi tăng lên 20-30 points, xuất hiện lỗi ngẫu nhiên `no-response` từ thiết bị BACnet — dù thiết bị hoàn toàn bình thường.

### Phân tích

Đây là đoạn code **nguy hiểm** trong vòng polling loop:

```python
# Code cũ — nguy hiểm trên MSTP
for mapping in mappings:
    if elapsed >= interval:
        asyncio.create_task(self._poll_single(mapping))  # ❌
```

`asyncio.create_task()` tạo một **task độc lập chạy song song**. Với 20 points, có thể có 20 tasks cùng gọi `network.read()` gần như đồng thời.

**Vấn đề vật lý:** Mạng BACnet/MSTP là **half-duplex bus** — chỉ một thiết bị được phép truyền tại một thời điểm. Khi nhiều requests đến nhanh hơn hệ thống có thể xử lý, BAC0's internal request queue bị saturation → timeout → `no-response`.

```
Không có lock:            Với lock (fix):
                          
t=0  Task A → read() ──►  t=0  Task A → read() ──►
t=0  Task B → read() ──►  t=0  Task B → WAIT...
t=0  Task C → read() ──►  t=0  Task C → WAIT...
                          t=1  Task B → read() ──►
     ↑ Tất cả tranh nhau  t=2  Task C → read() ──►
     → queue tràn          ↑ Tuần tự, ổn định
```

### Giải pháp

```python
# Thêm lock vào __init__
self._bacnet_read_lock = asyncio.Lock()

# Wrapper method serialize tất cả BACnet reads
async def _poll_single_locked(self, mapping):
    async with self._bacnet_read_lock:
        await self._poll_single(mapping)

# Dùng wrapper trong polling loop
asyncio.create_task(self._poll_single_locked(mapping))  # ✅
```

### Bản chất kỹ thuật: `asyncio.Lock`

`asyncio.Lock` là **cooperative lock** — nó không block thread (như `threading.Lock`), mà nó `await` đợi. Khi Task B gọi `async with lock:`, nó sẽ yield control về event loop cho đến khi Task A release lock. Điều này hoàn toàn phù hợp với asyncio's single-threaded concurrency model.

> **Tư duy quan trọng:** Race condition KHÔNG yêu cầu multi-threading. Trong asyncio, các tasks yield control tại `await`, và nếu hai tasks cùng truy cập shared resource giữa hai `await`, đó vẫn là race condition.

---

## 3. Blocking vs Non-Blocking

### Triệu chứng
Khi MQTT broker bị chậm hoặc mất kết nối tạm thời, **toàn bộ BACnet polling loop bị đứng** lên đến 5 giây.

### Phân tích

```python
# Code cũ — BLOCKING CALL trong asyncio event loop
result = self._client.publish(topic, payload, qos=1)
result.wait_for_publish(timeout=5)  # ❌ Block 5 giây!
```

**Nguyên tắc cốt lõi của asyncio:** Event loop là **single-threaded**. Chỉ một coroutine chạy tại một thời điểm. Khi một coroutine block (gọi I/O đồng bộ), **không có gì khác chạy được** — kể cả timer, WebSocket, BACnet reads.

```
asyncio event loop (single thread):

[BACnet Poll Task] → await read() → yield → [MQTT Publish] → wait_for_publish(5s) → STUCK!
                                              ↑
                                    Event loop bị treo 5s
                                    BACnet polls không chạy
                                    WebSocket không phản hồi
                                    API không nhận request
```

### Giải pháp

Paho-MQTT có **network loop thread riêng** (`loop_start()`). Thread này tự xử lý ACK của QoS 1. Ta không cần đợi:

```python
# Fix: fire-and-forget, paho xử lý internally
self._client.publish(topic, payload, qos=1)  # ✅ Non-blocking
# Paho's loop thread tự xử lý retransmission và ACK
```

### Khi nào cần wait?

Nếu thực sự cần xác nhận delivery (ví dụ đối với critical commands), dùng executor để không block event loop:

```python
async def publish_async(self, topic, payload):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        self._executor,              # Thread pool
        lambda: self.publish(topic, payload)  # Chạy trong thread riêng
    )
    # ✅ Event loop không bị block, blocking work chạy trong thread pool
```

> **Quy tắc vàng của asyncio:** Bất kỳ hàm nào có thể block (I/O, sleep, network, disk) đều PHẢI là `async/await` hoặc chạy trong `executor`. Nếu không, bạn đang stall toàn bộ hệ thống.

---

## 4. Class Variable vs Instance Variable

### Đây là bẫy tinh vi nhất trong Python

```python
class BacnetService:
    _cov_callbacks: dict = {}  # ❌ CLASS variable — chia sẻ giữa TẤT CẢ instances!

    def __init__(self):
        pass
```

vs:

```python
class BacnetService:
    def __init__(self):
        self._cov_callbacks: dict = {}  # ✅ INSTANCE variable — mỗi instance độc lập
```

### Tại sao nguy hiểm trong dự án này?

Trong `main.py`, khi người dùng click **Discover** sau khi network đã chạy:

```python
# main.py — Rebuild BACnet service khi reconnect
bacnet_service = BacnetService(config)      # Instance MỚI
gateway_engine._bacnet = bacnet_service     # Gán instance mới vào engine
```

Nếu `_cov_callbacks` là class variable:
- Instance cũ và instance mới **dùng CÙNG một dict**
- Subscriptions của instance cũ vẫn còn trong dict của instance mới
- Khi unsubscribe, xóa ở instance này sẽ ảnh hưởng instance kia

### Demo trực quan

```python
class Service:
    data = []  # class variable

s1 = Service()
s2 = Service()
s1.data.append("hello")

print(s2.data)  # ['hello'] ← s2 BỊ ẢNH HƯỞNG!

# ---

class ServiceFixed:
    def __init__(self):
        self.data = []  # instance variable

s1 = ServiceFixed()
s2 = ServiceFixed()
s1.data.append("hello")

print(s2.data)  # [] ← s2 KHÔNG bị ảnh hưởng ✅
```

> **Quy tắc:** Trong Python, nếu bạn khai báo attribute ở cấp class, nó là **shared state**. Luôn khai báo mutable state (list, dict, set) trong `__init__`. Chỉ dùng class variable cho constants hoặc cấu hình không đổi.

---

## 5. Thread Safety trong SQLite

### Vấn đề

`history_store.py` dùng SQLite với `check_same_thread=False`:

```python
conn = sqlite3.connect(db_path, check_same_thread=False)
```

Đây là sự cho phép SQLite chấp nhận calls từ nhiều threads — nhưng **không có nghĩa là safe!** Nó chỉ tắt cảnh báo của Python, còn việc serialize vẫn là trách nhiệm của bạn.

### Các nguồn gọi đồng thời vào HistoryStore

```
asyncio event loop       scheduler thread      cleanup loop
      │                        │                    │
      ▼                        ▼                    ▼
  record()             log_event()         _run_retention_cleanup()
      │                        │                    │
      └────────────────────────┴────────────────────┘
                               │
                          SQLite writes ← Không được concurrent!
```

### SQLite và WAL mode

Dù đã bật **WAL (Write-Ahead Logging)**:
```python
conn.execute("PRAGMA journal_mode=WAL")
```

WAL cho phép **nhiều readers + một writer** cùng lúc, nhưng hai writers đồng thời vẫn sẽ bị `database is locked` error.

### Giải pháp: `threading.RLock`

```python
import threading

class HistoryStore:
    def __init__(self):
        self._write_lock = threading.RLock()  # Reentrant Lock

    def record(self, mapping_id, value):
        with self._write_lock:   # Acquire lock
            self._conn.execute(...)
            self._conn.commit()
            self._apply_ring_buffer(...)  # Gọi method khác — RLock cho phép!
        # Lock released

    def _apply_ring_buffer(self, mapping_id):
        with self._write_lock:   # Acquire CÙNG lock — OK vì là RLock!
            self._conn.execute(...)
```

**Tại sao RLock (Reentrant) chứ không phải Lock thông thường?**

```
Lock thường:
Thread 1 → record() → acquire lock → _apply_ring_buffer() → acquire lock → DEADLOCK! ❌
                                      (cùng thread muốn lock đã giữ)

RLock:
Thread 1 → record() → acquire lock → _apply_ring_buffer() → acquire lại lần 2 → OK ✅
                                      (RLock đếm số lần acquire, chỉ release khi đếm về 0)
```

> **Tư duy:** Khi một function có thể gọi function khác mà cả hai đều cần lock, dùng **RLock**. Khi chắc chắn không có re-entry, dùng **Lock** (nhẹ hơn một chút).

---

## 6. Broadcast Response Misrouting

### Đây là bug ở tầng protocol — khó nhất để debug

### Triệu chứng
Tất cả BACnet reads đều trả về `no-response` dù Wireshark thấy thiết bị có gửi phản hồi về.

### Phân tích

Trong BACnet/IP, khi gateway gửi request, thiết bị phải reply về địa chỉ **unicast** của gateway. Nhưng một số thiết bị (do bug firmware hoặc BBMD config sai) lại gửi reply về **broadcast address** (`10.25.7.255`).

```
Gateway (10.25.7.21) ──ReadProperty──► Device (10.25.7.100)
                                             │
Gateway (10.25.7.21) ◄── gói tin về 10.25.7.255 (broadcast!)
                                             │
BACpypes3 nhận gói trên broadcast socket
└── Tag source là LocalBroadcast()
└── Không match với pending unicast request
└── Request timeout → "no-response"
```

BACpypes3 sử dụng **hai socket riêng biệt**: một cho unicast, một cho broadcast. Gói tin đến broadcast socket được tag là `LocalBroadcast()` → không match correlation với unicast request đang chờ.

### Giải pháp: Monkey-patch

```python
# Patch broadcast socket để nó "nghĩ" nó là local unicast socket
server.broadcast_protocol.destination = server.local_protocol.destination
```

Sau patch: gói tin đến broadcast socket sẽ được tag là `LocalAddress()` → match được với pending request.

### Tại sao gọi là "monkey-patch"?

Monkey-patch là kỹ thuật thay đổi hành vi của code ở runtime, không sửa source. Thường dùng khi:
- Không có quyền sửa thư viện gốc
- Bug ở thư viện bên thứ ba chưa được fix
- Cần workaround nhanh mà không đổi API

> **Tư duy quan trọng:** Khi Wireshark thấy response mà code thấy timeout → vấn đề là ở **xử lý gói tin sau khi nhận**, không phải network. Debug từ tầng cao (application) xuống tầng thấp (protocol/socket) để tìm đúng điểm gãy.

---

## 7. Tư Duy Debug

### Framework: Từ Triệu Chứng → Nguyên Nhân Gốc

```
1. OBSERVE (Quan sát)
   "Triệu chứng là gì?"
   "Khi nào xảy ra? Liên tục hay ngẫu nhiên?"
   "Có pattern không? (số lượng, thời gian, điều kiện)"

2. HYPOTHESIZE (Giả thuyết)
   "Có thể do A, B, hoặc C"
   Ưu tiên giả thuyết đơn giản nhất trước

3. ISOLATE (Cô lập)
   "Làm sao tái hiện được vấn đề một cách consistent?"
   "Có thể minimize thành test case nhỏ nhất không?"

4. TEST (Kiểm tra)
   Test từng giả thuyết — chỉ thay đổi MỘT biến mỗi lần

5. ROOT CAUSE (Nguyên nhân gốc)
   "5 whys": Cứ hỏi 'Tại sao?' 5 lần để đến nguyên nhân thực sự
   Ví dụ:
   - "Tại sao lỗi no-response?" → Poll quá nhiều
   - "Tại sao poll quá nhiều?" → Không có rate limiting
   - "Tại sao không rate limit?" → asyncio.create_task chạy song song
   - "Tại sao không serialize?" → Không biết BAC0 cần serialize
   - "Tại sao không biết?" → Chưa đọc kỹ BAC0 internals ← ROOT CAUSE

6. FIX (Sửa)
   Sửa nguyên nhân gốc, không chỉ sửa triệu chứng
```

### Ví dụ thực tế: "BACnet device không response"

```
Triệu chứng: read() timeout sau 3 giây
Kiểm tra network: Wireshark thấy gói đến → network OK
Kiểm tra binding: Port 47808 đang listen → OK
Kiểm tra response: Gói có destination = broadcast IP ← FOUND!
Root cause: Thiết bị reply về broadcast thay vì unicast
Fix: Patch broadcast socket destination
```

> **Quy tắc vàng:** **"Đừng tin vào log, hãy tin vào packet."** Wireshark/tcpdump luôn là nguồn sự thật cuối cùng khi debug network.

---

## 8. Lazy Initialization

### Vấn đề
Nếu khởi tạo BACnet ngay khi app start, nhiều vấn đề xảy ra:
- Port 47808 bị giữ → công cụ debug (YABE, Wireshark) không dùng được
- Nếu config sai, app crash ngay khi start
- Không cần BACnet nếu chỉ xem config UI

### Giải pháp: Lazy Initialization

```python
# Khai báo nhưng KHÔNG khởi tạo
bacnet_service: BacnetService | None = None

# Chỉ khởi tạo khi thực sự cần
async def discover_devices():
    if not bacnet_service.connected:
        await bacnet_service.start()   # Khởi tạo lần đầu tiên
    return await bacnet_service.discover()
```

### Lợi ích

```
Eager Init (khởi tạo sớm):          Lazy Init (khởi tạo muộn):
App start → bind port 47808          App start → (không làm gì)
→ Tools không dùng được              → Tools vẫn dùng được
→ Config sai → crash ngay            → Config sai → báo lỗi khi dùng
→ Tốn resource dù không dùng         → Tốn resource chỉ khi dùng
```

> **Tư duy:** Lazy initialization là một dạng "trả nợ sau" — defer initialization đến khi thực sự cần. Phù hợp với resources đắt tiền (network connections, file handles, GPU). Không phù hợp với resources cần sẵn sàng ngay (database connection pool).

---

## 9. Index-Based Fallback

### Vấn đề
BACnet standard cho phép đọc toàn bộ `objectList` trong một request. Nhưng nhiều thiết bị thực tế (đặc biệt MSTP, firmware cũ) không hỗ trợ hoặc timeout khi trả về list lớn.

### Chiến lược "Read-Retry-Fallback"

```python
async def read_object_list(address, device_id):
    # Attempt 1: Đọc full list (nhanh nhất)
    try:
        obj_list = await network.read(f"{address} device {device_id} objectList")
        if obj_list:
            return parse(obj_list)
    except Exception:
        pass  # Không panic, thử cách khác

    # Fallback: Đọc từng phần tử theo index (chậm nhưng universal)
    obj_list = []
    for idx in range(1, 200):
        try:
            item = await network.read(f"{address} device {device_id} objectList {idx}")
            if item is None:
                break  # Hết list
            obj_list.append(item)
        except Exception:
            break
    return parse(obj_list)
```

### Nguyên lý thiết kế Defensive Programming

```
Không phòng thủ:                    Phòng thủ:
Gọi API → Thành công → OK          Gọi API → Thành công → OK
Gọi API → Fail → CRASH ❌          Gọi API → Fail → Log + Fallback ✅
                                    Fallback → Fail → Log + Return [] ✅
```

> **Tư duy:** "Happy path" (khi mọi thứ hoạt động đúng) chỉ chiếm 20% code. 80% code tốt là xử lý các edge cases và failure modes. Thiết bị công nghiệp đặc biệt hay có firmware không chuẩn — luôn có fallback.

---

## 10. RAM Guardian

### Vấn đề
Gateway chạy trên Raspberry Pi với RAM hạn chế. Khi map nhiều điểm với polling interval ngắn, bộ nhớ có thể cạn kiệt.

### Giải pháp: Hệ thống tự giám sát và tự điều chỉnh

```python
def _check_ram_health(self) -> bool:
    used_pct = get_ram_usage()

    if used_pct >= 95:    # PAUSE: Ngừng polling hoàn toàn
        gc.collect()
        return False

    if used_pct >= 90:    # THROTTLE: Nhân đôi interval
        self._ram_throttled = True
        return True

    if used_pct >= 80:    # WARN: Log cảnh báo
        logger.warning("RAM elevated")

    if used_pct < 90 and self._ram_throttled:
        self._ram_throttled = False  # RECOVER: Trở về bình thường

    return True
```

Đây là **multi-level circuit breaker pattern**:
- Level 1 (80%): Cảnh báo
- Level 2 (90%): Giảm tải
- Level 3 (95%): Ngừng hẳn
- Recovery: Tự phục hồi khi điều kiện trở lại bình thường

### Tối ưu bổ sung: Giảm tần suất check

Ban đầu RAM được check **mỗi 0.5 giây** (mỗi vòng lặp). Với `/proc/meminfo` đọc từ filesystem, dù nhanh vẫn là disk I/O không cần thiết.

```python
# Fix: Check mỗi 60 giây thay vì 0.5 giây
self._ram_check_counter += 1
if self._ram_check_counter >= 120:  # 120 × 0.5s = 60s
    self._ram_check_counter = 0
    self._check_ram_health()
```

> **Tư duy micro-optimization:** Đừng tối ưu quá sớm (premature optimization). Nhưng khi bạn thấy một hàm được gọi 2 lần/giây mà chỉ cần 1 lần/phút — đó không phải optimization, đó là **correctness**.

---

## Tổng Kết: Checklist Tư Duy Kỹ Sư

Khi nhìn vào bất kỳ đoạn code nào, hãy tự hỏi:

| Câu hỏi | Kiểm tra |
|---|---|
| Code này có thể chạy đồng thời không? | → Cần lock? |
| Có blocking call trong async context không? | → Cần executor? |
| Shared state có được init đúng chỗ không? | → Class vs Instance var? |
| Nếu component này fail, cái gì xảy ra? | → Cần fallback? |
| Resource này được release đúng chỗ không? | → Cần close/stop? |
| Code có assume "happy path" không? | → Cần defensive code? |
| Hàm này có gọi hàm khác cùng lock không? | → Cần RLock? |

---

*Tài liệu này là living document — sẽ được cập nhật khi có thêm kỹ thuật và bài học mới.*
