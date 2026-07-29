#!/bin/bash

# Check if the job ID is provided as a command-line argument
if [ $# -ne 1 ]; then
    echo "Usage: source $0 <job_id>"
    echo "Note: Script must be sourced (source script.sh) for 'cd' to affect your shell."
    return 2>/dev/null || exit 1
fi

# Extract the job ID from the command-line argument
job_id_to_find="$1"

# Query PBS for full job details using qstat -f
job_info=$(qstat -f "$job_id_to_find" 2>/dev/null)

if [ -z "$job_info" ]; then
    echo "Job ID $job_id_to_find not found or has expired from PBS history."
    return 2>/dev/null || exit 1
fi

# Parse the working directory (PBS stores this under PBS_O_WORKDIR or Variable_List)
project_path=$(echo "$job_info" | grep -i "PBS_O_WORKDIR" | sed -E 's/.*PBS_O_WORKDIR=([^,]+).*/\1/' | xargs)

# Fallback: Check standard Output_Path directory if PBS_O_WORKDIR isn't directly listed
if [ -z "$project_path" ]; then
    output_path=$(echo "$job_info" | grep -i "Output_Path" | awk '{print $3}')
    # Strip hostname if present (e.g., host:/path/to/dir -> /path/to/dir)
    project_path=$(dirname "${output_path#*:}")
fi

if [ -n "$project_path" ] && [ -d "$project_path" ]; then
    echo "Changing directory to project path for job ID $job_id_to_find: $project_path"
    cd "$project_path" || return 2>/dev/null || exit 1
else
    echo "Could not resolve a valid working directory for job ID $job_id_to_find."
fi
