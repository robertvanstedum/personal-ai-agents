from __future__ import annotations

from copy import deepcopy
from decimal import Decimal


RATE_PLANS: dict[str, dict] = {
    "PLAN-IOT-001": {
        "rate_plan_id": "PLAN-IOT-001",
        "product_offering_id": "OFFER-IOT-CONNECTIVITY",
        "rate_plan_code": "RP1",
        "name": "Connected Device Essential",
        "monthly_price": Decimal("2.00"),
        "gl_code": "4100-IOT-RP1",
        "currency": "USD",
        "status": "ACTIVE",
    },
    "PLAN-IOT-002": {
        "rate_plan_id": "PLAN-IOT-002",
        "product_offering_id": "OFFER-IOT-CONNECTIVITY",
        "rate_plan_code": "RP2",
        "name": "Connected Device Enhanced",
        "monthly_price": Decimal("3.00"),
        "gl_code": "4100-IOT-RP2",
        "currency": "USD",
        "status": "ACTIVE",
    },
    "PLAN-IOT-003": {
        "rate_plan_id": "PLAN-IOT-003",
        "product_offering_id": "OFFER-IOT-CONNECTIVITY",
        "rate_plan_code": "RP3",
        "name": "Connected Device Premium",
        "monthly_price": Decimal("5.00"),
        "gl_code": "4100-IOT-RP3",
        "currency": "USD",
        "status": "ACTIVE",
    },
    "PLAN-IOT-004": {
        "rate_plan_id": "PLAN-IOT-004",
        "product_offering_id": "OFFER-IOT-CONNECTIVITY",
        "rate_plan_code": "RP4",
        "name": "Critical Asset Managed",
        "monthly_price": Decimal("8.00"),
        "gl_code": "4100-IOT-RP4",
        "currency": "USD",
        "status": "ACTIVE",
    },
    "PLAN-VAS-NETFLIX-PREMIUM": {
        "rate_plan_id": "PLAN-VAS-NETFLIX-PREMIUM",
        "product_offering_id": "OFFER-NETFLIX-PREMIUM",
        "rate_plan_code": "VAS-NFX-PREM",
        "name": "Netflix Premium Monthly",
        "monthly_price": Decimal("22.99"),
        "gl_code": "4110-VAS-NETFLIX",
        "currency": "USD",
        "status": "ACTIVE",
    },
    "PLAN-IOT-SHARED-100GB": {
        "rate_plan_id": "PLAN-IOT-SHARED-100GB",
        "product_offering_id": "OFFER-SHARED-DATA-POOL",
        "rate_plan_code": "POOL-100GB",
        "name": "Enterprise Shared Data Pool — 100 GB",
        "monthly_price": Decimal("100.00"),
        "gl_code": "4120-IOT-DATA-POOL",
        "currency": "USD",
        "status": "ACTIVE",
    },
}

PRODUCT_OFFERINGS: dict[str, dict] = {
    "OFFER-IOT-CONNECTIVITY": {
        "product_offering_id": "OFFER-IOT-CONNECTIVITY",
        "offering_code": "IOT-CONNECT",
        "name": "Managed IoT Connectivity",
        "fulfillment_type": "FLOWONE_NETWORK_ACTIVATION",
        "status": "ACTIVE",
    },
    "OFFER-NETFLIX-PREMIUM": {
        "product_offering_id": "OFFER-NETFLIX-PREMIUM",
        "offering_code": "VAS-NETFLIX-PREMIUM",
        "name": "Netflix Premium Add-on",
        "fulfillment_type": "PARTNER_FULFILLMENT",
        "status": "ACTIVE",
    },
    "OFFER-SHARED-DATA-POOL": {
        "product_offering_id": "OFFER-SHARED-DATA-POOL",
        "offering_code": "IOT-SHARED-DATA",
        "name": "Enterprise Shared Data Pool",
        "fulfillment_type": "ACCOUNT_CONFIGURATION",
        "status": "ACTIVE",
    },
}

NETWORK_PROFILES: dict[str, dict] = {
    "NET-DATA-SMS-DOM": {
        "technical_profile_id": "NET-DATA-SMS-DOM",
        "profile_code": "DATA_SMS_DOMESTIC",
        "name": "Data and SMS — Domestic Roaming",
        "service_package": "DATA_SMS",
        "roaming_package": "DOMESTIC",
        "status": "ACTIVE",
    },
    "NET-DATA-HOME": {
        "technical_profile_id": "NET-DATA-HOME",
        "profile_code": "DATA_HOME_ONLY",
        "name": "Data Only — Home Network",
        "service_package": "DATA_ONLY",
        "roaming_package": "HOME_ONLY",
        "status": "ACTIVE",
    },
}

OFFERING_RESOURCE_REQUIREMENTS: list[dict] = [
    {
        "requirement_id": "REQ-IOT-SIM",
        "product_offering_id": "OFFER-IOT-CONNECTIVITY",
        "resource_type": "SIM",
        "required": True,
        "allocation_method": "CUSTOMER_SELECTED",
    },
    {
        "requirement_id": "REQ-IOT-MDN",
        "product_offering_id": "OFFER-IOT-CONNECTIVITY",
        "resource_type": "MDN",
        "required": True,
        "allocation_method": "NEXT_AVAILABLE",
    },
]

DETAILED_ACCOUNT_CHARGES = [
    {
        "charge_code": "ACCOUNT_SETUP",
        "description": "Enterprise account setup",
        "charge_type": "ONE_TIME",
        "amount": Decimal("50.00"),
        "gl_code": "4200-IOT-SETUP",
    }
]

SUMMARIZED_ACCOUNT_CHARGES = [
    {
        "charge_code": "PLATFORM_ENABLEMENT",
        "description": "Wholesale platform enablement",
        "charge_type": "ONE_TIME",
        "amount": Decimal("100.00"),
        "gl_code": "4200-IOT-ENABLE",
    },
    {
        "charge_code": "DATA_EXCHANGE",
        "description": "Billing data exchange",
        "charge_type": "RECURRING",
        "amount": Decimal("25.00"),
        "gl_code": "4200-IOT-DATA",
    },
    {
        "charge_code": "MANAGED_OPERATIONS",
        "description": "Managed operations support",
        "charge_type": "RECURRING",
        "amount": Decimal("50.00"),
        "gl_code": "4200-IOT-OPS",
    },
]


def public_rate_plans() -> list[dict]:
    rows = []
    for plan in RATE_PLANS.values():
        row = deepcopy(plan)
        row["monthly_price"] = f"{row['monthly_price']:.2f}"
        rows.append(row)
    return rows


def public_product_offerings() -> list[dict]:
    return [deepcopy(row) for row in PRODUCT_OFFERINGS.values()]


def public_network_profiles() -> list[dict]:
    return [deepcopy(row) for row in NETWORK_PROFILES.values()]
