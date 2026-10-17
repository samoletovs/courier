"""Security invariants for the infrastructure template."""

from pathlib import Path


INFRASTRUCTURE = Path(__file__).parents[2] / "infrastructure" / "main.bicep"


def test_recipient_allowlist_is_securely_provided_from_key_vault():
    template = INFRASTRUCTURE.read_text(encoding="utf-8")

    assert "@secure()\nparam allowedRecipients string" in template
    assert "resource allowedRecipientsSecret 'Microsoft.KeyVault/vaults/secrets@" in template
    assert "value: allowedRecipients" in template
    assert (
        "value: '@Microsoft.KeyVault(VaultName=${keyVault.name};"
        "SecretName=${allowedRecipientsSecret.name})'"
    ) in template


def test_function_uses_managed_identity_with_least_privilege_for_key_vault():
    template = INFRASTRUCTURE.read_text(encoding="utf-8")

    assert "enableRbacAuthorization: true" in template
    assert "keyVaultReferenceIdentity: uami.id" in template
    assert "keyVaultSecretsUserRoleId = subscriptionResourceId(" in template
    assert "'4633458b-17de-408a-b874-0445c86b69e6'" in template
    assert "name: guid(keyVault.id, uami.id, keyVaultSecretsUserRoleId)" in template
    assert "roleDefinitionId: keyVaultSecretsUserRoleId" in template
