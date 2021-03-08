# Copyright (c)  2021,  Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl.
set -x
cat ../sample_files/sampleevent.json | fn invoke Serverless_Integration erp-file-load
