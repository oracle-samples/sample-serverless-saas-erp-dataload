# Copyright (c) 2021, Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.


import zipfile
import io
import logging


def create_erp_invoices_datafiles(json_data, zip_file_name):
    logging.info("Within create_erp_invoices_datafiles function")
    invoice_template = open('APInvoiceTemplate.csv.template', 'r').read()
    invoice_line_template = open('APInvoiceLinesTemplate.csv.template', 'r').read()

    # Now process the file
    ap_invoices_interface = ""
    ap_invoice_lines_interface = ""

    for single_invoice in json_data['invoices']:
        new_invoice = invoice_template
        for invoice_key, invoice_key_value in single_invoice.items():
            if invoice_key.upper() == "INVOICELINES":
                # Process Invoice Lines
                line_number = 1  # Set line number 1 and increment for each invoiceline
                for invoice_line in invoice_key_value:
                    # Process MANDATORY Elements in Invoice Line
                    # Get new template object
                    new_invoice_line = invoice_line_template
                    # Set Linenumber,  Invoice ID, $ACCOUNTINGDATE
                    new_invoice_line = new_invoice_line.replace("$INVOICELINENUM", str(line_number))
                    line_number = line_number + 1
                    new_invoice_line = new_invoice_line.replace("$ACCOUNTINGDATE", single_invoice['accountingDate'])
                    new_invoice_line = new_invoice_line.replace("$INVOICEID", single_invoice['invoiceId'])

                    # Now process invoice lines
                    for invoice_lines_key, invoice_lines_value in invoice_line.items():
                        new_invoice_line = new_invoice_line.replace(f'${invoice_lines_key.upper()}',
                                                                    f'{invoice_lines_value}')
                    ap_invoice_lines_interface = ap_invoice_lines_interface + new_invoice_line
            else:
                # Process Invoices
                new_invoice = new_invoice.replace(f'${invoice_key.upper()}', f'{invoice_key_value}')
        ap_invoices_interface = ap_invoices_interface + new_invoice

        logging.info("Processed data")
        logging.info("AP_INVOICES_INTERFACE")
        logging.info(ap_invoices_interface)
        logging.info("AP_INVOICE_LINES_INTERFACE")
        logging.info(ap_invoice_lines_interface)

        # Write data to zip file.
        # Due to limits of disk space in Functions, the zip file is created on the fly.
        zip_buffer = io.BytesIO()
        destination_zip_name = f'{zip_file_name}'
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, False) as zip_file:
            for file_name, json_data in [('ApInvoicesInterface.csv', io.BytesIO(ap_invoices_interface.encode())),
                                         ('ApInvoiceLinesInterface.csv', io.BytesIO(ap_invoice_lines_interface.encode()))]:
                zip_file.writestr(file_name, json_data.getvalue())

        with open(destination_zip_name, 'wb') as f:
            f.write(zip_buffer.getvalue())

    logging.info(f'Zip file writen to {destination_zip_name}')