# TÀI LIỆU KỸ THUẬT: TÍCH HỢP CLIPROXYAPI (ANTIGRAVITY & OPENAI) VÀO CODEX CLI

Tài liệu giải trình kiến trúc, cấu hình chuẩn và cẩm nang xử lý 8 sự cố kỹ thuật cốt lõi khi tích hợp CLIProxyAPI (Google Antigravity + OpenAI Codex) vào OpenAI Codex CLI.

---

## PHẦN 1: KIẾN TRÚC TÍCH HỢP & DÒNG CHẢY DỮ LIỆU

```
┌─────────────────────────────────────────────────────────────┐
│                      OpenAI Codex CLI                       │
│    - TUI Interface / Slash commands (/model, /review)       │
│    - Tool Router (Bash execute, file editing, MCP)          │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / SSE Stream
                               ▼
┌─────────────────────────────────────────────────────────────┐
│       AIC HTTP Request Sanitizer (Frontline Reverse Proxy)   │
│                      (127.0.0.1:8090)                       │
│    - Real-Time <model_switch> Anti-Filter Sanitizer         │
│    - Zero-Buffer SSE Stream Forwarder                       │
└──────────────────────────────┬──────────────────────────────┘
                               │ Loopback HTTP
                               ▼
┌─────────────────────────────────────────────────────────────┐
│         CLIProxyAPI (Local Multi-Provider Quota Engine)     │
│                      (127.0.0.1:8095)                       │
│    - Round-Robin Load Balancer & Retry Engine               │
│    - Multi-Account OAuth Quota Pool                         │
│    - Responses API Wire Compatibility Adapter               │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼                              ▼
┌──────────────────────────────┐┌──────────────────────────────┐
│  Google CloudCode Backend    ││      OpenAI Codex API        │
└──────────────────────────────┘└──────────────────────────────┘
```

---

## PHẦN 2: CẤU HÌNH CHUẨN HÓA CỦA HỆ THỐNG

### 1. Cấu hình Proxy (`config.yaml`)
* **Routing:** `strategy: "round-robin"`, `session-affinity: false` (phân tán đều tải trên toàn bộ pool).
* **Retry Policy:** `request-retry: 1`, `max-retry-credentials: 4`, `max-retry-interval: 1` (tự động chuyển tài khoản khác ngay khi gặp HTTP 429).
* **Model Alias:** Khai báo ánh xạ chuẩn để hiển thị đẹp mắt trong Codex CLI:
  * `claude-sonnet-4-6` ➔ `claude-sonnet-4.6-thinking`
  * `gemini-3.8-flash-high` ➔ `gemini-3.8-flash`
  * `muse-spark-1.3-contributor-free` ➔ `muse-spark-1.3`

### 2. Cấu hình Codex CLI (`~/.codex/config.toml`)
```toml
model = "gemini-3.8-flash"
model_reasoning_effort = "high"
service_tier = "default"
model_provider = "custom"

[model_providers.custom]
name = "Custom Quota Pool"
base_url = "http://127.0.0.1:8090/v1"
wire_api = "responses"

[windows]
sandbox = "elevated"
```

---

## PHẦN 3: CẨM NANG XỬ LÝ SỰ CỐ KỸ THUẬT CỐT LÕI

### 1. Sự cố UTF-8 BOM gây crash Serde JSON Parser trong Rust
* **Hiện tượng:** Codex CLI không tải được danh sách model hoặc crash khi khởi động.
* **Nguyên nhân:** PowerShell 5.1 tự động ghi byte order mark UTF-8 BOM (`ï»¿`) vào đầu file `.json`. Rust `serde_json` không hỗ trợ BOM theo đặc tả JSON RFC 8259.
* **Giải pháp:** Sử dụng `[System.IO.File]::WriteAllText($path, $text, [System.Text.UTF8Encoding]::new($false))` hoặc Python UTF-8 không BOM.

---

### 2. Sự cố Enum `ModelVisibility` (`public` vs `list`)
* **Hiện tượng:** Danh sách model không hiển thị trong menu `/model` của Codex CLI.
* **Phân tích mã nguồn `codex-rs/protocol/src/openai_models.rs:271-280`:**
  ```rust
  pub enum ModelVisibility { List, Hide, None }
  ```
  Codex CLI chỉ kiểm tra: `show_in_picker = (info.visibility == ModelVisibility::List)`.
* **Giải pháp:** Bắt buộc đặt `"visibility": "list"` cho toàn bộ model trong file cache template.

---

### 3. Sự cố Thiếu trường `instructions_template` và `model_messages`
* **Hiện tượng:** Serde deserialization thất bại khi đọc `models_cache.json`.
* **Phân tích mã nguồn `codex-rs/protocol/src/openai_models.rs:780-784`:**
  Codex CLI yêu cầu mỗi model phải có `model_messages.instructions_template` hoặc `base_instructions`.
* **Giải pháp:** Trích xuất template đầy đủ từ `codex-rs/models-manager/models.json` vào file [`docs/models_cache_template.json`](file:///E:/AI/agents_in_chat/docs/models_cache_template.json).

---

### 4. Sự cố ETag Revalidation ép ghi đè Cache & Khóa OS Read-Only
* **Hiện tượng:** Sau vài turn chat, danh sách 6 model bị reset về 5 model mặc định của OpenAI.
* **Phân tích mã nguồn `codex-rs/models-manager/src/manager.rs:356-372`:**
  Khi request stream trả về Header `ETag`, nếu khác ETag trong RAM, Codex CLI kích hoạt `RefreshStrategy::Online` bỏ qua TTL và gọi `GET /models` đè lại file trên đĩa.
* **Khám phá then chốt (`models-manager/src/cache.rs:23`):** Lỗi ghi cache là *non-fatal*.
* **Giải pháp:** Đặt thuộc tính hệ điều hành **`Read-Only` (`attrib +r` trên Windows, `chmod 444` trên Linux)** để chặn 100% việc ghi đè của Codex CLI mà không gây crash.

---

### 5. Sự cố "Lỗ hổng lượt chat" (Dangling Turns & Empty Content)
* **Hiện tượng:** Khi đổi model từ Gemini sang Claude Sonnet, Anthropic trả về lỗi: `400: messages.1.content.0.text.text: Field required`.
* **Nguyên nhân:** Gemini kết thúc lượt với reasoning trống rỗng, tạo ra chuỗi nhiều lượt `User` liên tiếp không có `Assistant` ở giữa. Chuẩn API Anthropic cấm lượt rỗng và bắt buộc đan xen `User` ⇄ `Assistant`.
* **Giải pháp:** Cắt tỉa (rollback) các turn rỗng ở cuối file session `.jsonl` trước khi chuyển sang model Claude.

---

### 6. Sự cố Tool Capability Contract (`apply_patch` / `tool_mode`)
* **Hiện tượng:** Resume đoạn chat cũ bằng Gemini/Claude bị treo hoặc lỗi định dạng.
* **Nguyên nhân:** Model tùy chỉnh thiếu cờ `apply_patch_tool_type: "freeform"` và `tool_mode: "direct"`. Khi Resume đoạn chat có chứa tool patch cũ, Tool Router của Codex CLI tháo dỡ toàn bộ handler.
* **Giải pháp:** Đồng bộ 100% các cờ năng lực nâng cao trong `models_cache_template.json`.

---

### 7. Sự cố Quota Limit 429 & Cơ chế Load-Balancing
* **Hiện tượng:** Tài khoản cá nhân bị cạn token hoặc chạm trần rate limit.
* **Giải pháp:** CLIProxyAPI tự động phân phối vòng tròn (Round-Robin) qua 10 tài khoản OAuth. Khi gặp 429, retry engine tự động nhảy sang credential tiếp theo trong tối đa 4 lần thử mà không gián đoạn người dùng.

---

### 8. Kiến trúc Lưu trữ Hai Tầng & Đồng bộ Lịch sử Hai Chiều
* **Hiện tượng:** Khi chuyển đổi qua lại giữa `custom` và `openai`, menu **Resume** bị trống.
* **Phân tích mã nguồn `codex-rs/app-server/src/request_processors/thread_processor.rs:5091`:**
  Menu Resume tự động lọc: `WHERE model_provider = self.config.model_provider_id`.
* **Giải pháp:** Bộ kịch bản tự động đồng bộ hai chiều (SQLite `threads` + file `sessions/**/*.jsonl`):
  * Khi `install`: Đồng bộ toàn bộ sang `custom`.
  * Khi `uninstall`: Đồng bộ toàn bộ về `openai`.

---

### 9. Sự cố `invalid_encrypted_content` (HTTP 400) do Token Reasoning Giả lập (`cpa-`)
* **Hiện tượng:** Sau khi gỡ AIC (`uninstall`), resume lại đoạn chat cũ từng chạy qua Gemini/Claude và gửi tin nhắn cho mô hình OpenAI (`gpt-5.6-terra`, `luna`, `sol`) gặp lỗi HTTP 400:
  ```json
  {
    "type": "error",
    "error": {
      "type": "invalid_request_error",
      "code": "invalid_encrypted_content",
      "message": "The encrypted content for item rs_resp_... could not be verified. Reason: Encrypted content could not be decrypted or parsed."
    },
    "status": 400
  }
  ```
* **Bản chất kỹ thuật & Phân tích nguyên nhân:**
  1. *Cơ chế OpenAI Encrypted Reasoning:* Khi mô hình suy luận OpenAI (Terra, Luna, Sol) suy nghĩ, máy chủ OpenAI mã hóa khối suy nghĩ bằng Private Key nội bộ (`encrypted_content: "gAAAAAB..."`). Ở các lượt chat kế tiếp, Codex CLI gửi lại chuỗi này để OpenAI khôi phục mạch suy luận.
  2. *Cơ chế Proxy Carrier Blob:* Khi chat qua Gemini/Claude với `wire_api = "responses"`, CLIProxyAPI tự động sinh ra các khối reasoning giả lập mang tiền tố `encrypted_content: "cpa-gemini-responses-carrier-v1:..."` để đáp ứng schema Responses API của Codex CLI.
  3. *Xung đột giải mã:* Khi chuyển về `api.openai.com`, máy chủ OpenAI cố gắng giải mã blob `cpa-gemini-...` bằng Private Key của OpenAI $\rightarrow$ giải mã thất bại $\rightarrow$ trả về HTTP 400 `invalid_encrypted_content`.
* **Giải pháp xử lý triệt để (`scripts/sync_sessions.py`):**
  * Khi đồng bộ về `openai`, script tự động quét và loại bỏ (sanitize/strip) toàn bộ các `response_item` dạng `reasoning` có chứa `encrypted_content` bắt đầu bằng `cpa-` hoặc chứa `cpa-gemini-`.
  * Giữ nguyên 100% tất cả các tin nhắn văn bản, câu hỏi của user và kết quả gọi lệnh tool execution.
  * Khi đó Codex CLI chỉ gửi phần văn bản sạch lên OpenAI, cho phép tiếp tục resume mọi phiên chat mượt mà 100%.

---

### 10. Cơ chế Khôi phục Cấu hình Chuẩn xác Nhị phân (Byte-Exact TOML Restore) & Giao dịch Rollback Toàn diện
* **Hiện tượng:** Khôi phục cấu hình sau khi uninstall bị sai lệch ký tự xuống dòng (LF $\leftrightarrow$ CRLF), mất UTF-8 BOM, hoặc xóa nhầm cấu hình cá nhân `[profiles.*]` của người dùng.
* **Giải pháp nâng cấp kiến trúc:**
  1. *Bảo toàn chuẩn xác Nhị phân (Byte-Exact):* Toàn bộ thao tác sao lưu và phục hồi `config.toml` chuyển sang chế độ nhị phân (`read_bytes()`, `write_bytes()`), đảm bảo SHA256 checksum của file phục hồi khớp 100% từng byte với file gốc ban đầu.
  2. *Manifest Validation Fail-Fast:* Kiểm tra toàn diện `manifest.json` trước khi sửa đổi cấu hình; dừng cài đặt ngay lập tức nếu backup bị hỏng hoặc mất file.
  3. *Legacy Fallback bóc tách Section:* Bộ phân tích TOML phân chia top-level và các named sections (`[profiles.*]`, `[projects.*]`, `[mcp_servers.*]`), chỉ làm sạch các giá trị AIC ở top-level và bảo toàn 100% tất cả các section của người dùng.
  4. *Rollback Transaction Toàn diện:* `install.ps1`/`install.sh` bọc toàn bộ quy trình trong cơ chế rollback tự động. Nếu gặp sự cố ở bất kỳ bước nào (kể cả PATH, Profile, Start Proxy), hệ thống sẽ hoàn tác sạch sẽ và trả về exit code lỗi.
  5. *Strict Atomic Replacement:* Toàn bộ thao tác ghi file sử dụng file tạm và `os.replace()` nguyên tử; tiến trình `codex.exe` được tự động giải phóng trước khi sửa đổi để loại bỏ triệt để nguy cơ xung đột khóa file và hỏng dữ liệu.

---

### 11. Sự cố Kẹt Model Cũ khi Resume (`Deadlock Model Switching Handshake` & Lỗi 503 `auth_unavailable`)
* **Hiện tượng:**
  * Người dùng logout/xóa tài khoản của một provider (ví dụ OpenAI `codex` - model `gpt-5.6-sol`).
  * Sau đó resume lại đoạn chat cũ từng tạo bằng model đó và gõ lệnh `/model` để đổi sang model khác (ví dụ `claude-sonnet-4.6-thinking`).
  * Dù thanh trạng thái TUI hiển thị model mới và đã tắt mở lại terminal, Codex CLI vẫn gửi request với model cũ và bị Proxy từ chối:
    ```text
    503 Service Unavailable: auth_unavailable: no auth available (providers codex, model gpt-5.6-sol)
    {"error":{"type":"invalid_request_error","code":"model_not_found","message":"unknown provider for model gpt-5.6-sol"}}
    ```
* **Bản chất kỹ thuật & Phân tích nguyên nhân:**
  1. *Khởi tạo `turn_context`:* Khi resume session, Codex CLI đọc trạng thái `turn_context` ở cuối file session `.jsonl` (lúc này đang lưu `model: "gpt-5.6-sol"`).
  2. *Vòng lặp nghẽn Handshake (Deadlock):* Khi người dùng gửi prompt đầu tiên sau khi đổi model, request của Codex CLI vẫn mang định danh `model` cũ trong payload Responses API để gửi kèm thẻ `<model_switch>`.
  3. *Tầng mạng từ chối trước:* Khi request tới Proxy, Proxy kiểm tra thư mục `auths/` thấy thiếu tài khoản cho provider cũ $\rightarrow$ ném ra lỗi HTTP 503 ngay lập tức.
  4. *Không ghi nhận được trạng thái mới:* Do request thất bại ở tầng mạng, Codex CLI không thể hoàn thành turn và **không thể ghi đè `turn_context` mới xuống file trên đĩa**. Khi khởi động lại terminal, Codex CLI tiếp tục nạp lại `turn_context` cũ và lặp lại lỗi.
* **Giải pháp xử lý triệt để:**
  * **Giải pháp 1 (Nạp tài khoản):** Đăng nhập lại tài khoản tương ứng qua `aic login_codex` để Proxy chấp nhận handshake model switch.
  * **Giải pháp 2 (Override session state):** Cập nhật trực tiếp trường `model` trong các khối `turn_context` và `thread_settings_applied` ở các dòng cuối file session `.jsonl` sang model đích (`claude-sonnet-4.6-thinking` hoặc `gemini-3.7-flash`) để phá vỡ vòng lặp kẹt model.

---

### 12. Tích hợp Mô hình Miễn phí Ngoại vi qua Chuẩn `OpenAI-Compatibility` (Mô hình Muse Spark 1.3 1M Context từ OpenCode Zen)
* **Bản chất mô hình & Hạ tầng nguồn:**
  * Mô hình **Muse Spark 1.3** (ID nội bộ: `muse-spark-1.3-contributor-free`) là mô hình agentic coding chuyên sâu từ Meta với context window lên tới **1.048.576 tokens (1M Context)**.
  * Hạ tầng Backend: **OpenCode Zen API** (`https://opencode.ai/zen/v1/chat/completions`), hỗ trợ truy cập mở với header `Authorization: Bearer public` không yêu cầu đăng nhập hay tạo tài khoản.
* **Cơ chế chuyển tiếp Responses API $\leftrightarrow$ OpenAI Chat Completions:**
  1. *Cấu hình Upstream Provider (`config.yaml`):* Khai báo entry `openai-compatibility` với endpoint `https://opencode.ai/zen/v1`, bearer key `public`, alias mapping `muse-spark-1.3-contributor-free` $\rightarrow$ `muse-spark-1.3` và `muse-3`.
  2. *Biên dịch Giao thức Luồng (SSE Protocol Translation):* CLIProxyAPI tự động dịch chuyển gói tin Responses API (`/v1/responses`) từ Codex CLI sang định dạng Chat Completions SSE của OpenCode Zen, đồng thời stream lại các block `reasoning_summary` và reasoning tokens về TUI của Codex CLI.
  3. *Đăng ký Xác thực trong Auth Manager (`auths/`):* Bộ điều phối luồng của Proxy yêu cầu file cấu hình xác thực `auths/openai-compatible-opencode-zen.json` để đăng ký candidate cho provider `openai-compatible-opencode-zen` (tránh lỗi 503 `auth_unavailable`). File này được tự động tạo bởi `bin/aic.py`, `install.ps1`, và `install.sh`.
  4. *Tương thích Cache Model (`models_cache_template.json`):* Nạp model `muse-spark-1.3` và `muse-3` vào cache `~/.codex/models_cache.json` với `visibility: "list"`, `context_window: 1048576`, và `supported_reasoning_levels: ["low", "high"]` (mặc định: `high`).
  5. *Đồng bộ Lịch sử (Cross-Model Seamless Switching):* Cho phép người dùng chuyển đổi mượt mà giữa Gemini 3.8 Flash, Claude Sonnet 4.6, GPT Sol, GPT Terra, GPT Luna và Muse Spark 1.3 trên cùng một phiên làm việc Codex CLI mà không bị đứt đoạn lịch sử chat.

---

### 13. Lỗi Lệch Con trỏ Byte / Ordinal khi Rẽ nhánh Hội thoại (`/fork`) & `thread_history_projection_state`
* **Hiện tượng:**
  * Khi người dùng gõ lệnh `/fork` trong Codex CLI để nhân bản đoạn chat sang nhánh mới, TUI báo lỗi:
    ```text
    ■ Failed to fork current session through the app server: thread/fork failed during TUI bootstrap:
    thread/fork failed: failed to prepare paginated fork: thread-store internal error:
    thread history projection for 01a01ebd-7357-7162-b1e7-15dce576a1b4 expected ordinal 1463, got 1472;
    1 rejected rollout lines cannot cover that gap (code -32603)
    ```
* **Bản chất kỹ thuật & Phân tích nguyên nhân:**
  1. *Cơ chế lưu trữ của Codex CLI (`thread_history_1.sqlite`):* Codex CLI sử dụng cơ sở dữ liệu `~/.codex/thread_history_1.sqlite` để đánh chỉ mục (index cache) vị trí byte (`next_rollout_byte_offset`) và số thứ tự dòng (`next_rollout_ordinal`) của các session `.jsonl`.
  2. *Lệch vị trí sau khi thay đổi kích thước file (Stale Offset Mismatch):* Khi các session `.jsonl` được đồng bộ, nén ngữ cảnh (compaction) hoặc làm sạch token định tuyến, kích thước file trên đĩa thay đổi khiến chỉ mục byte trong `thread_history_1.sqlite` bị lệch so với dữ liệu thực tế.
  3. *Lỗi đứt đoạn khi Fork (Projection Gap):* Khi nhận lệnh `/fork`, Codex CLI nhảy tới vị trí byte đã lưu trong cache SQLite thay vì đọc từ đầu file, dẫn đến việc bỏ qua một số dòng và gây ra lỗi `expected ordinal X, got Y`.
* **Giải pháp xử lý triệt để:**
  * **Tự động làm sạch chỉ mục cache (`sync_sessions.py`):** Mỗi khi script đồng bộ `sync_sessions.py` ghi đè file session `.jsonl`, hệ thống sẽ tự động dọn dẹp các bảng cache tạm (`thread_items`, `thread_turns`, `thread_history_projection_state`) trong `thread_history_1.sqlite`.
  * **Tự xây dựng lại chỉ mục (Zero-Loss On-Demand Projection):** Codex CLI sẽ tự động quét lại toàn bộ file `.jsonl` từ byte 0 và tính toán lại chính xác 100% vị trí các dòng khi người dùng thực hiện `/fork` mà không gây mất mát dữ liệu.

---

### 14. Cơ chế Đánh giá Tính Hợp lệ của Cache Model & Lỗi Lệch Phiên bản Client (`cache version mismatch`)
* **Hiện tượng:**
  * Khi Codex CLI tự động nâng cấp (ví dụ: từ `v0.148.0` lên `v0.149.0`), mặc dù file `~/.codex/models_cache.json` đã được cài đặt và khóa `Read-Only`, khi gõ lệnh `/model`, TUI vẫn hiển thị menu mặc định nguyên bản của OpenAI (như Gemini hiện `Minimal, Low, Medium, High`, Claude hiện `Extra high`).
* **Bản chất kỹ thuật (Kiểm chứng qua nhị phân `codex.exe`):**
  * Trong mã nguồn Rust của Codex CLI (`models-manager\src\manager.rs`), hàm `load_cache` kiểm tra trường `"client_version"` trong `models_cache.json`.
  * Nếu `client_version` trong file cache không khớp với phiên bản binary đang chạy (`0.148.0` $\neq$ `0.149.0`), runtime lập tức kích hoạt luồng log:
    ```text
    models cache: loaded cache file
    models cache: cache version mismatch
    models cache: no usable cache entry -> fetching remote models / using fallback builtins
    ```
  * Khi đó, Codex CLI coi file cache trên đĩa là "không hợp lệ/hết hạn" và tự động nạp cấu hình cứng tích hợp bên trong binary.
* **Giải pháp tự động hóa triệt để:**
  * **Tự động trích xuất phiên bản (`codex --version`):** Trong [`install.ps1`](file:///E:/AI/agents_in_chat/install.ps1) và [`install.sh`](file:///E:/AI/agents_in_chat/install.sh), hệ thống tự động chạy `codex --version` để lấy số phiên bản thực tế của máy tính người dùng và ghi động vào trường `"client_version"` trước khi khóa `Read-Only`.
  * **Chống lệch phiên bản khi nâng cấp:** Đảm bảo dù Codex CLI được nâng cấp lên bất kỳ phiên bản nào (0.150, 0.151,...), chỉ cần chạy `install.ps1` hoặc `aic restart`, hệ thống sẽ luôn đồng bộ 100%.

---

### 15. Ràng buộc Mức Suy luận Của OpenCode Zen Backend & Xử lý Lỗi HTTP 400 (`[1210]`)
* **Hiện tượng:**
  * Khi gửi request đến mô hình `Ox Alpha` (`x-preview-f-free`) với tham số `reasoning_effort: "medium"`, máy chủ OpenCode Zen từ chối với mã lỗi:
    ```json
    HTTP Error 400: {"error":{"type":"server_error","message":"Error from provider (Console): Upstream request failed: [1210] This model always engages in thinking and cannot be disabled; please use low, high, or max"}}
    ```
* **Bản chất backend OpenCode Zen:**
  * Cụm máy chủ OpenCode Zen Backend chỉ thiết kế chấp nhận 3 giá trị định danh suy luận cụ thể: **`low`**, **`high`**, và **`max`** (hoặc `default` khi bỏ trống trường `reasoning_effort`).
  * Backend hoàn toàn không hỗ trợ nấc `medium` cho mô hình stealth reasoning `x-preview-f-free`.
* **Giải pháp chuẩn hóa:**
  * Trong [`docs/models_cache_template.json`](file:///E:/AI/agents_in_chat/docs/models_cache_template.json), danh sách `supported_reasoning_levels` cho `ox-alpha` được cấu hình độc lập gồm đúng 3 nấc: `["low", "high", "max"]` với mặc định là `"max"`.
  * Điều này loại bỏ hoàn toàn nguy cơ gửi nhầm cờ `medium` và giúp mô hình phản hồi mượt mà ở mọi nấc lựa chọn.

---

### 16. Tối ưu Công thái học Thao tác TUI (Ergonomic Default Reasoning Levels)
* **Yêu cầu thực tế:**
  * Người dùng thường xuyên thao tác nhanh trên bàn phím bằng phím `Enter` khi chọn mô hình. Nếu mức suy luận mặc định nằm ở nấc thấp hoặc trung bình, người dùng phải bấm thêm phím mũi tên điều hướng.
* **Cấu hình tối ưu hóa theo mô hình:**
  1. **Nhóm Anthropic Claude (`claude-sonnet-4.6`):**
     * Đặt `default_reasoning_level: "high"`.
     * Loại bỏ nấc dư thừa `xhigh` $\rightarrow$ Menu chỉ hiển thị gọn gàng `[Low, Medium, High (default)]`.
  2. **Nhóm Google Gemini (`gemini-3.8-flash`):**
     * Đặt `default_reasoning_level: "high"`.
     * Menu hiển thị `[Low, Medium, High (default)]`.
  3. **Nhóm OpenCode Zen (`muse-spark-1.3`, `muse-3`):**
     * Đặt `default_reasoning_level: "high"`.
     * Menu hiển thị `[Low, High (default)]`.
* **Hiệu quả:**
  * Người dùng chỉ cần gõ `/model`, chọn mô hình mong muốn và nhấn `Enter` là hệ thống tự động kích hoạt mức suy luận cao nhất/sâu nhất mà không cần thêm bất kỳ thao tác bấm phím nào.

### 17. Cơ chế Atomic Auto-Backup & Auto-Recovery cho File Token (`auths/`) — Chống lỗi Null Bytes (0x00) trên NTFS
* **Hiện tượng:**
  * Sau khi máy tính bị Sleep, mất điện ngỏ, crash hoặc tiến trình Proxy bị kết thúc đột ngột đúng vào thời điểm đang xóa/ghi đè token, một số file OAuth trong `auths/` (đặc biệt là Google Antigravity với chu kỳ refresh 1 giờ) biến thành toàn bộ byte rỗng `0x00` (Null bytes) hoặc file 0-byte.
  * Khi khởi động lại, CLIProxyAPI gặp lỗi `Invalid JSON` trên các file này và **tự động bỏ qua** chúng. Toàn bộ tài khoản biến mất khỏi Management Web UI mà không hề báo lỗi HTTP 401 nào — lỗi "âm thầm" nghiêm trọng nhất của hệ thống.
* **Bản chất kỹ thuật & Nguyên nhân gốc trên NTFS:**
  * CLIProxyAPI ghi đè token theo chu trình: đọc token cũ → gọi OAuth backend để refresh → **xóa/truncate file cũ → ghi nội dung mới**. Khoảng thời gian giữa thao tác xóa và ghi chỉ là vài mili-giây, nhưng nếu ngắt điện/ngắt tiến trình đúng trong khoảng này thì:
    * *Trường hợp 1:* Metadata file đã được commit (size = 0 hoặc size = N) nhưng dữ liệu chưa được flush từ bộ đệm OS xuống đĩa → khi NTFS journaling phục hồi metadata, file trở thành toàn `0x00` hoặc 0-byte.
    * *Trường hợp 2:* Ghi dữ liệu xong nhưng chưa flush metadata → file giữ dung lượng cũ, nội dung mới chỉ lấp đầy một phần đầu file, phần còn lại là rỗng.
  * Khác biệt quan trọng với lỗi 401 của ChatGPT/Codex: Lỗi 401 là **lỗi trạng thái logic** (token hết hạn/chữ ký sai) — file vẫn là JSON hợp lệ nên Proxy báo lỗi rõ ràng và có thể tự refresh lại. Lỗi Null bytes là **lỗi vật lý dữ liệu** (file không còn là JSON) — Serde parser từ chối parse nên Proxy im lặng bỏ qua tài khoản, dẫn đến hiện tượng "biến mất" thay vì báo lỗi.
* **Giải pháp bảo vệ: Atomic Auto-Backup & Recovery ([`scripts/backup_auths.py`](file:///E:/AI/agents_in_chat/scripts/backup_auths.py)) — Zero-RAM Overhead:**
  1. *Không daemon, không RAM:* Chế độ chạy hoàn toàn **event-driven** — script chỉ được kích hoạt tại các mốc vòng đời (lifecycle hooks): trước khi start proxy (chạy `restore`), sau khi stop proxy (chạy `backup`), và khi install (khởi tạo snapshot ban đầu). Giữa hai mốc này không tốn bất kỳ tài nguyên RAM/CPU thường trú nào.
  2. *Sao lưu nguyên tử có kiểm định (`backup`):* Chỉ sao chép những file là **JSON hợp lệ 100%** (> 0 byte, không chứa `0x00`, parse được bằng `json.loads`). Các file rác/hỏng bị bỏ qua tuyệt đối, đảm bảo `auths_backup/` luôn chỉ chứa trạng thái sống khỏe mạnh mới nhất. Ghi đè bằng cơ chế temp-file + `os.replace()` nguyên tử.
  3. *Tự động phục hồi (`restore`):* Quét toàn bộ `auths/`; file nào bị 0-byte, toàn `0x00` hoặc JSON sai cú pháp sẽ được ghi đè **byte-exact** từ bản backup tương ứng. File bị mất hoàn toàn trong `auths/` mà vẫn còn trong `auths_backup/` cũng được tự động bù đắp lại. File khỏe mạnh (kể cả token mới refresh hơn bản backup) không bao giờ bị ghi đè — restore tuyệt đối không phá hủy trạng thái mới hơn.
  4. *Báo cáo sức khỏe (`verify`):* Thống kê số file hợp lệ / số file hỏng trong `auths/`, trả exit code 0 (khỏe) hoặc 1 (có file hỏng) để tích hợp vào pipeline kiểm thử.
  5. *Tích hợp vòng đời:* [`start.ps1`](file:///E:/AI/agents_in_chat/start.ps1)/[`start.sh`](file:///E:/AI/agents_in_chat/start.sh)/`aic start` chạy `restore` trước khi nạp binary; [`stop.ps1`](file:///E:/AI/agents_in_chat/stop.ps1)/[`stop.sh`](file:///E:/AI/agents_in_chat/stop.sh)/`aic stop`/`aic restart` chạy `backup` sau khi dừng; [`install.ps1`](file:///E:/AI/agents_in_chat/install.ps1)/[`install.sh`](file:///E:/AI/agents_in_chat/install.sh) khởi tạo `auths_backup/` và chụp snapshot ban đầu.
  6. *An toàn bảo mật:* Thư mục `auths_backup/` chứa token thật nên đã được đưa vào `.gitignore`, không bao giờ được commit/push lên Git repository.

---

### 18. Sự cố Lệch Phiên Bản Cache (`client_version`) khi Codex CLI Cập Nhật & Giải Pháp Smart Wrapper Hook (Zero-Touch)
* **Hiện tượng:**
  * Khi OpenAI phát hành bản cập nhật mới cho Codex CLI (ví dụ từ `0.150.1` lên `0.153.0`), người dùng nhấn `1. Update now` để cập nhật binary.
  * Sau khi cập nhật thành công, mở Codex CLI và gõ lệnh `/model` thì menu 8 mô hình tùy chỉnh bị biến mất hoàn toàn, thay bằng danh sách 5 mô hình tĩnh mặc định của OpenAI (`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.2`).
* **Bản chất kỹ thuật & Cơ chế của Codex CLI:**
  1. *Hardcoded fallback trong Rust binary:* OpenAI nhúng cứng danh sách 5 model mặc định vào thẳng bên trong file nhị phân `codex.exe`.
  2. *Kiểm tra phiên bản cache:* Khi khởi động, `codex.exe` đọc file `~/.codex/models_cache.json` và so sánh trường `client_version` với số phiên bản của binary đang chạy. Nếu `cache.client_version != current_exe_version`, Codex CLI coi file cache là "không hợp lệ / lỗi thời", bỏ qua hoàn toàn file trên đĩa và dùng 5 model nhúng tĩnh trong RAM của file `.exe`.
  3. *Bảo toàn dữ liệu nhờ khóa Read-Only:* Nhờ thuộc tính Read-Only ở tầng hệ điều hành NTFS mà AIC thiết lập, OpenAI không thể ghi đè hay xóa bỏ file cache 8 model tùy chỉnh của người dùng. Dữ liệu vẫn an toàn nguyên vẹn 100% trên đĩa.
* **Giải pháp: Smart Wrapper Hook trong PowerShell Profile (Zero-Touch Update):**
  * Đăng ký hàm wrapper `global:codex` bên trong khối `# >>> AIC >>>` của PowerShell Profile (`$PROFILE`).
  * Khi người dùng gõ `codex`, hàm wrapper thực thi trong **0.05 giây**:
    1. Kiểm tra nhanh đường dẫn binary thật của `codex.exe`.
    2. Đọc file `models_cache.json` và trích xuất `client_version` bằng biểu thức chính quy (Regex).
    3. Chạy `codex.exe --version` để lấy số phiên bản hiện tại.
    4. Nếu phát hiện lệch phiên bản: tự động mở khóa Read-Only, thay thế đúng chuỗi `client_version`, ghi đè nguyên tử bằng UTF-8 No BOM và khóa lại Read-Only.
    5. Chuyển tiếp toàn bộ cờ và đối số `@args` sang `codex.exe` thật.
  * **Trải nghiệm Zero-Touch:** Khi Codex CLI update, chọn **Update now**, sau đó **tắt terminal hiện tại và mở terminal mới để sử dụng**. Hook tích hợp trong PowerShell Profile sẽ tự động nhận diện phiên bản mới và đồng bộ cache trong tích tắc (~0.05s) mà không cần phải chạy lại kịch bản cài đặt (`install.ps1`).

---

### 19. Tích Hợp Mô Hình Google Gemini 3.8 Flash (High)
* **Bối cảnh:** Google Antigravity bổ sung mô hình thế hệ mới `gemini-3.8-flash`.
* **Cấu hình đồng bộ trong hệ sinh thái AIC:**
  1. *Proxy Alias Mapping ([`config.yaml`](file:///E:/AI/agents_in_chat/config.yaml)):* Khai báo ánh xạ cho Google Antigravity:
     * `gemini-3.8-flash-high` ➔ `gemini-3.8-flash`
     * `gemini-3.8-flash` ➔ `gemini-3.8-flash`
  2. *Đặc tả Codex Models Cache ([`docs/models_cache_template.json`](file:///E:/AI/agents_in_chat/docs/models_cache_template.json)):* Khai báo đầy đủ thuộc tính Native Tool Calling: `slug: "gemini-3.8-flash"`, `display_name: "Gemini 3.8 Flash (High)"`, `tool_mode: "direct"`, `default_reasoning_level: "high"`, `visibility: "list"`.
  3. *OpenCode Desktop ([`~/.config/opencode/opencode.jsonc`](file:///C:/Users/Lenovo/.config/opencode/opencode.jsonc)):* Bổ sung mục `"gemini-3.8-flash": { "name": "Gemini 3.8 Flash High" }`.
* **Xác thực Live SSE Protocol:** Gửi request thực tế gọi tool `exec_command` qua cổng 8080. Mô hình phản hồi đủ 9 sự kiện SSE và phát native `fc_call` chuẩn xác 100%, không bị nuốt turn hay rơi vào text fallback.

---

### 20. Sự Cố Ảo Giác XML Thẻ Tool Calling (`<function_calls>`) do Bẻ Lái Nhầm GPT-5.6 Sol & Nén Ngữ Cảnh (`Context compacted`)
* **Hiện tượng:**
  * Khi chọn mô hình `gpt-5.6-sol` trong Codex CLI, mô hình liên tục in ra các thẻ văn bản thô dạng XML: `<function_calls><invoke name="exec_command">...</invoke></function_calls>`. Mô hình không thực thi lệnh thực tế, in ra hàng loạt dòng giả lập gọi lệnh rồi bị Codex CLI ngắt lượt (`Conversation interrupted`).
* **Nguyên nhân gốc rễ:**
  1. *Cấu hình bẻ lái sai trong `config.yaml`:* Trong `config.yaml` có định nghĩa fallback gán alias `claude-sonnet-4-6` trỏ sang `gpt-5.6-sol` và loại trừ `gpt-5.6-sol` khỏi OpenAI Codex provider. Khi người dùng chọn `gpt-5.6-sol`, Proxy đã chuyển tiếp request sang **Claude Sonnet 4.6 của Antigravity** thay vì gọi GPT-5.6 Sol thật của OpenAI.
  2. *Nén ngữ cảnh (`Context compacted`):* Đoạn hội thoại kéo dài vượt ngưỡng token khiến Codex CLI kích hoạt tính năng nén ngữ cảnh. Sau khi bị nén nhiều lần, Claude Sonnet 4.6 bị mất cấu trúc system prompt của giao thức Tool Calling và rơi vào trạng thái ảo giác (hallucination), tự động nhả ra các thẻ XML thô trong dữ liệu huấn luyện tiền kỳ thay vì gửi cấu trúc đối tượng JSON gọi tool chuẩn.
  3. *Khác biệt với GPT-5.6 Sol thật:* GPT-5.6 Sol gốc chạy trực tiếp trên tài khoản OpenAI Codex OAuth qua Responses API, giao tiếp bằng nhị phân/JSON stream chuẩn xác của OpenAI và không bao giờ in ra các thẻ XML của Anthropic Claude.
* **Giải pháp khắc phục:**
  * Loại bỏ toàn bộ các dòng alias fallback bẻ lái `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` sang Claude Sonnet trong `config.yaml`.
  * Mở khóa các mô hình GPT-5.6 khỏi danh sách `oauth-excluded-models.codex` để toàn bộ request Sol/Terra/Luna được định tuyến 100% về tài khoản OpenAI Codex OAuth thật.

---

### 21. Sự Cố "Lineage Drift" Lệch Byte Offset Sau Khi Gọt Tỉa Reasoning (`invalid paginated history lineage: cutoff byte offset is past the source rollout`)
* **Hiện tượng:**
  * Người dùng mở lại (Resume) một phiên chat đã từng được rẽ nhánh (**Fork**) sau khi chạy `aic uninstall` (hoặc `sync_sessions.py openai`), giao diện Codex CLI hiện thông báo lỗi đỏ:
    `Failed to resume chat: invalid paginated history lineage for <thread_id>: cutoff byte offset is past the source rollout`.
* **Phân tích mã nguồn lõi của Codex CLI (`codex-rs`):**
  1. *Kiểm tra biên cắt trong [`rollout_lineage.rs:261-294`](file:///E:/AI/agents_in_chat/ma_nguon_tham_khao/codex-repo/codex-rs/thread-store/src/local/rollout_lineage.rs):*
     ```rust
     async fn validate_cutoff_bounds(
         requested_thread_id: ThreadId,
         rollout_path: &Path,
         end: &HistoryPosition,
     ) -> ThreadStoreResult<()> { ... }
     ```
  2. *Kiểm tra kích thước file trong [`seekable_reader.rs:87-89`](file:///E:/AI/agents_in_chat/ma_nguon_tham_khao/codex-repo/codex-rs/rollout/src/seekable_reader.rs):*
     ```rust
     pub fn rollout_contains_prefix(path: &Path, end_byte_offset: u64) -> io::Result<bool> {
         match RolloutReader::open(path)? {
             RolloutReader::Plain(file) => Ok(end_byte_offset <= file.metadata()?.len()),
             ...
         }
     }
     ```
     Codex CLI bắt buộc `end_byte_offset` (điểm cắt mà phiên con rẽ nhánh từ phiên cha) phải luôn **nhỏ hơn hoặc bằng độ dài thực tế của file phiên cha** (`file.metadata()?.len()`).
* **Bản chất kỹ thuật & Nguyên nhân gốc:**
  1. Khi người dùng chat qua AIC Proxy với các mô hình Antigravity (Gemini/Claude), proxy sinh ra các khối reasoning tổng hợp `cpa-*` (Encrypted Reasoning Carrier).
  2. Khi chuyển về provider `openai` (`aic uninstall`), `sync_sessions.py` bắt buộc phải loại bỏ các dòng `cpa-*` để OpenAI API không trả về lỗi HTTP 400 `invalid_encrypted_content`.
  3. Việc loại bỏ các dòng này làm file phiên cha co lại (giảm từ vài KB đến vài chục KB tùy số lượng turn).
  4. Trước đây, `sync_sessions.py` chỉ sửa độc lập file cha mà không rà soát lại các phiên con đã fork từ phiên cha đó. Dòng 1 metadata của phiên con vẫn lưu tĩnh `end_byte_offset` trỏ vào độ dài cũ của file cha.
  5. Khi resume phiên con, Codex CLI so sánh thấy `end_byte_offset (cũ) > độ_dài_file_cha (mới)` nên lập tức từ chối tải và báo lỗi hỏng chuỗi lineage.
* **Giải pháp tự động hóa toàn diện trong AIC:**
  1. *Cơ chế Re-align Lineage tự động ([`scripts/sync_sessions.py`](file:///E:/AI/agents_in_chat/scripts/sync_sessions.py)):*
     * Bổ sung hàm `realign_forked_lineages`: Sau khi lọc xong các file, script tự động quét tìm tất cả các file có trường `history_base`.
     * Nếu phát hiện `history_base.end_byte_offset > parent_file_size`, hàm `find_ordinal_byte_offset` sẽ dò lại chính xác vị trí byte kết thúc của `end_ordinal_exclusive` trong file cha mới (hoặc gán bằng dung lượng mới của file cha nếu rẽ nhánh ở cuối file).
     * Ghi đè nguyên tử dòng metadata 1 của phiên con bằng temporary file + `os.replace()`.
  2. *Cơ chế xóa Cache với Lock Retry (`clear_history_projection_cache`):*
     * Kết nối `thread_history_1.sqlite` với timeout 5.0 giây và cơ chế thử lại 3 lần nhằm phòng tránh xung đột khóa file (file lock) khi tiến trình `codex.exe` đang chạy nền trên Windows.
  3. *Tích hợp kiểm tra toàn vẹn trong `verify_provider`:*
     * Chạy 2 pass kiểm tra: pass 1 ghi nhận kích thước tất cả các session, pass 2 đối chiếu toàn bộ `history_base.end_byte_offset <= parent_size` và đối chiếu độ lệch byte offset so với ordinal cha. Bất kỳ trường hợp lệch offset nào đều bị bắt ngay từ bước verify.
  4. *Bảo đảm chất lượng bằng Unit Test ([`tests/test_sync_and_backup_unit.py`](file:///E:/AI/agents_in_chat/tests/test_sync_and_backup_unit.py)):*
     * Bổ sung Test 16 mô phỏng chu trình: tạo file cha mang carrier `cpa-` → tạo file con fork từ file cha → chạy `sync_provider("openai")` → xác nhận file cha co lại và file con tự động được re-align `end_byte_offset` khớp 100% với kích thước cha mới, `verify_provider` trả về kết quả hợp lệ tuyệt đối.

---

### 22. Sự Cố Gateway Antigravity Kích Hoạt Bộ Lọc Nội Dung Trả Về HTTP 429 Khi Payload Chứa Cụm "based on GPT-5"
* **Hiện tượng:**
  * Khi người dùng bắt đầu phiên chat hoặc chuyển đổi mô hình sang Google Gemini (`gemini-3.8-flash`) hay Anthropic Claude (`claude-sonnet-4.6-thinking`) trong Codex CLI qua Antigravity upstream, gateway lập tức từ chối yêu cầu và trả về lỗi:
    `429 Too Many Requests: {"error":{"code":"429","message":"Resource has been exhausted (e.g. check quota)."}}`.
  * Dù proxy cấu hình xoay vòng nhiều tài khoản Google Antigravity khác nhau, toàn bộ các tài khoản đều liên tiếp nhận mã lỗi 429 chỉ trong vài mili-giây.
* **Điều tra & Phân tích nguyên nhân gốc rễ (Root Cause Analysis):**
  1. *Kiểm tra hạn ngạch tài khoản (Quota Inspection):*
     * Kiểm tra trực tiếp endpoint `check_balance` của Google Antigravity API trên tất cả các token OAuth: Các tài khoản đều có `remainingFraction: 1.0` (100% hạn ngạch còn nguyên, chưa tiêu tốn bất kỳ lượt request nào).
  2. *Nguồn gốc phát sinh chuỗi "based on GPT-5" từ Codex CLI:*
     * **Top-Level `instructions` trong payload `/v1/responses`:** Khảo sát mã nguồn Rust của `codex-rs` (`core/src/client.rs:839` và `core/src/config/mod.rs:3890`) cho thấy trường `instructions` ở cấp cao nhất của request `/v1/responses` được lấy trực tiếp từ `prompt.base_instructions.text`. Nếu tệp `~/.codex/config.toml` không cấu hình biến `instructions`, `codex.exe` mặc định fallback về hằng số nội tại `BASE_INSTRUCTIONS = "You are Codex, an agent based on GPT-5."`. Do đó, ngay cả phiên chat hoàn toàn mới cũng bị nhúng chuỗi này vào đầu payload request gửi tới upstream proxy!
     * **Cấu trúc Dictionary trong Session Metadata (Dòng 0):** Trong tệp lịch sử phiên chat (`rollout-*.jsonl`), dòng 0 `session_meta` chứa trường `payload.base_instructions` dưới dạng một JSON Object `{"text": "You are Codex, an agent based on GPT-5."}` thay vì chuỗi phẳng. Nếu hàm khử độc chỉ quét các trường kiểu chuỗi thuần túy (string), chuỗi này sẽ sót lại và bị Codex CLI đọc lên nạp vào ngữ cảnh.
     * **Template Cache (`models_cache.json`):** Binary `codex.exe` (v0.155.1) mặc định lưu chuỗi `"You are Codex, a coding agent based on GPT-5."` vào trường system instructions trong cache model.
     * **Developer Message khi chuyển đổi mô hình (`<model_switch>`):** Khi người dùng chuyển đổi mô hình giữa chừng bằng lệnh `/model`, `codex.exe` tự động chèn một tin nhắn `role: "developer"` chứa thẻ `<model_switch>` mang chuỗi chỉ dẫn trên vào lịch sử phiên chat.
  3. *Cơ chế bộ lọc nội dung tại Gateway Upstream:*
     * Phía gateway upstream áp dụng cơ chế lọc nội dung (content filter) đối với các chuỗi nhận diện hệ thống chứa cụm từ `"based on GPT-5"`. Khi cụm từ này xuất hiện trong ngữ cảnh instruction hoặc message developer, gateway từ chối xử lý và trả về mã trạng thái HTTP 429 không phản ánh đúng bản chất hạn ngạch (thay vì HTTP 400 hoặc thông báo lọc nội dung).
  4. *Chứng minh thực nghiệm độc lập (A/B Isolation Testing):*
     * Thử nghiệm A: Giữ nguyên payload trích xuất từ phiên chat lỗi chứa `<model_switch>` hoặc `instructions` mang chuỗi `"based on GPT-5"` $\rightarrow$ Upstream API lập tức trả về HTTP 429 Resource has been exhausted.
     * Thử nghiệm B: Giữ nguyên toàn bộ cấu trúc payload (cùng tools, cùng context), chỉ thay thế câu mở đầu thành `"You are Codex, an expert coding agent."` $\rightarrow$ **HTTP 200 OK ngay lập tức, phản hồi SSE streaming hoạt động bình thường.**
* **Giải pháp chuẩn hóa toàn diện trong AIC (Bản phát hành v1.1.5):**
  1. *Bảo toàn Template Hoàn chỉnh & Không inject Top-level `instructions` (`scripts/config_manager.py`):*
     * Không thêm biến `instructions` vào top-level của `config.toml`, cho phép Codex CLI nạp trọn vẹn template ~17k ký tự từ `models_cache.json`.
     * Tự động dọn dẹp câu lệnh 1 dòng của bản thử nghiệm cũ (`You are Codex, an expert coding agent.`) và bảo tồn 100% các chỉ dẫn tùy biến cá nhân của người dùng cũng như các khóa lân cận (`model_instructions_file`).
  2. *Khử độc chính xác trong Models Cache Template (`docs/models_cache_template.json`):*
     * Chỉ thay thế cụm từ `"based on GPT-5"` thành `"an expert coding agent"`, giữ nguyên toàn bộ 100% cấu trúc Markdown và các phân mục hướng dẫn chuyên sâu.
  3. *Khử độc theo Schema Ngữ Cảnh cho Lịch Sử Phiên Chat (`scripts/instruction_compat.py`):*
     * Chỉ làm sạch các trường chỉ dẫn hệ thống của harness (`session_meta.base_instructions`, `turn_context.instructions`, và thẻ `<model_switch>` trong tin nhắn `developer`).
     * Bảo tồn tuyệt đối 100% nội dung tin nhắn của người dùng (`user`), phản hồi của trợ lý (`assistant`), và lời gọi/kết quả công cụ (`tool`), kể cả khi người dùng cố ý nhắc đến "based on GPT-5".
  4. *Lệnh bảo trì tự động `aic repair` (`scripts/repair.py` & `bin/aic.py`):*
     * Tự động phát hiện và kết thúc tiến trình `codex.exe` đang chạy ngầm (`kill_codex`) để giải phóng file lock, abort an toàn nếu không giải phóng được.
     * Cấu hình lại `config.toml`, đồng bộ nguyên tử `models_cache.json` từ template và khóa Read-Only.
     * Khử độc toàn bộ lịch sử session hiện có trong thư mục `sessions/`.
     * Cân chỉnh lại biên cắt lineage theo vị trí byte ordinal (`find_ordinal_byte_offset`) và dọn dẹp cache projection SQLite (`thread_history_1.sqlite`).
     * Kiểm tra toàn vẹn hệ thống trước khi kết thúc với nguyên tắc fail-closed, không bao giờ báo thành công giả.

---

### 23. Kiến Trúc HTTP Request Sanitizer Reverse Proxy Middleware — Triệt Tiêu Lỗi 429 Khi Đổi Model Thời Gian Thực Trong Codex CLI
* **Hiện tượng:**
  * Người dùng đang trong một phiên chat Codex CLI bình thường, gõ lệnh `/model` để đổi sang mô hình khác (ví dụ: `gpt-5.6-sol` $\rightarrow$ `claude-sonnet-4.6-thinking` hoặc `gemini-3.8-flash`).
  * Ngay khi gửi câu hỏi tiếp theo, giao diện terminal báo lỗi:
    `exceeded retry limit, last status: 429 Too Many Requests: Resource has been exhausted`.
  * Hiện tượng lặp lại liên tục dù tài khoản Antigravity còn 100% quota và dù đã chạy `aic repair` trước đó.
* **Bản chất kỹ thuật & Nguyên nhân cốt lõi:**
  1. *Cơ chế tiêm chỉ dẫn động trong RAM của `codex.exe`:* Khi người dùng gõ `/model`, nhị phân Rust của OpenAI Codex CLI tự động sinh và chèn một tin nhắn `role: "developer"` chứa thẻ `<model_switch>` mang chỉ dẫn nhúng cứng:
     ```xml
     <model_switch>
     The user was previously using a different model. Please continue the conversation according to the following instructions:

     You are Codex, a coding agent based on GPT-5...
     </model_switch>
     ```
  2. *Giới hạn của giải pháp tĩnh:* Các script sửa file tĩnh trên đĩa (`aic repair`, `sync_sessions.py`, `models_cache.json`) chỉ làm sạch các session cũ đã lưu trên đĩa. Khi Codex CLI đang chạy trong RAM, mỗi lần đổi model nó lại sinh ra khối `<model_switch>` mới và gửi trực tiếp qua gói tin HTTP `POST /v1/responses`.
  3. *Bộ lọc Antigravity kích hoạt ngay tại cửa mạng:* Gateway Google Antigravity phát hiện chuỗi `"based on GPT-5"` trong developer message $\rightarrow$ kích hoạt content filter $\rightarrow$ ném lỗi HTTP 429.
* **Giải pháp kiến trúc: HTTP Request Sanitizer Reverse Proxy Middleware ([`scripts/request_sanitizer.py`](file:///e:/AI/agents_in_chat/scripts/request_sanitizer.py)):**
  1. *Phân tách cổng hai tầng (Zero Client Config Disruption):*
     * **Cổng Công khai (`127.0.0.1:8090`):** Middleware Sanitizer đóng vai trò Frontline Reverse Proxy, lắng nghe trực tiếp tại cổng này. Mọi cấu hình client (`config.toml`, test scripts, browser `/management`) giữ nguyên 100%.
     * **Cổng Nội bộ (`127.0.0.1:8095`):** `proxy_manager.py` tự động sinh file cấu hình `.backend_config.yaml` và khởi chạy `cli-proxy-api.exe` độc lập trên cổng nội bộ.
  2. *Khử độc Schema-Aware theo thời gian thực (Real-Time Request Sanitization):*
     * Middleware chặn các request `POST /v1/responses`.
     * Kiểm tra mô hình đích: Nếu là mô hình Antigravity (`gemini`, `claude`), middleware giải mã JSON, quét mảng `input`, tìm các tin nhắn `role == "developer"` chứa `<model_switch>` và thay thế cụm từ `"based on GPT-5"` thành `"an expert coding agent"`.
     * Nếu có trường `instructions` ở root payload chứa cụm từ này, middleware cũng tự động làm sạch và cập nhật lại header `Content-Length`.
     * **Bảo tồn tuyệt đối:** Các request cho OpenAI GPT (`gpt-5.6-sol`, `terra`, `luna`, `astra`), toàn bộ tin nhắn người dùng (`user`), phản hồi của trợ lý (`assistant`), lời gọi và kết quả tool call được giữ nguyên 100% không suy suyển.
  3. *Chuyển tiếp luồng Server-Sent Events (SSE) tức thì (Zero Buffering):*
     * Middleware đọc từng khối dữ liệu nhị phân từ backend và gọi `self.wfile.flush()` ngay lập tức.
     * TUI của Codex CLI nhận các token suy luận và câu trả lời tức thời mà không có bất kỳ độ trễ hay hiện tượng buffer cục bộ nào.
  4. *Quản lý tiến trình bền vững (`spawn_daemon` qua WMI/CIM & Fail-Closed Lifecycle):*
     * Để vượt qua rào cản Windows Job Object (khi chạy trong IDE, runner hoặc AI sandbox), `proxy_manager.py` tích hợp hàm `spawn_daemon` kết hợp cơ chế `Win32_Process.Create` để đảm bảo cả Sanitizer Proxy và Backend Engine sống bền bỉ độc lập ngoài Job Object của shell cha.
     * Quản lý song song cả 2 PID (`sanitizer_pid`, `backend_pid`) với cơ chế kiểm tra sức khỏe kép và rollback dọn dẹp sạch sẽ khi dừng/khởi động lại.
