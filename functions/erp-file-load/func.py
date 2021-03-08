# Copyright (c) 2021, Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.



import logging
import io
import json
import base64
from fdk import response
import oci.object_storage
import requests

JSON_CONTENT_TYPE = "application/json"

class FA_REST_Exception(Exception):
    def __init__(self, message):
        self.message = message

def handler(ctx, data: io.BytesIO = None):
    logging.info("------------------------------------------------------------------------------")
    logging.info("Within load-file-erp function")
    logging.info("------------------------------------------------------------------------------")

    signer = oci.auth.signers.get_resource_principals_signer()
    object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    namespace = object_storage_client.get_namespace().data

    # Get Configuration Parameters
    cfg = ctx.Config()
    try:
        param_inbound_bucket_name = cfg["zip_inbound_bucket_name"]
        param_processing_bucket_name = cfg["processing_bucket_name"]
        param_erp_url = cfg["erp_url"]
        param_erp_username = cfg["erp_username"]
        param_oci_password_vault_ocid = cfg["erp_password_vault_ocid"]

        param_fa_jobname = cfg['erp_jobname']
        param_fa_paramlist = cfg['erp_paramlist']
        param_fa_callback_url = cfg['erp_callback_url']

        param_ons_error_topic_ocid = cfg["ons_error_topic_ocid"]
        param_ons_info_topic_ocid = cfg["ons_info_topic_ocid"]
    except KeyError as ke:
        message = f'Mandatory Configuration Parameter {ke} missing, please check all configuration parameters'
        return return_fn_error(ctx, response, message)

    # Check we've received the right type of event
    body = json.loads(data.getvalue())
    logging.info("---------------------------------------------------------")
    logging.info(f'Contents of event body is {str(body)}')
    logging.info("---------------------------------------------------------")

    if body["eventType"] != "com.oraclecloud.objectstorage.createobject":
        message = send_notification(
            ons_topic_id=param_ons_error_topic_ocid,
            title="Event Type Error",
            message="Function called with incorrect event type, eventType should be com.oraclecloud.objectstorage.createobject",
            status="ERROR",
            additional_details=body)

        return return_fn_error(ctx, response, message)

    data_file_name = body['data']['resourceName']
    logging.info(f'Data File = {data_file_name}')

    # Read Object
    try:
        data_file = object_storage_client.get_object(namespace, param_inbound_bucket_name, data_file_name)
    except oci.exceptions.ServiceError as ex:
        message = send_notification(
            ons_topic_id=param_ons_error_topic_ocid,
            title="File Read Error",
            message="Failed to load file from OCI storage",
            status="ERROR",
            additional_details=data_file_name
        )
        logging.info(message)
        return return_fn_error(ctx, response, message)
    logging.info(f'Success: File {data_file_name} was retrieved')


    # GET FA details  from OCI Vault
    try:
        logging.info(f"oci vaultID={param_oci_password_vault_ocid}")
        param_erp_password = read_secret_value(signer, param_oci_password_vault_ocid)
    except oci.exceptions.ServiceError as ex:
        if ex is None:
            ex = "NoError"
        logging.critical("Error getting erp password")
        additional_details = {"vaultOCID": param_oci_password_vault_ocid,
                              "error": str(ex)
                              }
        message = send_notification(
            ons_topic_id=param_ons_error_topic_ocid,
            title="FA Password Error",
            message="Error obtaining Fusion FA Password from OCI Vault",
            status="ERROR",
            additional_details=additional_details)
        return return_fn_error(ctx, response, message)

    param_erp_auth = (param_erp_username, param_erp_password)

    base64_encoded_file = base64.b64encode(data_file.data.content).decode('UTF8')

    saas_result = erpimport_bulk_data(param_erp_url, param_erp_auth, base64_encoded_file, data_file_name,
                                      param_fa_jobname,
                                      param_fa_paramlist, param_fa_callback_url)

    erp_job_id = saas_result["ReqstId"]
    logging.info(f'ERP Job number {erp_job_id} submitted')

    # Copy object to processing bucket, renaming file as we go
    put_object_response = object_storage_client.put_object(namespace, param_processing_bucket_name,
                                                           data_file_name + "_ERPJOBID_" + erp_job_id,
                                                           data_file.data.content)
    logging.info(f'Response of put file to destination bucket {put_object_response.status}')
    if put_object_response.status == 200:
        # If all good then delete original object
        delete_result = object_storage_client.delete_object(namespace, param_inbound_bucket_name, data_file_name)
        if delete_result.status != 204:
            # Error moving files is more of a warning than error....
            message = f'Warning : Error deleting file {data_file_name} from {param_inbound_bucket_name}'
            logging.info(message)
            additional_details = {
                "filename": data_file_name,
                "sourceBucket": param_inbound_bucket_name,
                "destinationBucket": param_processing_bucket_name
            }
            send_notification(
                ons_topic_id=param_ons_info_topic_ocid,
                title="Error moving data file in OCI",
                message=message,
                status="WARNING",
                additional_details=additional_details)
    else:
        # Error moving files is more of a warning than error....
        message = f'Warning Unable to copy  {data_file_name} from {param_inbound_bucket_name} to {param_processing_bucket_name} bucket, leaving original file'
        additional_details = {
            "filename": data_file_name,
            "sourceBucket": param_inbound_bucket_name,
            "destinationBucket": param_processing_bucket_name
        }
        send_notification(
            ons_topic_id=param_ons_info_topic_ocid,
            title="Error moving data file in OCI",
            message=message,
            status="WARNING",
            additional_details=additional_details)
        logging.info(message)
    logging.debug(f' Result from SaaS ')
    logging.debug(saas_result)

    # Publish successful load message to info topic
    additional_details = {"filename": data_file_name,
                          "erpJobId": erp_job_id,
                          "saasResponse": saas_result
                          }

    message = send_notification(
        ons_topic_id=param_ons_info_topic_ocid,
        title="Successfully Loaded Data to SaaS",
        message="Successfully Loaded Datafile to ERP",
        status="INFO",
        additional_details=additional_details)

    # Return result from SaaS as response
    return response.Response(
        ctx,
        response_data=json.dumps(message),
        headers={"Content-Type": JSON_CONTENT_TYPE}
    )


def erpimport_bulk_data(param_erp_url, param_erp_auth, base64_encoded_file, data_file_name, jobname, paramlist,
                        param_fa_callback_url):
    # Send file to ERP

    erp_payload = {
        "OperationName": "importBulkData",
        "DocumentContent": base64_encoded_file,
        "ContentType": "zip",
        "FileName": data_file_name,
        "JobName": jobname,
        "ParameterList": paramlist,
        "CallbackURL": param_fa_callback_url,
        "NotificationCode": "10"
    }
    logging.info(f'Sending file to erp with payload {erp_payload}')
    result = requests.post(
        url=param_erp_url,
        auth=param_erp_auth,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        json=erp_payload
    )

    if result.status_code != 201:
        message = "Error " + str(result.status_code) + " occurred during upload. Message=" + str(result.content)
        raise FA_REST_Exception("Error " + message)

    # Return result for future processing
    return result.json()


def return_fn_error(ctx, fn_response, message, additional_data="None"):
    logging.critical(message)
    # Return Error

    return fn_response.Response(
        ctx, response_data=json.dumps(
            {
                "errorMessage": message,
                "additionalData": additional_data
            }),
        headers={"Content-Type": JSON_CONTENT_TYPE}
    )


def read_secret_value(signer, secret_id):

    secret_client = oci.secrets.SecretsClient(config={}, signer=signer)
    secret_response = secret_client.get_secret_bundle(secret_id)
    base64_secret_content = secret_response.data.secret_bundle_content.content
    base64_secret_bytes = base64_secret_content.encode('ascii')
    base64_message_bytes = base64.b64decode(base64_secret_bytes)
    secret_content = base64_message_bytes.decode('ascii')
    return secret_content


#
# Helper functions
#
def publish_ons_notification(topic_id, msg_title, msg_body):
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        logging.info("Publish notification, topic id" + topic_id)
        client = oci.ons.NotificationDataPlaneClient({}, signer=signer)
        msg = oci.ons.models.MessageDetails(title=msg_title, body=msg_body)
        client.publish_message(topic_id, msg)
    except oci.exceptions.ServiceError as serr:
        logging.critical(f'Exception sending notification {0} to OCI, is the OCID of the notification correct? {serr}')
    except Exception as err:
        logging.critical(f'Unknown exception occurred when sending notification, please see log {err}')


def send_notification(ons_topic_id, title, message, status, additional_details) -> object:
    """

    :rtype: object
    """
    message = {"status": status,
               "header": title,
               "message": message,
               "additionalDetails": additional_details}
    publish_ons_notification(ons_topic_id, title, str(message))
    return message
