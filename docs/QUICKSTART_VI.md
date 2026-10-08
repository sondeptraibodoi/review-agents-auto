# Khởi chạy nhanh Lean Review Agents v0.3.0

## Windows PowerShell

```powershell
cd C:\path\to\lean-review-agents-v0.3.0
py -m pip install -e ".[all]"
py -m playwright install chromium
leanreview doctor
```

## Laravel: PHPUnit và PHPStan

Trong project Laravel đã `composer install` và đã cài `phpunit`/`phpstan`:

```powershell
cd C:\projects\server-system
leanreview backend check --output .leanreview\backend.json
leanreview review --base origin/main --max-tokens 2500
```

PHPUnit chạy code test thật; nên cấu hình `.env.testing` và database test riêng trước.

## PostgreSQL / MySQL

Tạo một tài khoản database **chỉ có SELECT**. Nhập chuỗi kết nối vào biến môi trường của phiên PowerShell hiện tại:

```powershell
$env:LEANREVIEW_DATABASE_URL = 'postgresql://review_ro:YOUR_PASSWORD@127.0.0.1:5432/myapp'
# Hoặc MySQL:
# $env:LEANREVIEW_DATABASE_URL = 'mysql://review_ro:YOUR_PASSWORD@127.0.0.1:3306/myapp'

leanreview db inspect --tables fleet_route_monitoring
leanreview db explain --sql-file examples\query.sql
leanreview db explain --sql-file examples\query.sql --analyze
```

Cảnh báo: dòng gán password có thể nằm trong lịch sử shell. Không sử dụng mật khẩu production hoặc đưa secret vào Git. `--analyze` thực thi SELECT nên chỉ dùng trên database test/replica khi có thể.

## API Laravel

Chạy Laravel tại localhost, chỉnh `examples/api-contract.json` theo endpoints thực tế:

```powershell
leanreview api check --url http://127.0.0.1:8000 --contract examples\api-contract.json
# Có Bearer token của tài khoản test:
$env:TEST_API_TOKEN = 'YOUR_TEST_BEARER_TOKEN'
leanreview api check --url http://127.0.0.1:8000 --contract examples\api-contract.json --bearer-env TEST_API_TOKEN
```

Mặc định chỉ gửi GET/HEAD và chỉ truy cập localhost. Giao diện vẫn dùng `leanreview ui audit` như v0.2.0.

## Các giới hạn

- Có code kết nối thật thông qua `psycopg` và `PyMySQL`, nhưng kiểm thử tự động tại đây dùng driver giả lập, chưa có PostgreSQL/MySQL server live.
- Rule SQL chỉ là biện pháp bổ sung; không thay thế tài khoản DB chỉ đọc.
- Kết nối DB, chạy PHPUnit/PHPStan và kiểm tra API đều **không gọi AI**.
