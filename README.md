# 🚀 Agents in Chat (AIC) - Multi-Model Quota Pool cho OpenAI Codex CLI

Giải pháp tích hợp đa mô hình hàng đầu (**Gemini 3.7 Flash, Claude Sonnet/Opus 4.6, GPT Sol / Terra / Luna**) vào trực tiếp giao diện **OpenAI Codex CLI** thông qua cơ chế Round-Robin Proxy và OAuth Quota Pool thông minh.

---

## ⚡ 1. CÀI ĐẶT 1-CHẠM (ONE-CLICK INSTALLATION)

> **💡 Tự động tải Binary:** Kịch bản cài đặt sẽ **tự động tải bản mới nhất của `CLIProxyAPI` từ GitHub Releases** nếu trong thư mục chưa có file binary. Bạn không cần tải trước bằng tay!

### 🪟 Trên Windows (PowerShell):
Mở PowerShell tại thư mục dự án và chạy:
```powershell
.\install.ps1
```
*(Script sẽ tự động cấu hình `config.toml`, nạp 6 model chuẩn vào `models_cache.json`, khóa chống ghi đè, đồng bộ lịch sử chat cũ và **đăng ký lệnh toàn cục `aic` vào PATH & PowerShell Profile**).*

### 🐧 Trên Linux / macOS / WSL:
Mở Terminal tại thư mục dự án và chạy:
```bash
./install.sh
```

---

## 🔑 2. ĐĂNG NHẬP TÀI KHOẢN OAUTH (NẠP QUOTA POOL)

Sau khi cài đặt, bạn có thể đứng ở bất kỳ thư mục nào và sử dụng lệnh `aic` để đăng nhập tài khoản:

### 1. Đăng nhập Google Antigravity (Gemini 3.7 & Claude 4.6):
```bash
aic login_agy
```
*(In ra đường dẫn xác thực OAuth trên terminal để bạn click hoặc copy dán vào trình duyệt).*

### 2. Đăng nhập OpenAI Codex (GPT Sol / Terra / Luna):
Lệnh hỗ trợ 2 phương thức linh hoạt (Browser OAuth hoặc Device Code):
* **Chế độ tương tác (Tự chọn menu 1 hoặc 2):**
  ```bash
  aic login_codex
  ```
* **Lấy link xác thực OAuth (In link để click/copy):**
  ```bash
  aic login_codex browser
  ```
* **Nhập mã xác thực thiết bị (Device Code Flow - auth0.openai.com/activate):**
  ```bash
  aic login_codex device
  ```

> **💡 Mẹo Pool Quota:** Bạn có thể chạy lệnh đăng nhập **nhiều lần với các tài khoản Gmail/OpenAI khác nhau** để nạp vào Pool. Proxy sẽ tự động phân phối vòng tròn (Round-Robin) và tự động chuyển tài khoản khi gặp giới hạn Rate Limit (HTTP 429).

---

## 🛠️ 3. QUẢN TRỊ TOÀN CỤC BẰNG LỆNH `aic` (TỪ BẤT KỲ ĐÂU)

Sau khi cài đặt, bạn có thể đứng ở **bất kỳ thư mục dự án nào** trên máy tính và gõ lệnh **`aic`**:

| Lệnh | Mô tả |
| :--- | :--- |
| **`aic login_agy`** | Đăng nhập tài khoản Google Antigravity (Gemini & Claude). |
| **`aic login_codex`** | Đăng nhập tài khoản OpenAI Codex (Browser hoặc Device Code). |
| **`aic start`** | Khởi động Proxy API chạy ngầm trên cổng `8080` (ẩn hoàn toàn, không popup). |
| **`aic stop`** | Tắt tiến trình Proxy và giải phóng RAM. |
| **`aic restart`** | Khởi động lại Proxy Service. |
| **`aic status`** | Kiểm tra nhanh trạng thái Proxy, Model Provider và Khóa Cache. |
| **`aic test`** | Chạy bộ kiểm thử tự động toàn diện (7/7 Test Suites). |
| **`aic sync`** | **Đồng bộ lịch sử chat**: Chuyển đổi nhãn các phiên chat cũ giữa `custom` và `openai` để hiển thị trong menu `Resume`. |
| **`aic uninstall`** | **Factory Reset 100%** về nguyên bản gốc của OpenAI Codex CLI và gỡ sạch `aic`. |

---

## 📂 4. CẤU TRÚC THƯ MỤC DỰ ÁN

```
agents_in_chat/
├── bin/
│   ├── aic.py                   # Python CLI Core Engine đa nền tảng
│   ├── aic.cmd                  # Entrypoint cho Windows (CMD/PowerShell)
│   ├── aic.ps1                  # PowerShell Dispatcher
│   └── aic                      # Bash Dispatcher cho Linux/macOS
├── auths/                       # Chứa các file token OAuth (.json)
├── cli-proxy-api.exe            # Binary Proxy Service (tự động tải nếu thiếu)
├── config.yaml                  # Cấu hình routing round-robin & retry
├── install.ps1 / install.sh     # Trình cài đặt 1-chạm & đăng ký PATH
├── start.ps1 / start.sh         # Kịch bản khởi động proxy ngầm (SW_HIDE)
├── stop.ps1 / stop.sh           # Kịch bản dừng proxy
├── uninstall.ps1 / uninstall.sh # Trình Factory Reset về nguyên bản
├── scripts/
│   ├── configure_codex_toml.py  # Xử lý cấu hình TOML an toàn không trùng lặp
│   └── sync_sessions.py         # Đồng bộ lịch sử phiên chat SQLite & JSONL
├── docs/
│   ├── models_cache_template.json # Template chuẩn 6 model
│   └── KIEN_TRUC_VA_XU_LY_LOI.md # Kiến trúc kết nối & xử lý 8 sự cố Codex CLI
└── tests/
    ├── test_proxy_health.py
    ├── test_config_yaml.py
    ├── test_models_cache.py
    ├── test_codex_config.py
    ├── test_sqlite_and_sessions.py
    ├── test_gemini_tool_calling.py
    ├── test_claude_tool_calling.py
    └── run_tests.py             # Bộ chạy kiểm thử 7/7 suites
```
