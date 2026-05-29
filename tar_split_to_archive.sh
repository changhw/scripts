#!/usr/bin/env bash

folder_name=pitagora
current_date=$(date +%Y%m%d)

tar \
  --listed-incremental=${folder_name}.snar \
  --exclude='*/postproc*' \
  --exclude='*/vtk*' \
  --exclude='*/four_results*' \
  --exclude='*/poincares*' \
  -cvf - ${folder_name} \
| ssh archive \
    "split --bytes=500G --suffix-length=4 --numeric-suffix - ${folder_name}_inc_${current_date}.tar."
