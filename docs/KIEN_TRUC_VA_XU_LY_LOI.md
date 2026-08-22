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
│              CLIProxyAPI (Local Reverse Proxy)              │
│                      (127.0.0.1:8080)                       │
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
  * `claude-opus-4-6-thinking` ➔ `claude-opus-4.6-thinking`
  * `gemini-3.7-flash-high` ➔ `gemini-3.7-flash`

### 2. Cấu hình Codex CLI (`~/.codex/config.toml`)
```toml
model = "gemini-3.7-flash"
model_reasoning_effort = "high"
service_tier = "default"
model_provider = "custom"

[model_providers.custom]
name = "Custom Quota Pool"
base_url = "http://127.0.0.1:8080/v1"
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
* **Giải pháp:** Bắt buộc đặt `"visibility": "list"` cho toàn bộ 6 model trong file cache template.

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
  5. *Windows In-Place Write Fallback:* Khi gặp `WinError 5 Access is denied` (do file session đang được mở bởi tiến trình Codex CLI đang hoạt động), script tự động chuyển sang ghi in-place an toàn với `seek(0)` và `truncate()` mà không làm gián đoạn tiến trình.

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

### 12. Tích hợp Mô hình Miễn phí Ngoại vi qua Chuẩn `OpenAI-Compatibility` (Mô hình Ox Alpha 1M Context từ OpenCode Zen)
* **Bản chất mô hình & Hạ tầng nguồn:**
  * Mô hình **Ox Alpha** (ID nội bộ: `x-preview-f-free`) là mô hình thử nghiệm reasoning (CoT) chuyên sâu cho coding với context window lên tới **1.048.576 tokens (1M Context)** và output 131.072 tokens.
  * Hạ tầng Backend: **OpenCode Zen API** (`https://opencode.ai/zen/v1/chat/completions`), hỗ trợ truy cập mở với header `Authorization: Bearer public` không yêu cầu đăng nhập hay tạo tài khoản.
* **Cơ chế chuyển tiếp Responses API $\leftrightarrow$ OpenAI Chat Completions:**
  1. *Cấu hình Upstream Provider (`config.yaml`):* Khai báo entry `openai-compatibility` với endpoint `https://opencode.ai/zen/v1`, bearer key `public`, alias mapping `x-preview-f-free` $\rightarrow$ `ox-alpha`.
  2. *Biên dịch Giao thức Luồng (SSE Protocol Translation):* CLIProxyAPI tự động dịch chuyển gói tin Responses API (`/v1/responses`) từ Codex CLI sang định dạng Chat Completions SSE của OpenCode Zen, đồng thời stream lại các block `reasoning_summary` và reasoning tokens về TUI của Codex CLI.
  3. *Đăng ký Xác thực trong Auth Manager (`auths/`):* Bộ điều phối luồng của Proxy yêu cầu file cấu hình xác thực `auths/openai-compatible-opencode-zen.json` để đăng ký candidate cho provider `openai-compatible-opencode-zen` (tránh lỗi 503 `auth_unavailable`). File này được tự động tạo bởi `bin/aic.py`, `install.ps1`, và `install.sh`.
  4. *Tương thích Cache Model (`models_cache_template.json`):* Nạp model `ox-alpha` vào cache `~/.codex/models_cache.json` với `visibility: "list"`, `context_window: 1048576`, và `supported_reasoning_levels: ["low", "high", "max"]` (mặc định: `max`).
  5. *Đồng bộ Lịch sử (Cross-Model Seamless Switching):* Cho phép người dùng chuyển đổi mượt mà giữa Gemini 3.7 Flash, Claude Sonnet 4.6, Claude Opus 4.6, GPT Sol, GPT Terra, GPT Luna và Ox Alpha trên cùng một phiên làm việc Codex CLI mà không bị đứt đoạn lịch sử chat.

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
  1. **Nhóm Anthropic Claude (`claude-sonnet-4.6`, `claude-opus-4.6`):**
     * Đặt `default_reasoning_level: "high"`.
     * Loại bỏ nấc dư thừa `xhigh` $\rightarrow$ Menu chỉ hiển thị gọn gàng `[Low, Medium, High (default)]`.
  2. **Nhóm Google Gemini (`gemini-3.7-flash`):**
     * Đặt `default_reasoning_level: "high"`.
     * Menu hiển thị `[Low, Medium, High (default)]`.
  3. **Nhóm OpenCode Zen (`ox-alpha`):**
     * Đặt `default_reasoning_level: "max"`.
     * Menu hiển thị `[Low, High, Max (default)]`.
* **Hiệu quả:**
  * Người dùng chỉ cần gõ `/model`, chọn mô hình mong muốn và nhấn `Enter` là hệ thống tự động kích hoạt mức suy luận cao nhất/sâu nhất mà không cần thêm bất kỳ thao tác bấm phím nào.

