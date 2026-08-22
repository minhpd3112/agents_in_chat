# Agents in Chat (AIC)

Công cụ tích hợp và quản trị Quota Pool đa tài khoản cho OpenAI Codex CLI, phục vụ 2 chức năng chính:

1. **Đa mô hình trên cùng một phiên chat:** Làm việc và chuyển đổi linh hoạt giữa các mô hình AI hàng đầu trực tiếp trên cùng một đoạn chat trong OpenAI Codex CLI.
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

- **Google Antigravity:**
  ```bash
  aic login_agy
  ```

- **OpenAI Codex:**
  ```bash
  aic login_codex
  ```

**Bắt đầu sử dụng Codex CLI:**
```bash
codex
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
| `aic test` | Chạy bộ kiểm thử tự động |
| `aic login_agy` | Đăng nhập tài khoản Google Antigravity |
| `aic login_codex` | Đăng nhập tài khoản OpenAI Codex |
| `aic uninstall` | Khôi phục cấu hình Codex CLI về provider OpenAI gốc, bảo toàn lịch sử chat để tiếp tục resume |

---

## 4. Giao diện Web Dashboard

Theo dõi trực quan trạng thái tài khoản, lưu lượng request và live logs qua trình duyệt:

- **Địa chỉ:** `http://127.0.0.1:8080/management.html`
- **Mật khẩu mặc định:** `aic` (tích chọn Remember password để tự động lưu phiên)

---

## 5. Cấu trúc thư mục

```
agents_in_chat/
├── bin/
│   ├── aic.py                   # Lõi thực thi CLI Python
│   ├── aic.cmd                  # Wrapper cho Windows
│   ├── aic.ps1                  # Wrapper cho PowerShell
│   └── aic                      # Wrapper cho Linux và macOS
├── auths/                       # Thư mục lưu token OAuth và cấu hình xác thực
├── static/
│   └── management.html          # Giao diện Web Dashboard
├── cli-proxy-api.exe            # Binary Proxy Service
├── config.yaml                  # Cấu hình định tuyến, retry và upstream providers
├── install.ps1 / install.sh     # Cài đặt và đăng ký PATH
├── start.ps1 / start.sh         # Khởi động dịch vụ ngầm
├── stop.ps1 / stop.sh           # Dừng dịch vụ
├── uninstall.ps1 / uninstall.sh # Khôi phục cấu hình OpenAI gốc & bảo toàn lịch sử chat
├── scripts/
│   ├── configure_codex_toml.py  # Xử lý cấu hình TOML
│   └── sync_sessions.py         # Đồng bộ lịch sử phiên chat
├── docs/
│   ├── models_cache_template.json # Template 7 models chuẩn
│   └── KIEN_TRUC_VA_XU_LY_LOI.md # Tài liệu kỹ thuật và xử lý lỗi
└── tests/
    └── run_tests.py             # Bộ chạy kiểm thử 8 suites
```
