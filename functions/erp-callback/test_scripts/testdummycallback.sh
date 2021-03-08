# Copyright (c)  2021,  Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.
# This script calls the callback function with a dummy event. Change the app name to suit your configuration
set -x
cat ../samplePayloads/sampleCallback.xml | fn invoke Serverless_Integration erp-callback

