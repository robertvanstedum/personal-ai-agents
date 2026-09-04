from __future__ import annotations

from xml.etree import ElementTree as ET

from contracts.flowone import (
    ElementResult,
    FlowOneActivationRequest,
    FlowOneActivationResponse,
)


SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
FLOWONE_NS = "http://prototype-lab.example/flowone/v1"

ET.register_namespace("soapenv", SOAP_NS)
ET.register_namespace("flow", FLOWONE_NS)


def soap_tag(name: str) -> str:
    return f"{{{SOAP_NS}}}{name}"


def flowone_tag(name: str) -> str:
    return f"{{{FLOWONE_NS}}}{name}"


def required_text(parent: ET.Element, name: str) -> str:
    element = parent.find(flowone_tag(name))
    if element is None or not element.text or not element.text.strip():
        raise ValueError(f"Missing required SOAP element: {name}")
    return element.text.strip()


def optional_text(parent: ET.Element, name: str) -> str | None:
    element = parent.find(flowone_tag(name))
    if element is None or not element.text or not element.text.strip():
        return None
    return element.text.strip()


def build_activation_envelope(request: FlowOneActivationRequest) -> bytes:
    envelope = ET.Element(soap_tag("Envelope"))
    header = ET.SubElement(envelope, soap_tag("Header"))
    ET.SubElement(header, flowone_tag("CorrelationId")).text = request.correlation_id
    body = ET.SubElement(envelope, soap_tag("Body"))
    operation = ET.SubElement(body, flowone_tag("ActivateSubscriberRequest"))
    ET.SubElement(operation, flowone_tag("IMSI")).text = request.imsi
    ET.SubElement(operation, flowone_tag("MSISDN")).text = request.mdn
    ET.SubElement(operation, flowone_tag("ServicePackage")).text = (
        request.service_package
    )
    ET.SubElement(operation, flowone_tag("RoamingPackage")).text = (
        request.roaming_package
    )
    if request.private_apn:
        ET.SubElement(operation, flowone_tag("PrivateAPN")).text = request.private_apn
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def parse_activation_envelope(payload: bytes) -> FlowOneActivationRequest:
    root = ET.fromstring(payload)
    if root.tag != soap_tag("Envelope"):
        raise ValueError("The document root must be a SOAP 1.1 Envelope")
    header = root.find(soap_tag("Header"))
    body = root.find(soap_tag("Body"))
    if header is None or body is None:
        raise ValueError("SOAP Header and Body are required")
    operation = body.find(flowone_tag("ActivateSubscriberRequest"))
    if operation is None:
        raise ValueError("ActivateSubscriberRequest is required")
    return FlowOneActivationRequest(
        correlation_id=required_text(header, "CorrelationId"),
        imsi=required_text(operation, "IMSI"),
        mdn=required_text(operation, "MSISDN"),
        service_package=required_text(operation, "ServicePackage"),
        roaming_package=required_text(operation, "RoamingPackage"),
        private_apn=optional_text(operation, "PrivateAPN"),
    )


def build_activation_response_envelope(response: FlowOneActivationResponse) -> bytes:
    envelope = ET.Element(soap_tag("Envelope"))
    body = ET.SubElement(envelope, soap_tag("Body"))
    operation = ET.SubElement(body, flowone_tag("ActivateSubscriberResponse"))
    values = {
        "FlowOneRequestId": response.flowone_request_id,
        "CorrelationId": response.correlation_id,
        "OverallStatus": response.overall_status,
        "ResultCode": response.result_code,
        "Message": response.message,
        "IMSI": response.imsi,
        "MSISDN": response.mdn,
        "ServicePackage": response.service_package,
        "RoamingPackage": response.roaming_package,
        "RollbackStatus": response.rollback_status,
        "StartedAt": response.started_at,
        "CompletedAt": response.completed_at,
    }
    for name, value in values.items():
        ET.SubElement(operation, flowone_tag(name)).text = value
    results = ET.SubElement(operation, flowone_tag("ElementResults"))
    for result in response.element_results:
        row = ET.SubElement(results, flowone_tag("ElementResult"))
        row_values = {
            "Element": result.element,
            "Operation": result.operation,
            "ProvisioningStatus": result.provisioning_status,
            "ElementCode": result.element_code,
            "Message": result.message,
            "RollbackStatus": result.rollback_status,
            "AppliedProfile": result.applied_profile,
        }
        for name, value in row_values.items():
            child = ET.SubElement(row, flowone_tag(name))
            if value is not None:
                child.text = value
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def parse_activation_response_envelope(payload: bytes) -> FlowOneActivationResponse:
    root = ET.fromstring(payload)
    body = root.find(soap_tag("Body"))
    if body is None:
        raise ValueError("SOAP Body is required")
    fault = body.find(soap_tag("Fault"))
    if fault is not None:
        fault_string = fault.findtext("faultstring") or "Unknown FlowOne SOAP fault"
        raise ValueError(fault_string)
    operation = body.find(flowone_tag("ActivateSubscriberResponse"))
    if operation is None:
        raise ValueError("ActivateSubscriberResponse is required")
    results_parent = operation.find(flowone_tag("ElementResults"))
    if results_parent is None:
        raise ValueError("ElementResults is required")
    element_results = []
    for row in results_parent.findall(flowone_tag("ElementResult")):
        applied = row.find(flowone_tag("AppliedProfile"))
        element_results.append(
            ElementResult(
                element=required_text(row, "Element"),
                operation=required_text(row, "Operation"),
                provisioning_status=required_text(row, "ProvisioningStatus"),
                element_code=required_text(row, "ElementCode"),
                message=required_text(row, "Message"),
                rollback_status=required_text(row, "RollbackStatus"),
                applied_profile=(
                    applied.text.strip()
                    if applied is not None and applied.text
                    else None
                ),
            )
        )
    return FlowOneActivationResponse(
        flowone_request_id=required_text(operation, "FlowOneRequestId"),
        correlation_id=required_text(operation, "CorrelationId"),
        overall_status=required_text(operation, "OverallStatus"),
        result_code=required_text(operation, "ResultCode"),
        message=required_text(operation, "Message"),
        imsi=required_text(operation, "IMSI"),
        mdn=required_text(operation, "MSISDN"),
        service_package=required_text(operation, "ServicePackage"),
        roaming_package=required_text(operation, "RoamingPackage"),
        rollback_status=required_text(operation, "RollbackStatus"),
        element_results=element_results,
        started_at=required_text(operation, "StartedAt"),
        completed_at=required_text(operation, "CompletedAt"),
    )


def build_soap_fault(message: str, fault_code: str = "soapenv:Client") -> bytes:
    envelope = ET.Element(soap_tag("Envelope"))
    body = ET.SubElement(envelope, soap_tag("Body"))
    fault = ET.SubElement(body, soap_tag("Fault"))
    ET.SubElement(fault, "faultcode").text = fault_code
    ET.SubElement(fault, "faultstring").text = message
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
