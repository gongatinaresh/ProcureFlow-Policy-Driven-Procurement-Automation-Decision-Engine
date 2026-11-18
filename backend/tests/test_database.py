from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Item, Plant, PRLine, Supplier


def seed_reference_rows(session: Session) -> None:
    session.add_all(
        [
            Item(
                item_code="ITM-TEST",
                description="Test item",
                uom="NOS",
                category="Testing",
                item_type="Material",
                standard_price=Decimal("12.50"),
            ),
            Supplier(
                supplier_id="S-TEST",
                name="Test supplier",
                city="Pune",
                categories=["Testing"],
                status="Active",
                on_time_pct=Decimal("95.00"),
                quality_rating=Decimal("4.50"),
            ),
            Plant(plant_code="PLANT-TEST", name="Test plant"),
        ]
    )
    session.commit()


def test_database_initialization_and_foreign_key_operations(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        seed_reference_rows(session)
        item = session.get(Item, "ITM-TEST")
        supplier = session.get(Supplier, "S-TEST")
        plant = session.get(Plant, "PLANT-TEST")

        assert item is not None
        assert item.standard_price == Decimal("12.50")
        assert supplier is not None and supplier.categories == ["Testing"]
        assert plant is not None

        line = PRLine(
            pr_no="PR-TEST-1",
            pr_line=1,
            created_at_source=datetime(2026, 11, 17, 10, tzinfo=timezone.utc),
            requester="Test requester",
            department="Testing",
            plant_code=plant.plant_code,
            matched_plant_code=plant.plant_code,
            supplied_item_code=item.item_code,
            description=item.description,
            resolved_item_code=item.item_code,
            quantity=Decimal("2"),
            uom=item.uom,
            estimated_unit_price=Decimal("12.50"),
            need_by_date=date(2026, 11, 25),
            urgency="Normal",
        )
        session.add(line)
        session.commit()
        session.refresh(line)

        assert line.id is not None
        assert line.resolved_item_code == item.item_code
        assert line.matched_plant_code == plant.plant_code


def test_pr_line_identity_is_unique(
    test_session_factory: sessionmaker[Session],
) -> None:
    with test_session_factory() as session:
        seed_reference_rows(session)
        values = {
            "pr_no": "PR-TEST-DUP",
            "pr_line": 1,
            "created_at_source": datetime(2026, 11, 17, 10, tzinfo=timezone.utc),
            "requester": "Test requester",
            "department": "Testing",
            "plant_code": "PLANT-TEST",
            "matched_plant_code": "PLANT-TEST",
            "supplied_item_code": "ITM-TEST",
            "description": "Test item",
            "resolved_item_code": "ITM-TEST",
            "quantity": Decimal("1"),
            "uom": "NOS",
            "estimated_unit_price": Decimal("12.50"),
            "need_by_date": date(2026, 11, 25),
            "urgency": "Normal",
        }
        session.add(PRLine(**values))
        session.commit()
        session.add(PRLine(**values))

        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
