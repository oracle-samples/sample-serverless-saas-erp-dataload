# Copyright (c) 2021, Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.


import xml.etree.ElementTree as ET
import re
import logging
import io
import json
from fdk import response
import oci.object_storage
from oci.object_storage.models import CopyObjectDetails
import os.path
import time


def handler(ctx, data: io.BytesIO = None):
    logging.info("------------------------------------------------------------------------------")
    logging.info("Inside ERP Callback ")
    logging.info("------------------------------------------------------------------------------")
    region = os.environ['OCI_RESOURCE_PRINCIPAL_REGION']

    cfg = ctx.Config()
    # Setup OCI
    signer = oci.auth.signers.get_resource_principals_signer()
    object_storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    namespace = object_storage_client.get_namespace().data

    try:
        param_completed_bucket_name = cfg["succeeded_bucket_name"]
        param_failed_bucket_name = cfg["failed_bucket_name"]
        param_processing_bucket_name = cfg["processing_bucket_name"]

        param_ons_error_topic_ocid = cfg["ons_error_topic_ocid"]
        param_ons_info_topic_ocid = cfg["ons_info_topic_ocid"]

    except KeyError as ke:
        message = f'Mandatory Configuration Parameter {ke} missing, please check all configuration parameters'
        return return_fn_error(ctx, response, message)

    try:

        xml_data = data.getvalue().decode('UTF8')
        logging.info(f'XML callback received XML= {xml_data}')
        logging.info("---------------------------------------------")
        root = ET.fromstring(xml_data)
        xml_result_message = ""
        # Extract the JSON response from the XML header
        for element in root.iter('resultMessage'):
            xml_result_message = element.text
        # Remove \n etc
        regex = re.compile(r'[\n\r\t]')
        result_message = regex.sub(" ", xml_result_message)
        # Now convert to JSON
        json_result_message = json.loads(result_message)
        erp_status = json_result_message['JOBS'][0]['STATUS']
        erp_request_id = json_result_message['JOBS'][0]['REQUESTID']
        erp_document_name = json_result_message['JOBS'][0]['DOCUMENTNAME']

        logging.info(f'Job {erp_request_id}, document {erp_document_name} completed with {erp_status}')
    except (Exception, ValueError) as ex:
        additional_details = {"status": "ERROR",
                    "eventInformation": data.getvalue().decode('UTF8'),
                    "errorMessage": str(ex)
                    }
        message = send_notification(
            ons_topic_id=param_ons_error_topic_ocid,
            title="Generic Failure",
            message="Generic error during callback processing of ERP JOB",
            status="ERROR",
            additional_details=additional_details
        )


        return return_fn_error(ctx, response, f'ERPCallback : Error parsing content payload, error message {ex} ')
    # Process callback
    try:
        # Move file from processing bucket to completed, or failed bucket
        data_file_name = erp_document_name + "_ERPJOBID_" + erp_request_id

        # Move file to final location
        if erp_status.upper() == "SUCCEEDED":
            destination_bucket_name = param_completed_bucket_name
        else:
            destination_bucket_name = param_failed_bucket_name
        #
        logging.info("Moving file to bucket " + destination_bucket_name)

        copy_object_request = CopyObjectDetails()
        copy_object_request.destination_bucket = destination_bucket_name
        copy_object_request.destination_namespace = namespace
        copy_object_request.destination_object_name = data_file_name
        copy_object_request.destination_region = region
        copy_object_request.source_object_name = data_file_name
        copy_object_result = object_storage_client.copy_object(namespace, param_processing_bucket_name,
                                                               copy_object_request)

        work_request_id = copy_object_result.headers['opc-work-request-id']
        logging.info("Copy Object request id " + work_request_id)

        work_request = object_storage_client.get_work_request(work_request_id)
        logging.info(f'Status of Copy {work_request.data.status}')
        # Wait for the request to complete
        while work_request.data.status != "COMPLETED":
            time.sleep(1)
            work_request = object_storage_client.get_work_request(work_request_id)
            logging.info(f'Status of Copy {work_request.data.status}')
        # now delete original file
        if object_storage_client.delete_object(namespace, param_processing_bucket_name, data_file_name) != 204:
            # Just a warning if it doesnt delete
            logging.warning(f'Error deleting file {data_file_name} from bucket {param_processing_bucket_name} ')

    except (Exception, ValueError) as ex:
        additional_details = {"status": "FAILURE",
                               "reportId": erp_request_id,
                               "errorMessage": str(ex)
                               }
        message = send_notification(
            ons_topic_id=param_ons_error_topic_ocid,
            title="Error during callback processing",
            message=f'Error during callback processing of ERP JOB {erp_request_id}',
            status="ERROR",
            additional_details=additional_details
        )
        return return_fn_error(ctx, response, message)

    # Publish successful load message to info topic
    additional_details={
                   "filename": data_file_name,
                   "erpJobId": erp_request_id,
                   "status": erp_status
               }
    message = send_notification(
        ons_topic_id=param_ons_info_topic_ocid,
        title=f'Call back from ERP , JOBID {erp_request_id} Processed',
        message=f'Successfully Processed ERP Callback for ERPJob {erp_request_id} ',
        status="SUCCESS",
        additional_details=additional_details
    )

    return response.Response(
        ctx, response_data=json.dumps(message),
        headers={"Content-Type": "application/json"}
    )


def return_fn_error(ctx, fn_response, message, additional_data="None"):
    logging.critical(message)
    # Return Error

    return fn_response.Response(
        ctx, response_data=json.dumps(
            {
                "errorMessage": message,
                "additionalData": additional_data
            }),
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
    """

    :rtype: object
    """
    message = {"status": status,
               "header": title,
               "message": message,
               "additionalDetails": additional_details}
    publish_ons_notification(ons_topic_id, title, str(message))
    return message
