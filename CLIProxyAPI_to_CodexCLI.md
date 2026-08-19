# TÀI LIỆU KỸ THUẬT: TÍCH HỢP CLIPROXYAPI (ANTIGRAVITY & OPENAI) VÀO CODEX CLI

Tài liệu hướng dẫn triển khai thực tế, cấu hình chuẩn và xử lý sự cố khi tích hợp CLIProxyAPI (Google Antigravity + OpenAI Codex) vào Codex CLI.

---

## PHẦN 1: THIẾT LẬP HỆ THỐNG TỪ ĐẦU (CLEAN SETUP)

### 1. Cài đặt và cấu hình CLIProxyAPI

CLIProxyAPI hoạt động như một Local Reverse Proxy chuyển tiếp các request từ Codex CLI sang Google CloudCode (Antigravity) và OpenAI API.

#### Bước 1.1: Chuẩn bị thư mục và Binary
1. Đặt file thực thi `cli-proxy-api.exe` vào thư mục làm việc (ví dụ: `E:\AI\CLIProxyAPI`).
2. Tạo thư mục con `auths/` để lưu trữ token OAuth.

#### Bước 1.2: Cấu hình `config.yaml`
Tạo file `config.yaml` với nội dung chuẩn hóa:

```yaml
host: "127.0.0.1"
port: 8080

auth-dir: "./auths"
request-retry: 1
max-retry-credentials: 4
max-retry-interval: 1

routing:
  strategy: "round-robin"
  session-affinity: false

oauth-model-alias:
  antigravity:
    - name: "claude-sonnet-4-6"
      alias: "claude-sonnet-4.6-thinking"
      display-name: "Claude Sonnet 4.6 (Thinking)"
    - name: "claude-opus-4-6-thinking"
      alias: "claude-opus-4.6-thinking"
      display-name: "Claude Opus 4.6 (Thinking)"
    - name: "gemini-3.7-flash-high"
      alias: "gemini-3.7-flash"
      display-name: "Gemini 3.7 Flash (High)"

oauth-excluded-models:
  codex:
    - "gpt-5.5"
    - "gpt-5.4"
    - "gpt-5.4-mini"
    - "gpt-5.3-codex-spark"
    - "codex-auto-review"
    - "gpt-image-1.5"
    - "gpt-image-2"
  antigravity:
    - "gemini-3-flash"
    - "gemini-3.1-flash-image"
    - "gemini-3.1-flash-lite"
    - "gemini-pro-agent"
    - "gemini-3.1-pro-low"
    - "gemini-3-flash-agent"
    - "gemini-3.5-flash-extra-low"
    - "gemini-3.5-flash-low"
    - "gemini-3.6-flash-high"
    - "gpt-oss-120b-medium"

debug: true
```

#### Bước 1.3: Xác thực tài khoản (OAuth Login)
1. Đăng nhập Google Antigravity (chạy nhiều lần để nạp nhiều tài khoản vào pool):
   ```bash
   .\cli-proxy-api.exe -antigravity-login -no-browser
   ```
2. Đăng nhập OpenAI (ChatGPT Plus hoặc Free):
   ```bash
   .\cli-proxy-api.exe -codex-login -no-browser
   ```
   *Lưu ý:* OAuth listener chạy trên cổng `http://localhost:1455/auth/callback`. Đảm bảo cổng này không bị chiếm dụng trong quá trình đăng nhập.

#### Bước 1.4: Khởi chạy Proxy Service
```bash
.\cli-proxy-api.exe
```

---

### 2. Cài đặt và cấu hình Codex CLI

#### Bước 2.1: Cấu hình `~/.codex/config.toml`
Trỏ Codex CLI về Provider chung `custom` và cấu hình quyền thực thi sandbox:

```toml
model = "claude-sonnet-4.6-thinking"
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

#### Bước 2.2: Cấu hình `~/.codex/models_cache.json` (Khóa TTL 2099)
Đặt `"fetched_at": "2099-01-01T00:00:00Z"` để khóa cache vĩnh viễn, ngăn Codex CLI tự động ghi đè danh sách model khi kết nối API:

```json
{
  "fetched_at": "2099-01-01T00:00:00Z",
  "client_version": "0.148.0",
  "models": [
    {
      "slug": "gemini-3.7-flash",
      "display_name": "Gemini 3.7 Flash (High)",
      "description": "Gemini 3.7 Flash (High)",
      "default_reasoning_level": "medium",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Lighter reasoning"},
        {"effort": "medium", "description": "Balances speed and reasoning"},
        {"effort": "high", "description": "Greater reasoning depth"}
      ],
      "shell_type": "shell_command",
      "visibility": "public",
      "supported_in_api": true,
      "priority": 1,
      "apply_patch_tool_type": "freeform",
      "use_responses_lite": true,
      "supports_search_tool": true,
      "tool_mode": "code_mode_only",
      "multi_agent_version": "v2",
      "context_window": 1048576,
      "max_context_window": 1048576,
      "effective_context_window_percent": 90,
      "input_modalities": ["text", "image"]
    },
    {
      "slug": "claude-sonnet-4.6-thinking",
      "display_name": "Claude Sonnet 4.6 (Thinking)",
      "description": "Claude Sonnet 4.6 (Thinking)",
      "default_reasoning_level": "medium",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Lighter reasoning"},
        {"effort": "medium", "description": "Balances speed and reasoning"},
        {"effort": "high", "description": "Greater reasoning depth"},
        {"effort": "xhigh", "description": "Extra high reasoning depth"}
      ],
      "shell_type": "shell_command",
      "visibility": "public",
      "supported_in_api": true,
      "priority": 2,
      "apply_patch_tool_type": "freeform",
      "use_responses_lite": true,
      "supports_search_tool": true,
      "tool_mode": "code_mode_only",
      "multi_agent_version": "v2",
      "context_window": 200000,
      "max_context_window": 200000,
      "effective_context_window_percent": 90,
      "input_modalities": ["text", "image"]
    },
    {
      "slug": "claude-opus-4.6-thinking",
      "display_name": "Claude Opus 4.6 (Thinking)",
      "description": "Claude Opus 4.6 (Thinking)",
      "default_reasoning_level": "medium",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Lighter reasoning"},
        {"effort": "medium", "description": "Balances speed and reasoning"},
        {"effort": "high", "description": "Greater reasoning depth"},
        {"effort": "xhigh", "description": "Extra high reasoning depth"}
      ],
      "shell_type": "shell_command",
      "visibility": "public",
      "supported_in_api": true,
      "priority": 3,
      "apply_patch_tool_type": "freeform",
      "use_responses_lite": true,
      "supports_search_tool": true,
      "tool_mode": "code_mode_only",
      "multi_agent_version": "v2",
      "context_window": 200000,
      "max_context_window": 200000,
      "effective_context_window_percent": 90,
      "input_modalities": ["text", "image"]
    },
    {
      "slug": "gpt-5.6-sol",
      "display_name": "GPT-5.6 Sol",
      "description": "OpenAI flagship model with extended reasoning",
      "default_reasoning_level": "low",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Lighter reasoning"},
        {"effort": "medium", "description": "Balances speed and reasoning"},
        {"effort": "high", "description": "Greater reasoning depth"}
      ],
      "shell_type": "shell_command",
      "visibility": "public",
      "supported_in_api": true,
      "priority": 4,
      "apply_patch_tool_type": "freeform",
      "use_responses_lite": true,
      "supports_search_tool": true,
      "tool_mode": "code_mode_only",
      "multi_agent_version": "v2",
      "context_window": 272000,
      "max_context_window": 272000,
      "effective_context_window_percent": 90,
      "input_modalities": ["text", "image"]
    },
    {
      "slug": "gpt-5.6-terra",
      "display_name": "GPT-5.6 Terra",
      "description": "OpenAI balanced reasoning model",
      "default_reasoning_level": "medium",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Lighter reasoning"},
        {"effort": "medium", "description": "Balances speed and reasoning"},
        {"effort": "high", "description": "Greater reasoning depth"}
      ],
      "shell_type": "shell_command",
      "visibility": "public",
      "supported_in_api": true,
      "priority": 5,
      "apply_patch_tool_type": "freeform",
      "use_responses_lite": true,
      "supports_search_tool": true,
      "tool_mode": "code_mode_only",
      "multi_agent_version": "v2",
      "context_window": 272000,
      "max_context_window": 272000,
      "effective_context_window_percent": 90,
      "input_modalities": ["text", "image"]
    },
    {
      "slug": "gpt-5.6-luna",
      "display_name": "GPT-5.6 Luna",
      "description": "OpenAI fast reasoning model",
      "default_reasoning_level": "medium",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Lighter reasoning"},
        {"effort": "medium", "description": "Balances speed and reasoning"},
        {"effort": "high", "description": "Greater reasoning depth"}
      ],
      "shell_type": "shell_command",
      "visibility": "public",
      "supported_in_api": true,
      "priority": 6,
      "apply_patch_tool_type": "freeform",
      "use_responses_lite": true,
      "supports_search_tool": true,
      "tool_mode": "code_mode_only",
      "multi_agent_version": "v2",
      "context_window": 272000,
      "max_context_window": 272000,
      "effective_context_window_percent": 90,
      "input_modalities": ["text", "image"]
    }
  ]
}
```

#### Bước 2.3: Đồng bộ Danh mục Phiên chat và Provider cho tính năng Resume
Chạy script Python để đồng bộ `model_provider = 'custom'` trong SQLite và file `.jsonl`:

```python
import sqlite3, os, json

# 1. Đồng bộ database state_5.sqlite
db_path = os.path.expanduser("~/.codex/state_5.sqlite")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE threads SET model_provider = 'custom';")
    conn.commit()
    conn.close()

# 2. Đồng bộ header session file .jsonl
sessions_dir = os.path.expanduser("~/.codex/sessions")
if os.path.exists(sessions_dir):
    for root, dirs, files in os.walk(sessions_dir):
        for f in files:
            if f.endswith(".jsonl"):
                p = os.path.join(root, f)
                try:
                    with open(p, "r", encoding="utf-8") as sfile:
                        lines = sfile.readlines()
                    if lines:
                        d = json.loads(lines[0])
                        if "payload" in d and isinstance(d["payload"], dict):
                            d["payload"]["model_provider"] = "custom"
                            lines[0] = json.dumps(d) + "\n"
                            with open(p, "w", encoding="utf-8") as sfile:
                                sfile.writelines(lines)
                except Exception:
                    pass
```

---

## PHẦN 2: CÁC VẤN ĐỀ KỸ THUẬT VÀ GIẢI PHÁP XỬ LÝ

### 1. Hiện tượng Treo phản hồi 50s khi gặp Soft Rate Limit
* **Nguyên nhân:** Proxy dùng `routing.strategy: "fill-first"` và `request-retry: 5`, dẫn đến lặp retry số mũ trên cùng 1 account bị rate limit thay vì chuyển account khác trong pool.
* **Giải pháp:** Cấu hình `routing.strategy: "round-robin"`, `max-retry-credentials: 4`, `request-retry: 1`.

### 2. Lỗi `unknown provider for model claude-opus-4.6-thinking`
* **Nguyên nhân:** Tên upstream trong Antigravity là `claude-opus-4-6-thinking` (dấu gạch ngang). Nếu alias sai lệch, Proxy sẽ loại bỏ model khỏi registry.
* **Giải pháp:** Khai báo alias chuẩn trong `config.yaml`:
  ```yaml
  oauth-model-alias:
    antigravity:
      - name: "claude-opus-4-6-thinking"
        alias: "claude-opus-4.6-thinking"
        display-name: "Claude Opus 4.6 (Thinking)"
  ```

### 3. Xung đột Tool Router khi chuyển Model trong phiên chat
* **Nguyên nhân:** `codex-rs/core/src/tools/spec_plan.rs` gỡ bỏ `ApplyPatchHandler` nếu model có `apply_patch_tool_type: null` hoặc `use_responses_lite: false`, gây gãy payload khi replay lịch sử có lệnh patch code.
* **Giải pháp:** Khai báo đầy đủ `apply_patch_tool_type: "freeform"`, `use_responses_lite: true`, `tool_mode: "code_mode_only"`, `multi_agent_version: "v2"` cho toàn bộ model trong `models_cache.json`.

### 4. Cơ chế Khóa TTL bộ nhớ đệm Model (`fetched_at: "2099-01-01T00:00:00Z"`)
* **Nguyên nhân:** Codex CLI có cơ chế định kỳ đồng bộ danh sách model từ server. Nếu không khóa TTL, file `models_cache.json` sẽ bị ghi đè về danh sách mặc định của OpenAI, làm mất các model Antigravity đã thêm.
* **Giải pháp:** Đặt trường `fetched_at` thành một thời điểm xa trong tương lai (`2099-01-01T00:00:00Z`) để chặn vĩnh viễn hành vi ghi đè tự động.

### 5. Gemini Flash không sinh văn bản trên phiên chat quá dài
* **Nguyên nhân:** Google Gemini Thinking dồn toàn bộ output tokens cho khối suy nghĩ (`thought`) và đóng stream với `finishReason: STOP` khi hết token budget, không phát ra khối `content` (text).
* **Khuyến nghị:** Đối với phiên chat lịch sử siêu dài (>10 triệu tokens tích lũy), ưu tiên sử dụng `claude-sonnet-4.6-thinking`, `claude-opus-4.6-thinking`, `gpt-5.6-luna`, `gpt-5.6-terra` (các model có cơ chế bắt buộc mở block text sau thinking).

### 6. Lỗi 400 `messages.1.content.0.text.text: Field required` trên Claude
* **Nguyên nhân:** Khi turn trước của Gemini không sinh text, file `.jsonl` ghi nhận các lượt User liên tiếp. Khi chuyển sang Claude, bộ dịch Anthropic tạo ra block assistant rỗng, vi phạm quy tắc bắt buộc của Anthropic API.
* **Giải pháp:** Cắt tỉa các dòng turn rỗng ở cuối file `.jsonl` để khôi phục cấu trúc xen kẽ User-Assistant hợp lệ.

### 7. Xử lý trạng thái Quota 429 trên Provider `custom`
* **Bản chất:** Thông báo `exceeded retry limit, last status: 429` xuất hiện khi model hết hạn ngạch từ tài khoản OpenAI. Proxy đưa model vào cooldown và tự động định tuyến sang các model/tài khoản khác còn quota.

### 8. Đồng bộ danh mục phiên chat và Provider khi Resume
* **Nguyên nhân:** Codex CLI đọc `model_provider` từ SQLite `state_5.sqlite` để khởi tạo `TurnContext`. Nếu còn lưu cờ `openai`, request sẽ không đi qua Proxy cục bộ.
* **Giải pháp:** Cập nhật `UPDATE threads SET model_provider = 'custom'` trong SQLite và cập nhật dòng header của tất cả file `.jsonl` trong `~/.codex/sessions/`.
