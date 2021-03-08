# Copyright (c)  2021,  Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.
# triggers a dummy event ,
#
set -x
# ./loadtestfile2oci.sh
cat ../sample_files/sampleEvent.json | fn invoke Serverless_Integration erp-transform-file
