# Domain & Student Data — External API Specification

This document specifies the **external HTTP APIs** consumed when fetching domains and student/staff data, and uploading captured fingerprint templates. It is intended for a web-app implementation that needs to integrate with the same upstream services.

---

## 1. Two-tier API Model

There are two kinds of upstream servers:

1. **Master (aggregator) server** — single base URL configured per client install. Authenticates the *client* and returns the list of domains the client may access.
2. **Domain server** — one per registered domain. Each domain has its own base URL and its own credentials (issued by the master). All academic structure (departments, programme types, levels) and student/staff data is fetched from the domain server.

```
              ┌──────────────────────────┐
              │   MASTER SERVER          │   base = SERVER_URL
              │   /api/v1/domain/all     │   creds = PUBLIC_KEY / PRIVATE_KEY
              │   /api/v1/enrollment/    │
              │       byte/upload        │
              └────────────┬─────────────┘
                           │ returns Domain[] (host, identity, secret)
            ┌──────────────┴───────────────┐
            ▼                              ▼
   ┌──────────────────┐           ┌──────────────────┐
   │  DOMAIN A        │           │  DOMAIN B        │  base = domain.host
   │  /api/biometric/ │           │  ...             │  creds = domain.identity / domain.secret
   │     departments  │           │                  │
   │     programmes-  │           │                  │
   │       types      │           │                  │
   │     levels       │           │                  │
   │     users        │           │                  │
   └──────────────────┘           └──────────────────┘
```

### Authentication

All requests use two custom headers (capitalisation matters):

| Header     | Value (master calls)        | Value (domain calls) |
| ---------- | --------------------------- | -------------------- |
| `Identity` | `PUBLIC_KEY` (RSA public)   | `domain.identity`    |
| `Secret`   | `PRIVATE_KEY` (RSA private) | `domain.secret`      |

`Content-Type: application/json` is sent on every request, including GETs.

Non-2xx responses with a JSON body should include `{ "message": "<reason>" }`. The client surfaces `message` to the user.

---

## 2. Master Server APIs

Base URL: `{SERVER_URL}` (configured by the operator at install time).

### 2.1 `GET /api/v1/domain/all` — list domains

Returns the domains this client may access. The returned `identity` and `secret` are the credentials to use for each domain's own API.

**Request**
```http
GET {SERVER_URL}/api/v1/domain/all
Identity: <PUBLIC_KEY>
Secret:   <PRIVATE_KEY>
Content-Type: application/json
```

**Response 200**
```json
{
  "success": true,
  "data": [
    {
      "host":     "https://campus-a.example.edu",
      "identity": "ident_abc123...",
      "secret":   "sec_xyz789..."
    },
    {
      "host":     "https://campus-b.example.edu",
      "identity": "...",
      "secret":   "..."
    }
  ]
}
```

**Response — failure**
```json
{
  "success": false,
  "message": "Reason for failure"
}
```

The client treats `success != true` as an error.

---

### 2.2 `POST /api/v1/enrollment/byte/upload` — upload fingerprint templates

Uploads a batch of captured fingerprint templates. The `data` and `user` fields are **AES-encrypted with the client's symmetric key** (server has the matching key); after decryption, `data` is a base64-encoded SourceAFIS fingerprint template (binary). Raw fingerprint images are NOT sent.

**Request**
```http
POST {SERVER_URL}/api/v1/enrollment/byte/upload
Identity: <PUBLIC_KEY>
Secret:   <PRIVATE_KEY>
Content-Type: application/json
```
```json
{
  "user": "<AES-encrypted operator username>",
  "prints": [
    {
      "finger":         "RIGHT_THUMB",
      "data":           "<AES-encrypted base64 of fingerprint template>",
      "identification": "<authId of the user the print belongs to>",
      "name":           "Jane Doe",
      "captureDate":    "2026-04-29T10:14:32.123",
      "category":       "STUDENT"
    }
  ]
}
```

**Field reference**

| Field            | Type   | Notes                                                                 |
| ---------------- | ------ | --------------------------------------------------------------------- |
| `user`           | string | Encrypted operator username.                                          |
| `prints[]`       | array  | One entry per captured fingerprint.                                   |
| `prints.finger`  | string | One of: `RIGHT_THUMB`, `RIGHT_INDEX`, `RIGHT_MIDDLE`, `RIGHT_RING`, `RIGHT_PINKY`, `LEFT_THUMB`, `LEFT_INDEX`, `LEFT_MIDDLE`, `LEFT_RING`, `LEFT_PINKY`. |
| `prints.data`    | string | Encrypted base64 fingerprint template.                                |
| `prints.identification` | string | The user's `authId` (from §3.4 response).                       |
| `prints.name`    | string | User's full name.                                                     |
| `prints.captureDate` | string | ISO-8601 local date-time, no timezone (e.g. `2026-04-29T10:14:32.123`). |
| `prints.category`| string | One of: `STAFF`, `STUDENT`, `SENATE`, `Teaching`, `SeniorNonTeaching`, `JuniorNonTeaching`. |

**Response 200** — body is opaque to the client; any 200 is treated as success and the corresponding records are marked uploaded.

---

## 3. Domain Server APIs

Base URL: `{domain.host}` (returned from §2.1). All four endpoints below use the domain's own credentials in `Identity` / `Secret` headers.

> **Important — IDs.** All IDs returned by domain endpoints are the canonical upstream IDs. They must be sent back **as-is** in subsequent queries (e.g. when fetching users, the `department` parameter is the same `id` returned by §3.1).

### 3.1 `GET /api/biometric/departments` — list departments

**Request**
```http
GET {domain.host}/api/biometric/departments
Identity: <domain.identity>
Secret:   <domain.secret>
Content-Type: application/json
```

**Response 200** — bare JSON array (no envelope):
```json
[
  { "id": 12, "name": "Computer Science", "code": "CSC" },
  { "id": 13, "name": "Mathematics" }
]
```

| Field  | Type    | Notes                |
| ------ | ------- | -------------------- |
| `id`   | number  | Upstream department ID. Use this value when calling `/users`. |
| `name` | string  | Department name.     |
| `code` | string? | Optional short code. |

---

### 3.2 `GET /api/biometric/programmes-types` — list programme types

A domain may have **zero** programme types. An empty array means the domain does not classify by programme type, and `level` should be omitted in subsequent user queries.

**Request**
```http
GET {domain.host}/api/biometric/programmes-types
Identity: <domain.identity>
Secret:   <domain.secret>
Content-Type: application/json
```

**Response 200**
```json
[
  { "id": 1, "name": "Undergraduate" },
  { "id": 2, "name": "Postgraduate" }
]
```

| Field  | Type   | Notes                          |
| ------ | ------ | ------------------------------ |
| `id`   | number | Upstream programme-type ID.    |
| `name` | string | Programme-type display name.   |

---

### 3.3 `GET /api/biometric/levels?programmeType={programmeTypeId}` — list levels

Returns the levels (year/stage) that belong to a given programme type.

**Request**
```http
GET {domain.host}/api/biometric/levels?programmeType=1
Identity: <domain.identity>
Secret:   <domain.secret>
Content-Type: application/json
```

**Query parameters**

| Param           | Required | Notes                                  |
| --------------- | -------- | -------------------------------------- |
| `programmeType` | yes      | Programme-type ID from §3.2.           |

**Response 200**
```json
[
  { "id": 100, "name": "100 Level" },
  { "id": 200, "name": "200 Level" }
]
```

| Field  | Type   | Notes              |
| ------ | ------ | ------------------ |
| `id`   | number | Upstream level ID. |
| `name` | string | Level name.        |

---

### 3.4 `GET /api/biometric/users` — list students / staff

Lists users in a department. If the domain has any programme types (§3.2), `level` is **required**; otherwise it must be omitted.

**Request**
```http
GET {domain.host}/api/biometric/users?department=12&level=100&user=CSC
Identity: <domain.identity>
Secret:   <domain.secret>
Content-Type: application/json
```

**Query parameters**

| Param        | Required                                              | Notes                                                |
| ------------ | ----------------------------------------------------- | ---------------------------------------------------- |
| `department` | yes                                                   | Upstream department ID from §3.1.                    |
| `level`      | required iff the domain has programme types (§3.2)    | Upstream level ID from §3.3.                         |
| `user`       | optional                                              | Search term applied to `userId` (matric/staff number). |

**Response 200**
```json
[
  {
    "authId":   "9f0c8b2a-...-stable-id",
    "userId":   "CSC/2020/001",
    "name":     "Jane Doe",
    "category": "STUDENT"
  }
]
```

| Field      | Type   | Notes                                                                                  |
| ---------- | ------ | -------------------------------------------------------------------------------------- |
| `authId`   | string | **Stable** opaque identifier. Use this as `prints.identification` on upload (§2.2).    |
| `userId`   | string | Human-readable identifier (matric/staff number). Unique within the domain.             |
| `name`     | string | Full name.                                                                             |
| `category` | string | One of: `STAFF`, `STUDENT`, `SENATE`, `Teaching`, `SeniorNonTeaching`, `JuniorNonTeaching`. |

> **Identity rotation.** The same `userId` may be returned with a different `authId` over time (account re-issue). Web clients should treat `authId` as the durable key and tolerate `userId` reuse.

---

## 4. Required Fetch Order

Because each step depends on the previous one's response, calls must happen in this order:

```
1. GET  master  /api/v1/domain/all                                     → Domain[]
2. for each domain:
     GET  {domain.host}/api/biometric/departments                       → Department[]
     GET  {domain.host}/api/biometric/programmes-types                  → ProgrammeType[]
     for each programme type:
        GET  {domain.host}/api/biometric/levels?programmeType={id}      → Level[]
3. GET  {domain.host}/api/biometric/users
        ?department={departmentId}
        [&level={levelId}]      ← required iff programme types exist
        [&user={searchTerm}]                                            → User[]
4. POST master  /api/v1/enrollment/byte/upload                          (when capturing)
```

---

## 5. Endpoint Cheatsheet

| Endpoint                                                       | Method | Auth   | Purpose                |
| -------------------------------------------------------------- | ------ | ------ | ---------------------- |
| `{SERVER_URL}/api/v1/domain/all`                               | GET    | master | List domains           |
| `{SERVER_URL}/api/v1/enrollment/byte/upload`                   | POST   | master | Upload templates       |
| `{domain.host}/api/biometric/departments`                      | GET    | domain | List departments       |
| `{domain.host}/api/biometric/programmes-types`                 | GET    | domain | List programme types   |
| `{domain.host}/api/biometric/levels?programmeType={id}`        | GET    | domain | List levels            |
| `{domain.host}/api/biometric/users?department=&level=&user=`   | GET    | domain | List students/staff    |

---

## 6. Enum Reference

**User category** (`category` in §2.2 and §3.4):
`STAFF`, `STUDENT`, `SENATE`, `Teaching`, `SeniorNonTeaching`, `JuniorNonTeaching`.

---

## 7. First-time Admin Setup

Before any of the APIs above can be called, the operator (admin) must complete a one-time configuration. This is what binds the client to the master server and produces the credentials used in the `Identity` / `Secret` headers.

### 7.1 What the admin provides

| Field        | Description |
| ------------ | ----------- |
| Server URL   | Base URL of the master server (must start with `http://` or `https://`; trailing slash is stripped). |
| Username     | Operator username — included (encrypted) in every upload payload. |
| Password     | Operator password. Minimum **8 characters**. Must be entered twice and match. |
| Secret       | Symmetric (AES) key used to encrypt fingerprint upload payloads. Issued by the master-server administrator. |
| RSA key pair | Generated locally on this screen — see §7.2. |

### 7.2 Generating the RSA key pair

1. Click **Generate Keys**. A fresh RSA public/private pair is created in the browser/client; nothing leaves the machine yet.
2. Copy the **public key** and register it on the master server against this operator account. The master will only accept calls whose `Identity` header equals this public key.
3. The **private key** stays on this machine — it becomes the `Secret` header on master calls.
4. Both keys are required to submit the form.

### 7.3 Submission

When the admin submits the form, the client persists the configuration locally:

| Stored as     | Value |
| ------------- | ----- |
| `SERVER_URL`  | The submitted server URL (trailing `/` removed). |
| `USER_NAME`   | Plain operator username. |
| `PASSWORD`    | bcrypt hash of the password (verified on subsequent logins). |
| `SECRET`      | AES symmetric key (used to encrypt upload payloads). |
| `PUBLIC_KEY`  | RSA public key (sent as `Identity` to the master). |
| `PRIVATE_KEY` | RSA private key (sent as `Secret` to the master). |
| `CONFIGURED`  | `"true"` — the flag the app checks on next launch. |

After this step the app routes to the login screen and, from then on, it can call the master endpoints in §2 and trigger the domain-sync flow in §4.

### 7.4 Web-app equivalent

For a web-app implementation, the same setup translates to:

1. The master-server admin creates the operator account and issues the AES `SECRET`.
2. The operator signs in to the web app, generates an RSA key pair (server-side, e.g. in a KMS), and registers the public key with the master under their account.
3. The web app stores `SERVER_URL`, `PUBLIC_KEY`, `PRIVATE_KEY`, and `SECRET` server-side (vault / KMS / encrypted column) — never in browser storage. The operator's bcrypt password hash is stored alongside.
4. From the next session onward, the BFF uses these to call `GET /api/v1/domain/all` and proceed with the rest of the fetch order in §4.
