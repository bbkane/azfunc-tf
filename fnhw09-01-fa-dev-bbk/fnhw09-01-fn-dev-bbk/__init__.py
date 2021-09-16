import json
import itertools
import logging
import os
import random
import string
import sys
import traceback
import typing as ty

from azure.identity import DefaultAzureCredential
from azure.identity import ManagedIdentityCredential
from azure.mgmt.keyvault import KeyVaultManagementClient
from azure.mgmt.keyvault.models import VaultCreateOrUpdateParameters
from azure.mgmt.keyvault.models import VaultProperties
from azure.mgmt.keyvault.models import Vault
from azure.mgmt.keyvault.models import Sku
from azure.mgmt.keyvault.models import Permissions
from azure.mgmt.keyvault.models import AccessPolicyEntry

import azure.functions as func

Numeric = ty.Union[int, float]

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class ConcertAzureFuncError(Exception):
    """Used to map errors onto log messages and HTTP results

    - ``message`` - explanation of the error
    - ``status_code`` - HTTP status code this should map onto
    - ``data`` -- other fields to be mapped into a JSON log message as well as an HTTP response. Let's try to keep it one level, so a simple dict
    - ``debug_data`` -- fields not shown to the user but logged. Keys should not conflict with data
    """

    def __init__(
        self,
        *,
        message: str,
        status_code: int,
        data: ty.Dict[str, ty.Union[str, Numeric]] = None,
        debug_data: ty.Union[str, Numeric] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.data = data or dict()
        self.debug_data = debug_data or dict()


def create_keyvault(
    *,
    credential: ty.Union[ManagedIdentityCredential, DefaultAzureCredential],
    subscription_id: str,
    tenant_id: str,
    resource_group_name: str,
    location: str,
    keyvault_name: str,
    owning_group_object_id: str,
) -> Vault:
    keyvault_client = KeyVaultManagementClient(
        credential,
        subscription_id,
        logging_enable=False,
    )

    # https://github.com/MicrosoftDocs/python-sdk-docs-examples/blob/main/key_vault/provision_key_vault.py

    # KV name restrictions: https://docs.microsoft.com/en-us/azure/key-vault/secrets/quick-create-cli#create-a-key-vault
    invalid_chars = tuple(
        c
        for c in keyvault_name
        if c not in itertools.chain(string.digits, string.ascii_lowercase, ("-",))
    )
    if len(invalid_chars) != 0:
        raise ConcertAzureFuncError(
            message="keyvault_name contains invalid chars",
            status_code=422,  # HTTP Unprocessable Entity : https://stackoverflow.com/a/9132152/2958070
            data={"keyvault_name": keyvault_name, "invalid_chars": repr(invalid_chars)},
        )

    if len(keyvault_name) < 3 or len(keyvault_name) > 24:
        raise ConcertAzureFuncError(
            message="keyvault_name should be between 3 and 24 characters",
            status_code=422,
            data={"keyvault_name": keyvault_name, "length": len(keyvault_name)},
        )

    availability_result = keyvault_client.vaults.check_name_availability({"name": keyvault_name})
    if not availability_result.name_available:
        raise ConcertAzureFuncError(
            message="keyvault name not available (maybe previously claimed?)",
            status_code=409,
            data={"keyvault_name": keyvault_name},
            debug_data=dict(),
        )

    # https://docs.microsoft.com/en-us/python/api/azure-mgmt-keyvault/azure.mgmt.keyvault.v2021_04_01_preview.operations.vaultsoperations?view=azure-python#begin-create-or-update-resource-group-name--vault-name--parameters----kwargs-
    poller = keyvault_client.vaults.begin_create_or_update(
        resource_group_name,
        keyvault_name,
        VaultCreateOrUpdateParameters(
            location=location,
            properties=VaultProperties(
                tenant_id=tenant_id,
                sku=Sku(
                    name="standard",
                    family="A",
                ),
                access_policies=[
                    # let's leave this until we're actually adding users
                    AccessPolicyEntry(
                        tenant_id=tenant_id,
                        # https://portal.azure.com/#blade/Microsoft_AAD_IAM/GroupDetailsMenuBlade/Overview/groupId/5779e176-8600-472f-b067-620c2ab92d15
                        # concert-user01-sgp
                        # object_id="5779e176-8600-472f-b067-620c2ab92d15",
                        object_id=owning_group_object_id,
                        permissions=Permissions(
                            keys=["all"],
                            secrets=["all"],
                        ),
                    ),
                ],
            ),
        ),
    )

    # https://docs.microsoft.com/en-us/python/api/azure-core/azure.core.polling.lropoller?view=azure-python#result-timeout-none-
    keyvault = poller.result()
    return keyvault


def main(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:

    try:
        # https://stackoverflow.com/a/64523180/2958070
        invocation_id = context.invocation_id

        owning_group_object_id = req.params.get("owning_group_object_id", None)
        if owning_group_object_id is None:
            raise ConcertAzureFuncError(
                message="missing required URL parameter",
                status_code=422,
                data={"missing_parameter": "owning_group_object_id"},
            )

        keyvault_name = req.params.get("keyvault_name", None)
        if keyvault_name is None:
            raise ConcertAzureFuncError(
                message="missing required URL parameter",
                status_code=422,
                data={"missing_parameter": "keyvault_name"},
            )

        # https://docs.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential?view=azure-python
        # I really just wanna log in via managed service or, failing that, the CLI
        credential = DefaultAzureCredential(
            exclude_shared_token_cache_credential=True,
            exclude_visual_studio_code_credential=True,
        )

        # https://docs.microsoft.com/en-us/python/api/azure-identity/azure.identity.managedidentitycredential?view=azure-python
        # credential = ManagedIdentityCredential(logging_enable=True)
        logger.info(f"credential = {credential!r}")

        # https://docs.microsoft.com/en-us/azure/azure-functions/functions-how-to-use-azure-function-app-settings?tabs=portal
        # Where do we want to create these key vaults
        KV_CREATION_AZURE_SUBSCRIPTION_ID = os.environ["KV_CREATION_AZURE_SUBSCRIPTION_ID"]
        KV_CREATION_AZURE_TENANT_ID = os.environ["KV_CREATION_AZURE_TENANT_ID"]
        KV_CREATION_RESOURCE_GROUP_NAME = os.environ["KV_CREATION_RESOURCE_GROUP_NAME"]
        KV_CREATION_LOCATION = os.environ["KV_CREATION_LOCATION"]

        # keyvault_name = f"cncrt-{random.randint(0,10000):05}-kv"
        keyvault = create_keyvault(
            credential=credential,
            subscription_id=KV_CREATION_AZURE_SUBSCRIPTION_ID,
            tenant_id=KV_CREATION_AZURE_TENANT_ID,
            resource_group_name=KV_CREATION_RESOURCE_GROUP_NAME,
            location=KV_CREATION_LOCATION,
            keyvault_name=keyvault_name,
            owning_group_object_id=owning_group_object_id,
        )

        debug_data = {
            "message": "keyvault created",
            "keyvault_name": keyvault_name,
            "keyvault": repr(keyvault),
        }
        logger.info(json.dumps(debug_data))
        ret_data = {
            "invocation_id": invocation_id,
            "message": "keyvault created",
            "keyvault_name": keyvault_name,
            "keyvault": keyvault.as_dict(),
        }
        return func.HttpResponse(
            body=json.dumps(ret_data),
            status_code=201,
            mimetype="application/json",
        )

    except ConcertAzureFuncError as e:

        log_data = {
            "message": e.message,
            "status_code": e.status_code,
            **e.data,
            **e.debug_data,
        }
        logger.error(json.dumps(log_data))
        ret_data = {"message": e.message, "invocation_id": invocation_id, **e.data}
        return func.HttpResponse(
            body=json.dumps(ret_data),
            status_code=e.status_code,
            mimetype="application/json",
        )
    except Exception:
        # I'm in a bit of quandery because I need all this information
        # It's nicely formatted to the logs if this exception isn't handled
        # but I also need to `return` something to the user and give them
        # the invocation_id so I can find this again
        # So here we go manually formatting exceptions...
        exc_type, exc_value, exc_traceback = sys.exc_info()
        debug_data = {
            # TODO: use e.message in log?
            "exc_type": repr(exc_type),
            "exc_value": repr(exc_value),
            "exc_traceback": "".join(
                traceback.format_exception(exc_type, exc_value, exc_traceback)
            ),
        }
        logger.error(json.dumps(debug_data))
        ret_data = {"message": "unexpected error", "invocation_id": invocation_id}
        return func.HttpResponse(
            body=json.dumps(ret_data),
            status_code=500,
            mimetype="application/json",
        )
