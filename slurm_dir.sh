#!/bin/bash

# Check if the job ID is provided as a command-line argument
if [ $# -ne 1 ]; then
    echo "Usage: $0 <job_id>"
    return
fi

# Extract the job ID from the command-line argument
job_id_to_find="$1"

# job_info=$(scontrol show job $job_id_to_find | grep WorkDir)
# project_path=$(echo "$job_info" | sed 's/WorkDir=//') #$(echo "$job_info" | awk '{print $NF}')

sacct --starttime 2023-08-09 --format=User,JobID,Jobname%10,state,start,end,elapsed,nnodes,WorkDir%180 | grep "$USER $job_id_to_find"

job_info=$(sacct --starttime 2023-08-09 --format=User,JobID,WorkDir%200 | grep "$USER $job_id_to_find")
job_info=$(echo "$job_info" | sed s/$USER//)
job_info="${job_info#*[0-9] }"
project_path="${job_info// /}"


if [ -n "$project_path" ]; then
    echo " cd project path for job ID $job_id_to_find: $project_path"
    # Now, you can use 'cd' to change into the project path
    cd ${project_path}
else
    echo "Job ID $job_id_to_find not found in the Slurm output."
fi

