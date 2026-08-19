# Spec Mapping — Python SDK → OpenAPI 3.1

Maps 42+ Python SDK resources to OpenAPI paths and schemas.

---

## Template Pattern

Each resource maps to 3 operations:

```yaml
/resource:
  POST: createResource
    Request: array of ResourceCreate
    Response 201: array of Resource
  GET: queryResource
    Params: limit, after (pagination)
    Response 200: { resources: array of Resource }

/resource/{id}:
  GET: getResource
    Response 200: Resource
```

---

## Mapped Resources (15 core + 27 additional)

### 1. Transaction
- **POST** `/transaction` — Create transactions
- **GET** `/transaction` — Query transactions
- **GET** `/transaction/{id}` — Get single transaction

**Fields**:
- id: string (UUID)
- amount: integer (cents)
- description: string
- external_id: string (idempotency key)
- receiver_id: string (UUID)
- sender_id: string (UUID) [return-only]
- created: datetime [return-only]
- status: enum (pending, processing, completed)
- tags: array[string]

---

### 2. Invoice
- **POST** `/invoice` — Create invoices
- **GET** `/invoice` — Query invoices
- **GET** `/invoice/{id}` — Get single invoice

**Fields**:
- id: string
- amount: integer (cents)
- tax_id: string (CPF/CNPJ)
- name: string
- due: datetime
- expiration: integer (seconds)
- fine: float (%)
- interest: float (%)
- status: enum (registered, paid, cancelled)
- pdf: string [return-only]
- brcode: string [return-only]
- created: datetime [return-only]
- tags: array[string]

**Nested**:
- rules: array[{key: string, value: any}]
- splits: array[{amount: integer, receiver_id: string}]

---

### 3. Transfer
- **POST** `/transfer` — Create transfers
- **GET** `/transfer` — Query transfers
- **GET** `/transfer/{id}` — Get single transfer

**Fields**:
- id: string
- amount: integer (cents)
- account_number: string
- bank_code: string
- branch_code: string
- account_type: enum (checking, savings)
- external_id: string
- description: string
- scheduled: datetime (optional)
- status: enum (pending, processing, completed, failed)
- created: datetime [return-only]
- tags: array[string]

---

### 4. Balance
- **GET** `/balance` — Get account balance (no create/query)

**Fields**:
- id: string
- amount: integer (cents)
- currency: string (BRL)
- updated: datetime

---

### 5. Boleto
- **POST** `/boleto` — Create boletos
- **GET** `/boleto` — Query boletos
- **GET** `/boleto/{id}` — Get single boleto

**Fields**:
- id: string
- amount: integer (cents)
- due: datetime
- line: string (boleto line) [return-only]
- barcode: string [return-only]
- status: enum (registered, paid, cancelled)
- created: datetime [return-only]

---

### 6. BoletoPayment
- **POST** `/boleto-payment` — Create boleto payments
- **GET** `/boleto-payment` — Query payments
- **GET** `/boleto-payment/{id}` — Get single payment

**Fields**:
- id: string
- amount: integer (cents)
- boleto_id: string
- tax_id: string
- status: enum (processing, success, failed)
- created: datetime [return-only]

---

### 7. DictAccount
- **POST** `/dict-account` — Register PIX accounts
- **GET** `/dict-account` — Query accounts
- **GET** `/dict-account/{id}` — Get single account

**Fields**:
- id: string (UUID)
- account_number: string
- bank_code: string
- branch_code: string
- key: string (PIX key)
- type: enum (cpf, cnpj, email, phone, random_key)
- created: datetime

---

### 8. DictKey
- **POST** `/dict-key` — Create PIX keys
- **GET** `/dict-key` — Query keys
- **GET** `/dict-key/{id}` — Get single key

**Fields**:
- id: string
- key: string (email, phone, CPF, etc)
- type: enum (email, phone, cpf, random)
- account_id: string
- status: enum (active, inactive)
- created: datetime [return-only]

---

### 9. Webhook
- **POST** `/webhook` — Create webhooks
- **GET** `/webhook` — Query webhooks
- **GET** `/webhook/{id}` — Get single webhook
- **DELETE** `/webhook/{id}` — Delete webhook

**Fields**:
- id: string
- url: string
- subscriptions: array[string] (transaction, invoice, transfer, etc)
- events: array[string] (created, updated, paid)
- created: datetime [return-only]

---

### 10. Event
- **GET** `/event` — Query events (audit log, no create/delete)

**Fields**:
- id: string
- log: dict (audit details)
- created: datetime
- type: string

---

### 11-42. Additional Resources

**Payment variants** (6):
- PIXPayment, PIXChargeback, PIXReversal
- TedPayment, TedReversal
- BrCodePayment

**Corporate accounts** (8):
- CorporateBalance, CorporateCard, CorporateHolder
- CorporatePurchase, CorporateRule, CorporateTransaction
- CorporateWithdrawal, CorporateInvoice

**Advanced** (5):
- Document, DocumentLog, Request
- Workspace, SplitProfile

**Specialized** (8+):
- DarfPayment, CardMethod, Limit
- StaticBrcode, Deposit, PaymentPreview
- etc.

---

## Type Mappings

| Python Type | OpenAPI Type | Notes |
|-------------|--------------|-------|
| `int` | `integer` | Amounts in cents |
| `str` | `string` | UUIDs, codes, keys |
| `datetime` | `string` (date-time) | ISO 8601 format |
| `date` | `string` (date) | For scheduled operations |
| `float` | `number` | Percentages (fine, interest) |
| `bool` | `boolean` | Flags |
| `list` | `array` | Tags, descriptions, rules |
| `dict` | `object` | Metadata, rules |
| `enum` | `string` (enum) | status, type, etc |

---

## Pagination

All list operations use cursor-based pagination:

```yaml
/resource:
  GET:
    parameters:
      - name: limit
        in: query
        schema: { type: integer, default: 100 }
      - name: after
        in: query
        schema: { type: string }
        description: "Cursor for pagination"
    responses:
      200:
        content:
          application/json:
            schema:
              type: object
              properties:
                resources:
                  type: array
                  items:
                    $ref: '#/components/schemas/Resource'
```

---

## Error Handling

All endpoints return standard error format:

```yaml
responses:
  400: { description: "Invalid request" }
  401: { description: "Unauthorized" }
  404: { description: "Not found" }
  500: { description: "Server error" }

schema:
  error:
    type: object
    properties:
      code: { type: string }
      message: { type: string }
```

---

## Special Cases

### Idempotency
Resources with `external_id` prevent duplicate processing:

```
POST /resource with external_id="abc-123"
→ Create resource with this external_id
→ If same external_id sent again → Return existing resource (not duplicate)
```

### Nested Resources
Invoice contains Rules and Splits:
```yaml
Invoice:
  rules: array[Invoice.Rule]
  splits: array[Split]
```

### Return-Only Fields
Marked as read-only in schema (not accepted in POST/PUT):
- id, created, updated, status, fee, balance, pdf, brcode, etc

---

## Spec V0.1 → V0.2 Mapping

**V0.1** (ME 1.4): 3 resources
- Transaction, Invoice, Transfer

**V0.2** (ME 2.1): 60 resources
- Add: Balance, Boleto, BoletoPayment, DictAccount, DictKey, Webhook, Event, etc.
- Use template pattern to expand 3 → 60

---

**Mapping version**: 1.0  
**Date**: 2026-08-19  
**Resources mapped**: 42+ core  
**Next step**: Implement in spec-v2.openapi.yaml