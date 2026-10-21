# Financial Payment Integration API (FastAPI)

Demo REST API untuk pencatatan payment, pemrosesan webhook bertanda tangan,
dan laporan payment. Dokumentasi interaktif tersedia melalui `/docs` dan
OpenAPI JSON melalui `/openapi.json`.

## Menjalankan

Atur `DATABASE_URL` di `.env` menggunakan connection string PostgreSQL Anda.
Semua endpoint payment dan laporan menggunakan tabel `payments` Neon yang
sudah ada. API juga membuat tabel pendamping untuk idempotency dan deduplikasi
webhook; tabel payment yang ada tidak diganti atau dibuat ulang.

```bash
.venv/bin/uvicorn app.main:app --reload
```

Webhook memerlukan secret yang sama dengan pengirim:

```bash
export WEBHOOK_SECRET="ganti-dengan-secret-yang-aman"
.venv/bin/uvicorn app.main:app --reload
```

## Endpoint

| Method | Endpoint | Fungsi |
| --- | --- | --- |
| `GET` | `/` | Info service |
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/payments` | Membuat payment |
| `GET` | `/api/v1/payments` | Daftar payment |
| `GET` | `/api/v1/payments/{id}` | Detail payment berdasarkan UUID atau payment number |
| `POST` | `/api/v1/webhooks/payment` | Memproses webhook payment |
| `GET` | `/api/v1/reports/payment-summary?from=YYYY-MM-DD&to=YYYY-MM-DD` | Ringkasan per status dan currency |
| `GET` | `/api/v1/reports/daily-payments?from=YYYY-MM-DD&to=YYYY-MM-DD` | Ringkasan harian per currency |

Parameter tanggal laporan opsional dan tanggal `to` bersifat inklusif.

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

Data payment dibaca dan ditulis di tabel Neon dan dibagikan antar-worker. Belum
ada autentikasi, ledger, ekspor CSV/XLSX/PDF, maupun domain sales/inventory.
Jangan gunakan sebagai pemroses pembayaran produksi.
# financial-payment-integration-api-python
