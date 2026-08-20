# Agents in Chat (AIC)

Công cụ tích hợp và quản trị Quota Pool đa tài khoản cho OpenAI Codex CLI, phục vụ 2 chức năng chính:

1. **Mở rộng mô hình AI:** Đưa các mô hình hàng đầu như Gemini 3.7 Flash, Claude Sonnet 4.6, Claude Opus 4.6, GPT Sol, GPT Terra, GPT Luna vào làm việc trực tiếp trong giao diện OpenAI Codex CLI.
2. **Gộp Quota đa tài khoản:** Kết nối nhiều tài khoản OAuth Google và OpenAI, tự động cân bằng tải Round-Robin và tự động chuyển tài khoản khi gặp giới hạn Rate Limit 429 để duy trì làm việc liên tục không bị gián đoạn.

---

## 1. Cài đặt

Kịch bản cài đặt tự động tải binary nếu thiếu, cấu hình `config.toml`, nạp cache model chuẩn, khóa chống ghi đè, đồng bộ lịch sử chat cũ và đăng ký lệnh toàn cục `aic` vào PATH và PowerShell Profile.

- **Windows (PowerShell):**
  ```powershell
  .\install.ps1
  ```
- **Linux / macOS / WSL:**
  ```bash
  ./install.sh
  ```

---

## 2. Đăng nhập tài khoản

Có thể đăng nhập nhiều tài khoản liên tiếp để nạp vào Quota Pool.

- **Google Antigravity (Gemini và Claude):**
  ```bash
  aic login_agy
  ```
  In link xác thực OAuth trên terminal để click hoặc copy dán vào trình duyệt.

- **OpenAI Codex (GPT):**
  ```bash
  aic login_codex          # Menu chọn phương thức 1 hoặc 2
  aic login_codex browser  # Lấy link đăng nhập OAuth
  aic login_codex device   # Lấy mã xác thực thiết bị qua auth0.openai.com/activate
  ```

---

## 3. Quản trị hệ thống qua lệnh aic

Sau khi cài đặt, có thể gọi lệnh `aic` từ bất kỳ thư mục nào trên hệ thống:

| Lệnh | Chức năng |
| :--- | :--- |
| `aic start` | Khởi động Proxy API chạy ngầm |
| `aic stop` | Dừng Proxy API và giải phóng RAM |
| `aic restart` | Khởi động lại Proxy Service |
| `aic status` | Kiểm tra trạng thái Proxy, Provider và Khóa Cache |
| `aic test` | Chạy bộ kiểm thử tự động 7 suites |
| `aic sync` | Đồng bộ lịch sử phiên chat giữa custom và openai |
| `aic login_agy` | Đăng nhập tài khoản Google Antigravity |
| `aic login_codex` | Đăng nhập tài khoản OpenAI Codex |
| `aic uninstall` | Khôi phục nguyên bản cài đặt gốc Codex CLI |

---

## 4. Cấu trúc thư mục

```
agents_in_chat/
├── bin/
│   ├── aic.py                   # Lõi thực thi CLI Python
│   ├── aic.cmd                  # Wrapper cho Windows
│   ├── aic.ps1                  # Wrapper cho PowerShell
│   └── aic                      # Wrapper cho Linux và macOS
├── auths/                       # Thư mục lưu token OAuth
├── cli-proxy-api.exe            # Binary Proxy Service
├── config.yaml                  # Cấu hình định tuyến và retry
├── install.ps1 / install.sh     # Cài đặt và đăng ký PATH
├── start.ps1 / start.sh         # Khởi động dịch vụ ngầm
├── stop.ps1 / stop.sh           # Dừng dịch vụ
├── uninstall.ps1 / uninstall.sh # Khôi phục cài đặt gốc
├── scripts/
│   ├── configure_codex_toml.py  # Xử lý cấu hình TOML
│   └── sync_sessions.py         # Đồng bộ lịch sử phiên chat
├── docs/
│   ├── models_cache_template.json # Template danh sách model
│   └── KIEN_TRUC_VA_XU_LY_LOI.md # Tài liệu kỹ thuật và xử lý lỗi
└── tests/
    └── run_tests.py             # Bộ chạy kiểm thử 7 suites
```
