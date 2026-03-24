# Commercial Domain — DOMAIN_SPEC_commercial.md
**Status:** Design phase — nothing built yet  
**Visibility:** Separate public repo when ready  
**Created:** 2026-03-07

---

## Vision

A multi-tenant digital commerce platform for small and medium businesses. 
Digital-first, low-touch order flow, real transactions. Built to be used 
in the real world — not a demo.

The platform serves two purposes simultaneously:
1. **Real business utility** — Vera's jewelry shop, cleaning service, 
   RVS consulting presence, future client properties
2. **Portfolio and learning** — demonstrates full-stack commercial 
   architecture, SMB digital commerce patterns, and modern Rails development

If it doesn't work as a real business tool, it doesn't count.

---

## Business Entities

| Entity | Type | Primary Need | Status |
|--------|------|-------------|--------|
| RVS Associates | Consulting + real estate | Portfolio, contact, insights | Owner |
| Vera's jewelry (name TBD) | Artisanal e-commerce | Showcase, payments, WhatsApp | Phase 1 |
| Cleaning by Vera | Service business | Booking, contact, trust | Phase 2 |
| Terra do Cafe | Artisanal umbrella brand | Future expansion | Future |
| [Client properties] | Future tenants | TBD | Future |

**Note on naming:** Vera's jewelry site name and domain TBD. 
Brazilian angle is the hook. Name decision before build starts.

---

## Core Architecture

### Multi-Tenant Rails 8 Platform

One application, multiple storefronts. Each business is a tenant.
Subdomain routing: `vera-jewelry.rvsassociates.com` or custom domain
pointing to same app.

```
RVSAssociates Platform (Rails 8)
├── Admin panel          ← Robert manages all tenants
├── Tenant: jewelry      ← Vera manages her own products
├── Tenant: cleaning     ← future
└── Tenant: [client]     ← future commercial opportunity
```

### Why Multi-Tenant From Day One
Adding a tenant field to models at the start costs nothing.
Retrofitting multi-tenancy later costs everything.
This is the architectural decision that separates a website from a platform.

### Tech Stack

```
Backend:      Rails 8
Frontend:     Hotwire (Turbo + Stimulus) — server-rendered, no React
Database:     PostgreSQL (multi-domain ready, shared with mini-moi future)
Payments:     Stripe Connect — each tenant receives to own account
Storage:      S3-compatible (product images, assets)
Deployment:   Kamal (Docker-based, any VPS)
Hosting:      Hetzner or DigitalOcean VPS
WhatsApp:     WhatsApp Business link first, API integration later
Email:        Postmark or similar transactional email
```

**Why Rails 8 + Hotwire:**
DHH's current recommended stack. Fast, server-rendered, no JavaScript 
framework complexity. Enterprise patterns without enterprise overhead.
The right tool for multi-tenant commerce at this scale.

**Why Stripe Connect (not basic Stripe):**
Stripe Connect lets each tenant receive payments directly to their own 
bank account. Platform can take a fee if desired. This is how real 
commerce platforms work — Shopify, Etsy, Airbnb all use this pattern.
The difference between "I added a payment button" and "I built a platform."

---

## Build Sequence

### Phase 1 — End-to-End Plumbing (First)
Goal: One product, one sale, deployed. Ugly but functional.

```
[ ] Rails 8 app scaffolded, PostgreSQL connected
[ ] Multi-tenant model — Tenant, subdomain routing
[ ] Basic auth — admin (Robert), tenant owner (Vera), customer
[ ] One product model with image upload
[ ] Stripe Connect — tenant onboarding + checkout flow
[ ] Order model — created on successful payment
[ ] WhatsApp button — simple link, no API needed yet
[ ] Kamal deployment — live on VPS
[ ] rvsassociates.com pointing to app
[ ] vera subdomain or custom domain live
[ ] End-to-end test: customer visits → adds product → pays → order recorded
```

**Do not touch frontend design until this works end-to-end.**

### Phase 2 — Commercial Features
```
[ ] Product catalog management (Vera adds/edits products herself)
[ ] Order management dashboard
[ ] Admin panel (manage all tenants from one place)
[ ] Email notifications (order confirmed, shipped)
[ ] Basic inventory tracking
[ ] Bilingual support (Portuguese + English)
[ ] WhatsApp Business API proper integration
[ ] Customer accounts (optional — guest checkout first)
```

### Phase 3 — Frontend Polish
```
[ ] Brand identity per tenant
[ ] Design system (shared components, tenant-customizable)
[ ] Mobile optimization
[ ] Product photography integration
[ ] SEO basics
[ ] Brazilian market optimizations (PIX payment? future)
```

### Phase 4 — AI Integration (future, connects to mini-moi)
```
[ ] Product recommendations based on browsing
[ ] Customer behavior signals → learned profile
[ ] Inventory alerts, demand patterns
[ ] Content generation for product descriptions
[ ] RVS Associates blog fed by geopolitics insights
```

---

## Data Model (Core)

```ruby
# Multi-tenancy foundation
Tenant
  subdomain, custom_domain, name, stripe_account_id
  has_many :products, :orders, :customers

Product  
  tenant_id, name, description, price, currency
  stock_quantity, images, active
  available_languages: [:en, :pt]

Order
  tenant_id, customer_id, stripe_payment_intent_id
  status, total, currency, line_items (JSON)
  whatsapp_order: boolean

Customer
  tenant_id, email, name, phone, preferred_language
  whatsapp_number

# Shared with mini-moi future
# domain field makes this multi-platform ready
```

---

## Digital-First Order Flow

```
Customer lands on product page
        ↓
Browses catalog (no account required)
        ↓
Adds to cart
        ↓
Checkout — email only, no forced account creation
        ↓
Stripe payment (card) OR WhatsApp order (for Brazilian market)
        ↓
Order confirmed — email receipt
        ↓
Vera notified — email + WhatsApp
        ↓
No human required unless Vera chooses to engage
```

**WhatsApp as parallel sales channel:**
Brazilian customers often prefer to close via WhatsApp.
"Buy via WhatsApp" button sends pre-filled message with product details.
Vera closes the sale conversationally, marks order manually in admin.
This is low-touch for the customer, personal for the seller.
Both channels feed the same order management system.

---

## Staff Tools (Admin Panel)

Robert (platform admin):
- Manage all tenants
- View platform-wide metrics
- Onboard new tenants
- Manage Stripe Connect accounts

Vera (tenant admin):
- Add/edit/remove products
- View and manage orders
- Update content (bilingual)
- View sales dashboard
- WhatsApp order management

**The admin panel is as important as the storefront.**
A platform is only as good as the tools it gives operators.

---

## Bilingual Architecture

All user-facing content supports Portuguese and English from day one.

```ruby
# Rails i18n
# Product descriptions stored in both languages
product.name_en, product.name_pt
product.description_en, product.description_pt

# URL structure
vera.rvsassociates.com/en/products
vera.rvsassociates.com/pt/products

# Auto-detect from browser, manual toggle available
```

Brazilian Portuguese is the priority for Vera's sites.
English for RVS Associates and international reach.

---

## Repository Structure

```
rvsassociates-platform/          ← separate repo from personal-ai-agents
├── app/
│   ├── models/
│   │   ├── tenant.rb
│   │   ├── product.rb
│   │   ├── order.rb
│   │   └── customer.rb
│   ├── controllers/
│   │   ├── storefront/          ← customer-facing
│   │   └── admin/               ← tenant + platform admin
│   └── views/
│       ├── storefront/
│       └── admin/
├── config/
│   └── deploy.yml               ← Kamal deployment config
├── db/
│   └── schema.rb                ← PostgreSQL schema
└── README.md
```

**Separate repo from personal-ai-agents.**
Different language (Ruby vs Python), different audience (public commercial),
different deployment (VPS vs local MacBook).

---

## Connection to personal-ai-agents

Kept separate now. Connected later via API.

```
Future integration points:
  - RVS Associates blog ← geopolitics insights from mini-moi
  - Customer behavior signals → mini-moi feedback loop pattern
  - AI product descriptions ← same LLM infrastructure
  - Shared PostgreSQL schema pattern (domain field)
```

The two platforms share architectural principles, not code.
Same feedback loop pattern, different domain content.

---

## Privacy and Security

| Concern | Approach |
|---------|----------|
| Customer payment data | Never stored — Stripe handles entirely |
| Customer emails | Stored, never sold, GDPR-considerate |
| Stripe keys | Environment variables, never in repo |
| Multi-tenant isolation | Tenant scoping on every query |
| Admin access | Role-based, Robert vs tenant owner vs customer |

---

## Domains and Hosting

| Domain | Purpose | Status |
|--------|---------|--------|
| rvsassociates.com | Platform root + RVS consulting | Owned |
| [vera-jewelry].com | Vera's jewelry storefront | TBD — decide before build |
| cleaningbyvera.com | Cleaning service | Future |
| terradocafe.com | Umbrella brand | Future |

---

## Success Definition

**Phase 1 complete when:**
One real product is listed, one real payment is processed,
one real order is recorded, deployed to live URL.
Everything else is detail.

**Platform complete when:**
Vera can manage her own store without Robert's help.
A new tenant can be onboarded in under one hour.
Orders flow from customer to payment to notification with no manual steps.

---

## Notes

- Name decision for Vera's jewelry site needed before domain purchase and build
- PIX (Brazilian instant payment) worth evaluating for Phase 3 — dominant payment method in Brazil
- WhatsApp Business API requires Facebook Business verification — start the process early, it takes time
- Kamal deployment requires a VPS — provision early, cheap (~$6/month Hetzner)
- Rails 8 is current stable — do not use older tutorials, many patterns have changed
