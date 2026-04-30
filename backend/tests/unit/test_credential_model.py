"""Tests for the vault Credential SQLModel."""

from sqlalchemy import LargeBinary, UniqueConstraint


def test_credential_model_is_registered() -> None:
    from cubebox.models import Credential

    assert Credential.__tablename__ == "credentials"
    assert Credential.__table__.c.value_encrypted.type.__class__ is LargeBinary
    assert Credential.__table__.c.cred_metadata.type.python_type is dict


def test_credential_model_has_org_kind_name_unique_constraint() -> None:
    from cubebox.models import Credential

    constraints = [
        constraint
        for constraint in Credential.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]

    assert any(
        constraint.name == "uq_credential_org_kind_name"
        and [column.name for column in constraint.columns] == ["org_id", "kind", "name"]
        for constraint in constraints
    )
