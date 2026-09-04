# WDH-to-FlowOne Provisioning Component — Working Specification

**Date:** August 28, 2026  
**Status:** Synchronous SOAP component implemented and tested locally  
**Scope:** Synchronous network provisioning only  
**Historical fidelity:** Business flow based on Robert's direct experience;
payload and element mappings are intentionally synthetic

## 1. Purpose

Demonstrate the Phase 1 provisioning boundary in which WING Digital Hub (WDH)
sends a small commercial request to FlowOne and receives one synchronous,
deterministic success or failure result.

WDH does not know or send the configuration required by every network element.
It supplies four business fields:

1. IMSI;
2. MDN/MSISDN;
3. service package; and
4. roaming package.

WDH generates a correlation identifier for technical tracing. FlowOne expands
the four business fields into element-specific operations.

## 2. Northbound WDH request

WDH-facing route:

```text
POST /api/v1/network-activations
```

Example:

```json
{
  "imsi": "310150123456789",
  "mdn": "+13125550101",
  "service_package": "DATA_SMS",
  "roaming_package": "DOMESTIC"
}
```

Allowed demonstration packages:

```text
service_package: DATA_ONLY | SMS_ONLY | DATA_SMS
roaming_package: HOME_ONLY | DOMESTIC
```

The demo intentionally excludes voice and a large catalog of options. These
four inputs are sufficient to show that a billing/commercial platform sends a
small entitlement request and mediation owns the technical decomposition.

### Post-activation charging boundary

Successful FlowOne provisioning makes the SIM eligible to use the requested
service. It does not represent usage processing or online charging.

After activation, the conceptual downstream flow is:

```text
Active SIM generates packet-data usage
        -> packet gateway / policy and enforcement function
        -> Diameter Gy charging interaction
        -> WDH-aligned online charging / rating capability
        -> usage authorization, reservation, debit, and later billing detail
```

The Gy boundary is retained in the architecture as a dotted, post-activation
interface. It is not implemented, mocked, or exercised in this component. No
usage event is required to prove the provisioning transaction.

## 3. SOAP mock system boundary

The WDH-facing operation remains REST/JSON. WDH's connector translates that
request into a synchronous SOAP 1.1/XML call to the separate FlowOne service:

```text
POST /mock/flowone/v1/FlowOneProvisioningService
Content-Type: text/xml; charset=utf-8
SOAPAction: ActivateSubscriber
```

Its WSDL is available at:

```text
GET /mock/flowone/v1/FlowOneProvisioningService?wsdl
```

The SOAP header carries WDH's generated correlation ID. The body carries IMSI,
MSISDN, service package, and roaming package. The default local address is
`http://127.0.0.1:8096` and can be changed with `FLOWONE_BASE_URL`.

This is an actual HTTP/SOAP boundary. The browser or Postman normally calls
WDH's REST API; WDH's Python connector builds the SOAP envelope, calls
FlowOne, and parses the XML response. A SOAP client or Postman can also call
the FlowOne mock directly for learning and evidence.

A ready-to-paste request body is retained at
`fixtures/flowone_activate_subscriber_soap.xml`.

## 4. Illustrative element decomposition

| Element | Demonstration operation | Illustrative profile decision |
|---|---|---|
| HSS | Upsert subscriber and APN profile | IMSI/MSISDN, service entitlement, and roaming profile |
| AAA | Apply packet-data authorization profile | Standard IoT data access or no-data policy |
| PCRF | Apply packet-data policy profile | Standard IoT data policy or no-data policy |
| SMSC | Apply SMS service profile | SMS enabled or disabled |
| SIM OTA | Apply SIM over-the-air profile | Home-only or domestic-roaming OTA profile |

These five targets are plausible demonstration abstractions, not a claim that
the historical UScellular implementation used these exact payloads, sequence,
or names.

The terminology is grounded in public 3GPP material. Subscriber data standards
cover IMSI/MSISDN, APN configuration, subscribed QoS and AMBR, SMS-related
subscription data, and access/roaming restrictions. See:

- [ETSI / 3GPP TS 23.008 — Organization of subscriber data](https://www.etsi.org/deliver/etsi_ts/123000_123099/123008/11.13.00_60/ts_123008v111300p.pdf)
- [ETSI / 3GPP TS 29.272 — MME and SGSN related interfaces based on Diameter](https://www.etsi.org/deliver/etsi_ts/129200_129299/129272/13.11.00_60/ts_129272v131100p.pdf)

## 5. Synchronous result semantics

### Success

- all five element operations return success;
- FlowOne returns `overall_status: SUCCESS` and `FLOW-200`;
- no rollback is required; and
- WDH marks the service `ACTIVE`.

### Element failure

- the failed element returns its element-specific result;
- later elements are not attempted;
- every earlier successful operation is rolled back;
- FlowOne returns `overall_status: FAILURE` and generic code `FLOW-400`; and
- WDH marks the attempt `ACTIVATION_FAILED` and does not activate service.

The current mock assumes rollback succeeds. Partial rollback and manual network
recovery are valid future scenarios but are deliberately excluded from this
first component.

## 6. Demonstration result codes

These are body-level FlowOne demonstration codes, not HTTP status codes.

| Code | Meaning |
|---|---|
| `FLOW-200` | All required elements provisioned successfully |
| `FLOW-400` | Generic orchestration failure returned to WDH |
| `FLOW-401` | HSS provisioning failure |
| `FLOW-402` | AAA/data-authorization provisioning failure |
| `FLOW-403` | PCRF/policy provisioning failure |
| `FLOW-404` | SMSC provisioning failure |
| `FLOW-405` | SIM OTA provisioning failure |
| `FLOW-499` | Element not attempted after an earlier failure |

Prefixing the values prevents confusion with HTTP codes such as HTTP 405
`Method Not Allowed`.

## 7. SOAP transport and business outcomes

The FlowOne mock returns HTTP 200 with an
`ActivateSubscriberResponse` SOAP body when orchestration completed and
produced a definitive business result, whether that result is `SUCCESS` or
`FAILURE`. A malformed SOAP envelope or invalid contract produces HTTP 500
with a SOAP Fault.

The WDH API returns HTTP 201 because it creates and records a network-activation
attempt. The created resource contains either:

```text
wdh_service_status: ACTIVE
```

or:

```text
wdh_service_status: ACTIVATION_FAILED
```

An unreachable FlowOne service, timeout, non-success HTTP transport response,
or invalid response contract is different from a completed provisioning
failure. WDH returns its standard HTTP 502 `INTEGRATION_ERROR` for those cases.

## 8. Failure demonstration control

The four-field business request contains no test flags. The separate mock-only
control arms a one-shot element failure:

```text
POST /mock/flowone/v1/demo/fail-next
```

Example:

```json
{
  "element": "SIM_OTA"
}
```

The next activation fails at SIM OTA with `FLOW-405`; all prior successful
elements show `rollback_status: SUCCESS`. The control automatically clears, so
the following activation returns to the normal success scenario.

## 9. Evidence retained by WDH

For the lifetime of the local process, WDH retains:

- WDH activation identifier;
- correlation identifier;
- WDH service status;
- FlowOne request identifier;
- overall FlowOne status and result code;
- per-element operation, profile, code, message, and provisioning result;
- per-element rollback result; and
- start and completion timestamps.

Evidence can be retrieved with:

```text
GET /api/v1/network-activations/{activation_id}
```

Durable memory/Snowflake persistence and UI presentation are intentionally
deferred until Robert has exercised and approved this component behavior.

## 10. Current implementation locations

```text
contracts/flowone.py + contracts/flowone_soap.py
  shared request and response contract

integration_stubs/wsdl/FlowOneProvisioningService.wsdl
  inspectable WSDL contract for SOAP clients and Postman

integration_stubs/main.py
  separate FlowOne mock, decomposition, failure injection, and rollback

app/integrations/flowone.py
  WDH outbound SOAP/XML connector

app/services/provisioning.py
  WDH activation orchestration and evidence

app/api.py + app/api_models.py
  WDH-facing API route and four-field request

tests/test_flowone_provisioning.py
  success, five element failures, rollback, WSDL, SOAP Fault, and validation
```

## 11. Explicitly deferred

- Diameter Gy usage and online-charging interaction after SIM activation;
- usage-event ingestion, rating, reservation, debit, and balance behavior;
- combining the independent Amdocs compatibility call into one activation
  orchestration flow;
- WDH subscription creation and contract-state integration;
- persistence in the repository/Snowflake data plane;
- Operations exception UI;
- browser presentation;
- Postman collection update;
- AWS deployment; and
- authentication beyond the existing local prototype conventions.
