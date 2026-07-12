"""Tests for the vault Credential SQLModel."""

from sqlalchemy import Index, LargeBinary


def test_credential_model_is_registered() -> None:
    from cubeplex.models import Credential

    assert Credential.__tablename__ == "credentials"
    assert Credential.__table__.c.value_encrypted.type.__class__ is LargeBinary
    assert Credential.__table__.c.cred_metadata.type.python_type is dict


def test_credential_model_has_partial_unique_indexes() -> None:
    """Org-scoped and system-scoped uniqueness are enforced by partial indexes.

    System credentials carry ``org_id IS NULL``, so a single full unique
    constraint cannot cover both system and org-scoped rows -- two partial
    indexes do.
    """
    from cubeplex.models import Credential

    indexes: list[Index] = list(Credential.__table__.indexes)

    org_scoped = next(
        (i for i in indexes if i.name == "uq_credential_org_kind_name"),
        None,
    )
    system_scoped = next(
        (i for i in indexes if i.name == "uq_credential_system_kind_name"),
        None,
    )
    assert org_scoped is not None and org_scoped.unique
    assert [c.name for c in org_scoped.columns] == ["org_id", "kind", "name"]
    assert system_scoped is not None and system_scoped.unique
    assert [c.name for c in system_scoped.columns] == ["kind", "name"]
