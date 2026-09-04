import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.flowone import FlowOneClient
from app.main import create_app
from app.repositories.memory import MemoryRepository
from integration_stubs.main import FlowOneScenarioState, create_flowone_stub_app


VALID_ACTIVATION = {
    "imsi": "310150123456789",
    "mdn": "+13125550101",
    "service_package": "DATA_SMS",
    "roaming_package": "DOMESTIC",
}


@pytest.fixture
def flowone_system():
    state = FlowOneScenarioState()
    stub_app = create_flowone_stub_app(state)
    gateway = FlowOneClient(
        "http://flowone.test",
        transport=httpx.ASGITransport(app=stub_app),
    )
    wdh_client = TestClient(create_app(MemoryRepository(), gateway))
    stub_client = TestClient(stub_app)
    return wdh_client, stub_client


def test_standard_activation_skips_private_apn_aaa_after_required_elements_succeed(flowone_system):
    wdh_client, _ = flowone_system

    response = wdh_client.post("/api/v1/network-activations", json=VALID_ACTIVATION)

    assert response.status_code == 201
    result = response.json()
    assert result["wdh_service_status"] == "ACTIVE"
    assert result["flowone"]["overall_status"] == "SUCCESS"
    assert result["flowone"]["result_code"] == "FLOW-200"
    assert result["flowone"]["rollback_status"] == "NOT_REQUIRED"
    assert [row["element"] for row in result["flowone"]["element_results"]] == [
        "HSS",
        "POLICY",
        "SMSC",
        "AAA",
    ]
    assert [row["provisioning_status"] for row in result["flowone"]["element_results"]] == [
        "SUCCESS", "SUCCESS", "SUCCESS", "SKIPPED_NOT_APPLICABLE"
    ]
    assert result["flowone"]["element_results"][-1]["element_code"] == "AAA-000"

    evidence = wdh_client.get(
        f"/api/v1/network-activations/{result['activation_id']}"
    )
    assert evidence.status_code == 200
    assert evidence.json() == result


@pytest.mark.parametrize(
    ("failed_element", "failure_code"),
    [
        ("HSS", "FLOW-401"),
        ("POLICY", "FLOW-402"),
        ("SMSC", "FLOW-403"),
        ("AAA", "FLOW-404"),
    ],
)
def test_each_element_can_fail_with_rollback_and_one_black_box_result(
    flowone_system, failed_element, failure_code
):
    wdh_client, stub_client = flowone_system
    armed = stub_client.post(
        "/mock/flowone/v1/demo/fail-next",
        json={"element": failed_element},
    )
    assert armed.status_code == 200

    response = wdh_client.post(
        "/api/v1/network-activations",
        json={**VALID_ACTIVATION, "private_apn": "BOREAL_IOT_PRIVATE"},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["wdh_service_status"] == "ACTIVATION_FAILED"
    assert result["flowone"]["overall_status"] == "FAILURE"
    assert result["flowone"]["result_code"] == "FLOW-400"
    assert result["flowone"]["rollback_status"] == "COMPLETED"

    rows = result["flowone"]["element_results"]
    failed_index = next(
        index for index, row in enumerate(rows) if row["element"] == failed_element
    )
    assert rows[failed_index]["provisioning_status"] == "FAILURE"
    assert rows[failed_index]["element_code"] == failure_code
    assert all(row["rollback_status"] == "SUCCESS" for row in rows[:failed_index])
    assert all(
        row["provisioning_status"] == "NOT_ATTEMPTED"
        for row in rows[failed_index + 1 :]
    )


def test_failure_scenario_is_one_shot(flowone_system):
    wdh_client, stub_client = flowone_system
    stub_client.post(
        "/mock/flowone/v1/demo/fail-next",
        json={"element": "SMSC"},
    )

    first = wdh_client.post("/api/v1/network-activations", json=VALID_ACTIVATION)
    second = wdh_client.post("/api/v1/network-activations", json=VALID_ACTIVATION)

    assert first.json()["wdh_service_status"] == "ACTIVATION_FAILED"
    assert second.json()["wdh_service_status"] == "ACTIVE"


def test_private_apn_activation_executes_aaa_with_account_profile(flowone_system):
    wdh_client, _ = flowone_system
    response = wdh_client.post(
        "/api/v1/network-activations",
        json={**VALID_ACTIVATION, "private_apn": "BOREAL_IOT_PRIVATE"},
    )

    assert response.status_code == 201
    rows = response.json()["flowone"]["element_results"]
    assert all(row["provisioning_status"] == "SUCCESS" for row in rows)
    assert rows[-1]["element"] == "AAA"
    assert rows[-1]["operation"] == "AUTHORIZE_PRIVATE_APN"
    assert rows[-1]["applied_profile"] == "ENT_APN_BOREAL_IOT_PRIVATE"


def test_wdh_rejects_invalid_imsi_before_calling_flowone(flowone_system):
    wdh_client, _ = flowone_system
    payload = {**VALID_ACTIVATION, "imsi": "123"}

    response = wdh_client.post("/api/v1/network-activations", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"


def test_flowone_publishes_wsdl_and_accepts_only_soap_for_activation(flowone_system):
    _, stub_client = flowone_system

    wsdl = stub_client.get(
        "/mock/flowone/v1/FlowOneProvisioningService?wsdl"
    )
    old_json_route = stub_client.post(
        "/mock/flowone/v1/activations",
        json={"obsolete": True},
    )

    assert wsdl.status_code == 200
    assert "FlowOneProvisioningService" in wsdl.text
    assert "ActivateSubscriber" in wsdl.text
    assert "PrivateAPN" in wsdl.text
    assert old_json_route.status_code == 404


def test_malformed_flowone_soap_returns_a_soap_fault(flowone_system):
    _, stub_client = flowone_system

    response = stub_client.post(
        "/mock/flowone/v1/FlowOneProvisioningService",
        content=b"<not-soap/>",
        headers={"Content-Type": "text/xml", "SOAPAction": "ActivateSubscriber"},
    )

    assert response.status_code == 500
    assert "Fault" in response.text
    assert "SOAP 1.1 Envelope" in response.text
