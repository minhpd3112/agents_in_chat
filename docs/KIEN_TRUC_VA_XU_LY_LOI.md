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
