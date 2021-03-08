# Copyright (c) 2021, Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.


import logging
import io
import json

import oci.object_storage
from fdk import response
import erp_data_file


def handler(ctx, data: io.BytesIO = None):
    logging.info("------------------------------------------------------------------------------")
    logging.info("Within erp-transform-file")
    logging.info("------------------------------------------------------------------------------")

    oci_signer = oci.auth.signers.get_resource_principals_signer()
    object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=oci_signer)
    namespace = object_storage_client.get_namespace().data

    # Get Configuration Parameters
    cfg = ctx.Config()

    try:
        param_json_inbound_bucket_name = cfg['json_inbound_bucket_name']
        param_zip_inbound_bucket_name = cfg["zip_inbound_bucket_name"]

        param_ons_error_topic_ocid = cfg["ons_error_topic_ocid"]
        param_ons_info_topic_ocid = cfg["ons_info_topic_ocid"]

    except KeyError as ke:
        message = f'Mandatory Configuration Parameter {ke} missing, please check all configuration parameters'
        return return_fn_error(ctx, response, message)

    try:
        body = json.loads(data.getvalue())
    except json.decoder.JSONDecodeError as ex:

        message = send_notification(ons_topic_id=param_ons_error_topic_ocid,
                                    title="JSON Exception",
                                    message="JSON Exception Parsing input data file",
                                    status="ERROR",
                                    additional_details=str(ex)
                                    )

        return return_fn_error(ctx, response, message)

    if body["eventType"] != "com.oraclecloud.objectstorage.createobject":
        message = f'eventType not of com.oraclecloud.objectstorage.createobject aborting'

        message = send_notification(ons_topic_id=param_ons_info_topic_ocid,
                                    title="Incorrect Event",
                                    message="Incorrect EventType Received",
                                    status="ERROR",
                                    additional_details=body)
        return return_fn_error(ctx, response, message)

    json_datafile_name = body['data']['resourceName']
    logging.info(f'Data File received = {json_datafile_name}')

    # Read datafile from OCI
    json_data_file = object_storage_client.get_object(namespace, param_json_inbound_bucket_name, json_datafile_name)
    if json_data_file.status != 200:
        msg = f'Unable to read Data File [{json_datafile_name} from bucket [{param_json_inbound_bucket_name}'
        additional_details = {"jsonDataFilename": json_datafile_name}
        message = send_notification(
            ons_topic_id=param_ons_info_topic_ocid,
            title="Data File Read Error",
            message=msg,
            status="ERROR",
            additional_details=additional_details)

        return return_fn_error(ctx, response, message, json.dumps(additional_details))
    try:
        json_data = json.loads(json_data_file.data.content.decode('UTF8'))
    except json.decoder.JSONDecodeError as ex:

        additional_details={
                            "jsonDecodeError": str(ex),
                            "filename" : json_datafile_name
                            }
        message = send_notification(ons_topic_id=param_ons_error_topic_ocid,
                                    title="JSON Decode Exception",
                                    message="JSON Decode Exception Parsing input data file, please check the file",
                                    status="ERROR",
                                    additional_details=additional_details
                                    )
        return return_fn_error(ctx, response, message)
    # Write result to /tmp
    transformed_data_file = "/tmp/ " + json_datafile_name
    erp_data_file.create_erp_invoices_datafiles(json_data, transformed_data_file)

    # Write resulting object to json_inbound_bucket_name, no change extension , enroute
    with open(transformed_data_file, 'rb') as f:
        oci_response = object_storage_client.put_object(namespace, param_zip_inbound_bucket_name,
                                                        json_datafile_name.replace('.json', '.zip'), f)
        if oci_response.status != 200:
            message = f'Error loading file into OCI bucket  {json_datafile_name}'
            additional_details = {"jsonDataFilename": json_datafile_name}

            message = send_notification(
                ons_topic_id=param_ons_info_topic_ocid,
                title="Data Bucket LoadError",
                message="Received error whilst writing file to OCI bucket",
                status="ERROR",
                additional_details=additional_details)

            return return_fn_error(ctx, response, message, json.dumps(additional_details))

    # Now delete file as its been processed
    if object_storage_client.delete_object(namespace, param_json_inbound_bucket_name, json_datafile_name).status != 204:
        message_details = f'Error deleting processed file {json_datafile_name} into OCI bucket '
        additional_details = {"jsonDataFilename": json_datafile_name}
        message = send_notification(
            ons_topic_id=param_ons_error_topic_ocid,
            title="Failed to Delete Tranform file",
            message=message_details,
            status="ERROR",
            additional_details=additional_details)
        return return_fn_error(ctx, response, message, json.dumps(additional_details))

    # Publish Success Message
    ons_body = {"message": "ERP Transform of file " + json_datafile_name + " completed",
                "filename": json_datafile_name}
    additional_details = ""
    message = send_notification(
        ons_topic_id=param_ons_info_topic_ocid,
        title=f'Transform of file {json_datafile_name} Completed ',
        message=ons_body,
        status="INFO",
        additional_details=additional_details)

    return response.Response(
        ctx, response_data=json.dumps(
            {
                "message": f'Datafile [{json_datafile_name}] transformed and put into bucket [{param_zip_inbound_bucket_name}]'}),
        headers={"Content-Type": "application/json"}

    )


def return_fn_error(ctx, fn_response, message, additional_details="None"):
    logging.critical(message)
    # Return Error

    return fn_response.Response(
        ctx, response_data=
        {
            "errorMessage": message,
            "additionalDetails": additional_details
        },
        headers={"Content-Type": "application/json"}
    )


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
    message = {"status": status,
               "header": title,
               "message": message,
               "additionalDetails": additional_details}
    publish_ons_notification(ons_topic_id, title, str(message))
    return message
