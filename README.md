# Financial Payment Integration API (FastAPI)

REST API untuk autentikasi, pengelolaan customer dan payment account, payment,
webhook bertanda tangan, serta laporan. Dokumentasi interaktif tersedia melalui
`/docs` dan OpenAPI JSON melalui `/openapi.json`.

## Menjalankan

Atur `DATABASE_URL` di `.env` menggunakan connection string PostgreSQL Anda.
Semua endpoint memakai tabel `customers` dan `payments` yang sudah ada. API
membuat tabel pendamping dengan prefix `financial_payment_api_` untuk user,
sesi login, payment account, idempotency, dan deduplikasi webhook. Tabel
payment/customer yang ada tidak diganti atau dibuat ulang.

```bash
.venv/bin/uvicorn app.main:app --reload
```

Webhook memerlukan secret yang sama dengan pengirim:

```bash
export WEBHOOK_SECRET="ganti-dengan-secret-yang-aman"
.venv/bin/uvicorn app.main:app --reload
```

Set secret admin awal sebelum menjalankan API untuk pertama kali. Admin dibuat
saat tabel user diinisialisasi; mengubah environment setelah admin dibuat tidak
mengganti password yang sudah tersimpan.

```bash
export INITIAL_ADMIN_EMAIL="admin@example.com"
export INITIAL_ADMIN_PASSWORD="gunakan-password-unik-minimal-12-karakter"
.venv/bin/uvicorn app.main:app --reload
```

## Endpoint

| Method | Endpoint | Fungsi |
| --- | --- | --- |
| `GET` | `/` | Info service |
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/auth/register` | Registrasi customer baru |
| `POST` | `/api/v1/auth/login` | Login dan memperoleh bearer token |
| `POST` | `/api/v1/auth/logout` | Mencabut sesi aktif |
| `GET`, `PATCH` | `/api/v1/auth/me` | Lihat/perbarui profil |
| `GET`, `POST` | `/api/v1/customers` | Daftar/buat customer (admin) |
| `GET`, `PATCH`, `DELETE` | `/api/v1/customers/{id}` | Detail, ubah, atau nonaktifkan customer |
| `POST` | `/api/v1/customers/{id}/user` | Buat user untuk customer (admin) |
| `GET`, `POST` | `/api/v1/customers/{id}/accounts` | Lihat/tambahkan payment account |
| `DELETE` | `/api/v1/customers/{id}/accounts/{account_id}` | Nonaktifkan payment account |
| `POST` | `/api/v1/payments` | Membuat payment |
| `GET` | `/api/v1/payments` | Daftar payment milik customer atau semua (admin) |
| `GET` | `/api/v1/payments/{id}` | Detail payment berdasarkan UUID atau payment number |
| `POST` | `/api/v1/webhooks/payment` | Memproses webhook payment |
| `GET` | `/api/v1/reports/payment-summary` | Ringkasan per status dan currency (admin) |
| `GET` | `/api/v1/reports/daily-payments` | Ringkasan harian per currency (admin) |

Endpoint selain `/`, `/health`, registrasi, login, dan webhook memerlukan
`Authorization: Bearer <access_token>`. Token sesi berlaku 12 jam; logout
mencabut token yang sedang digunakan. Customer hanya dapat mengakses profil,
payment, dan payment account miliknya. Laporan dan pengelolaan customer
memerlukan role `admin`.

Registrasi membuat customer dan user baru sekaligus. Email yang sudah terdaftar
sebagai customer lama tidak dapat diklaim lewat registrasi publik; admin perlu
membuat user-nya melalui `/api/v1/customers/{id}/user`. Parameter tanggal
laporan opsional dan tanggal `to` bersifat inklusif.

### Authentication

`POST /api/v1/auth/register`:

```json
{
  "name": "Nama Customer",
  "email": "customer@example.com",
  "password": "gunakan-password-unik-12-karakter",
  "phone": "+628123456789"
}
```

Password minimal 12 karakter dan disimpan menggunakan PBKDF2-HMAC-SHA256.
Token sesi acak disimpan sebagai hash di database, bukan sebagai token mentah.
Login menggunakan `email` dan `password`; response memuat `access_token`,
`expires_at`, dan data user. Gunakan token pada header Bearer untuk request
terproteksi.

Contoh body login (`POST /api/v1/auth/login`) di Swagger UI:

```json
{
  "email": "admin@example.com",
  "password": "<INITIAL_ADMIN_PASSWORD>"
}
```

Ganti placeholder dengan nilai `INITIAL_ADMIN_PASSWORD` yang dikonfigurasi
secara lokal. Jangan simpan password asli di dokumentasi atau source control.
Nama field request menggunakan huruf kecil `email` dan `password`.

Admin awal hanya dibuat jika `INITIAL_ADMIN_EMAIL` dan
`INITIAL_ADMIN_PASSWORD` dikonfigurasi saat skema pertama kali dibuat. Akun
admin tidak dapat dibuat melalui registrasi publik.

### Customer dan payment account

Admin dapat mengelola customer melalui `/api/v1/customers`; `DELETE` melakukan
soft-deactivation dengan mengubah status menjadi `INACTIVE`. User customer
dapat membaca dan mengubah nama/telepon profilnya di `/api/v1/auth/me`.
Payment account menyimpan label, provider, jenis akun, dan empat digit terakhir
opsional. Endpoint ini **tidak** menerima atau menyimpan nomor kartu penuh,
CVV, PIN, password bank, atau kredensial payment provider.

### Create payment

Request `POST /api/v1/payments`:

```json
{
  "reference": "PAY-001",
  "amount": 150000,
  "currency": "IDR",
  "customer_id": "00000000-0000-0000-0000-000000000101",
  "method": "BANK_TRANSFER"
}
```

`reference` wajib unik, `amount` harus positif, dan `currency` merupakan kode
tiga huruf (dinormalisasi menjadi uppercase). `customer_id` wajib berisi UUID
customer yang sudah ada dan `method` wajib diisi. Request menerima header
`Idempotency-Key` opsional; penggunaan ulang key menghasilkan `409 Conflict`.

Response `201 Created`:

```json
{
  "id": "01a06cb0-c035-7302-abaa-9f5cd711ac56",
  "reference": "PAY-001",
  "amount": 150000,
  "currency": "IDR",
  "status": "PENDING",
  "created_at": "2026-10-21T16:00:00",
  "updated_at": "2026-10-21T16:00:00",
  "customer_id": "00000000-0000-0000-0000-000000000101",
  "method": "BANK_TRANSFER",
  "gateway": null,
  "gateway_transaction_id": null,
  "paid_at": null,
  "description": null
}
```

Payment baru disimpan ke tabel Neon dan berstatus `PENDING`. Daftar payment
mengembalikan array JSON; detail dapat dicari menggunakan UUID `id` atau
`payment_number`. Detail yang tidak ditemukan menghasilkan `404 Not Found`.
Request tidak valid menghasilkan `422` dengan bentuk
`{"error":"invalid request","details":[...]}`; konflik menghasilkan `409`
dengan bentuk `{"detail":"..."}`.

### Webhook payment

`POST /api/v1/webhooks/payment` menerima:

```json
{
  "event_id": "evt-001",
  "type": "payment.success",
  "reference": "PAY-001"
}
```

Sertakan `X-Webhook-Signature: sha256=<hex>` yang berisi HMAC-SHA256 atas body
request mentah menggunakan `WEBHOOK_SECRET`. Tipe event yang didukung adalah
`payment.success` dan `payment.failed`. Event unik hanya dapat diproses sekali;
transisi hanya diizinkan dari `PENDING` atau `PROCESSING`.

Response:

```json
{
  "event_id": "evt-001",
  "reference": "PAY-001",
  "status": "SUCCESS"
}
```

### Laporan

`GET /api/v1/reports/payment-summary` menghasilkan:

```json
{
  "items": [
    {"status": "PENDING", "currency": "IDR", "count": 1, "amount": 150000}
  ]
}
```

`GET /api/v1/reports/daily-payments` menghasilkan item dengan tanggal pembuatan
payment (`date`), `currency`, `count`, dan `amount`. Tidak ada konversi
antar-mata-uang.

## Batasan demo

Data payment dan customer dibaca/ditulis di Neon dan dibagikan antar-worker.
Belum ada verifikasi email, rate limiting login, alur reset password, ledger,
atau ekspor CSV/XLSX/PDF. Sebelum dipakai di produksi, tambahkan kontrol tersebut
dan gunakan HTTPS. Jangan gunakan sebagai pemroses pembayaran produksi.
# financial-payment-integration-api-python
