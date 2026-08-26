"""Basic tests for the Day 1 domain vocabulary and project paths."""

from design_automation.core import DATA_DIR
from design_automation.models import Component, ComponentKind, Connection


def test_component_and_connection_describe_an_interaction() -> None:
    order_service = Component(
        name="Order Service",
        kind=ComponentKind.SERVICE,
        responsibility="Own the order lifecycle and orchestrate refunds.",
    )
    payment_call = Connection(
        source=order_service.name,
        target="Payment Service",
        interaction="Request a partial refund.",
    )

    assert payment_call.source == "Order Service"
    assert payment_call.target == "Payment Service"


def test_expected_data_directories_exist() -> None:
    expected_directories = {
        "requirements",
        "architecture",
        "interfaces",
        "existing_sdds",
    }

    assert {path.name for path in DATA_DIR.iterdir() if path.is_dir()} == expected_directories

