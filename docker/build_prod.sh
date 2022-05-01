#!/bin/bash
#
# Due to Docker fs, we need to copy all sources
#
rsync -avr --delete --exclude-from=not_to_copy.lst ../py/ py/

#docker build            -t ecotaxa/ecotaxa_back -f prod_image/Dockerfile .
docker build --no-cache -t ecotaxa/ecotaxa_back -f prod_image/Dockerfile .

#docker build -t ecotaxa/ecotaxa_gpu_back -f gpu_prod_image/Dockerfile .

# once built, replace 2.3 with the version...:
#   docker tag grololo06/ecotaxaback:latest grololo06/ecotaxaback:2.5
#   docker push grololo06/ecotaxaback:2.5

#   docker tag grololo06/ecotaxagpuback:latest grololo06/ecotaxagpuback:2.5
#   docker push grololo06/ecotaxagpuback:2.5
# needing before:
#   docker login
