#!/bin/bash
# Copyright (c) 2021, Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.

if [ -z "$1" ] 
then
    echo "usage : test_loadfile2oci.sh <namespace>"
    exit -1
fi


# Upload file
oci os object put -ns $1 -bn Serverless_Integration_json_inbound --file ../sample_files/createInvoiceSample.json --name createInvoiceSample.`uuidgen | cut -c1-8`.json --force

