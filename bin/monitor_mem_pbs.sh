#!/bin/bash
#
# monitor_mem_pbs.sh — live memory monitor for a PBS/Torque job
#
# Usage:  ./monitor_mem_pbs.sh <JOBID> [INTERVAL_SECONDS]
#         Default interval is 30 seconds.
#
# Example: ./monitor_mem_pbs.sh 12345.mgmt 10
#
# Notes:
#   PBS does not have a direct equivalent of SLURM's 'sstat' for live
#   per-node RSS.  This script uses 'qstat -f' which reports aggregate
#   memory via cgroups (if enabled).  For per-node detail, uncomment
#   the SSH block inside the loop.

JOBID="${1:?Usage: $0 <JOBID> [INTERVAL_SECONDS]}"
INTERVAL="${2:-30}"

# --- helpers: human-readable sizes ----------------------------------
hr() {
  local bytes=$1
  if   (( bytes >= 1099511627776 )); then printf "%.1f TB" "$(bc <<< "scale=1; $bytes/1099511627776")"
  elif (( bytes >= 1073741824 ));    then printf "%.1f GB" "$(bc <<< "scale=1; $bytes/1073741824")"
  elif (( bytes >= 1048576 ));       then printf "%.1f MB" "$(bc <<< "scale=1; $bytes/1048576")"
  elif (( bytes >= 1024 ));          then printf "%.1f KB" "$(bc <<< "scale=1; $bytes/1024")"
  else printf "%d B" "$bytes"
  fi
}

# --- parse size string (e.g. "447117392kb", "3.2gb") to bytes -------
to_bytes() {
  local raw="$1"
  raw="${raw,,}"                         # lowercase
  raw="${raw//[[:space:]]/}"             # strip whitespace
  if [[ -z "$raw" || "$raw" == "0" ]]; then
    echo 0; return
  fi
  local num="${raw%%[a-z]*}"             # numeric part
  local unit="${raw##*[0-9.]}"           # unit suffix (kb, mb, gb, b, ...)
  num="${num%%.}"                        # strip decimal (bash can't float)
  num=${num:-0}
  case "$unit" in
    tb) echo $(( num * 1099511627776 )) ;;
    gb) echo $(( num * 1073741824 ))    ;;
    mb) echo $(( num * 1048576 ))       ;;
    kb) echo $(( num * 1024 ))          ;;
    b)  echo "$num"                     ;;
    *)  echo 0 ;;                        # unknown format
  esac
}

# --- check job exists in queue -------------------------------------
chk() {
  qstat "$JOBID" 2>/dev/null | awk -v id="$JOBID" '$1 == id {print $5; exit}'
}

STATE=$(chk)
if [[ -z "$STATE" ]]; then
  echo "Job $JOBID not found in queue. Trying tracejob..."
  tracejob "$JOBID" 2>/dev/null | tail -20
  exit 1
fi

# --- try to discover mem limit from qstat -f once -------------------
mem_limit_str=$(qstat -f "$JOBID" 2>/dev/null | \
  grep -iE 'Resource_List\.(mem|pmem)' | head -1 | awk -F'= ' '{print $2}')
mem_limit_bytes=$(to_bytes "$mem_limit_str")

# --- header ---------------------------------------------------------
printf "%-12s  %-10s  %12s  %12s  %12s  %11s  %s\n" \
       "TIME" "STATE" "MEM_USED" "VMEM" "MEM_LIMIT" "USED%" "EXEC_HOSTS"
printf "%s\n" "$(printf -- '-%.0s' {1..100})"

# --- main loop ------------------------------------------------------
while true; do
  if [[ -z "$(chk)" ]]; then
    echo ""
    echo "[$(date +%H:%M:%S)] Job $JOBID finished."
    break
  fi

  # grab full qstat -f for this job
  qf=$(qstat -f "$JOBID" 2>/dev/null)

  state=$(echo "$qf" | grep -i 'job_state' | awk -F'= ' '{print $2}')
  state=${state:-UNKNOWN}

  mem_used_str=$(echo "$qf" | grep -i 'resources_used.mem'  | head -1 | awk -F'= ' '{print $2}')
  vmem_str=$(   echo "$qf" | grep -i 'resources_used.vmem' | head -1 | awk -F'= ' '{print $2}')
  hosts=$(      echo "$qf" | grep -i 'exec_host'           | head -1 | awk -F'= ' '{print $2}')

  mem_used_bytes=$(to_bytes "$mem_used_str")
  vmem_bytes=$(to_bytes "$vmem_str")

  # percentage
  if (( mem_limit_bytes > 0 )); then
    pct=$(bc <<< "scale=1; $mem_used_bytes * 100 / $mem_limit_bytes")
  else
    pct="N/A"
  fi

  printf "%-12s  %-10s  %12s  %12s  %12s  %10s%%  %s\n" \
         "$(date +%H:%M:%S)" \
         "$state" \
         "$(hr $mem_used_bytes)" \
         "$(hr $vmem_bytes)" \
         "$(hr $mem_limit_bytes)" \
         "$pct" \
         "${hosts:-N/A}"

  # ------------------------------------------------------------------
  # [Optional] per-node detail via SSH.
  # Enable this block if you have passwordless SSH to compute nodes
  # and want per-process rss.  Adjust the grep pattern to match your
  # executable name.
  # ------------------------------------------------------------------
  # for node in $(echo "$hosts" | tr '+' '\n' | cut -d/ -f1 | sort -u); do
  #   ssh "$node" "ps -u \$USER -o rss,pid,args --no-headers 2>/dev/null \
  #     | grep jorek | awk '{sum+=\$1} END {printf \"  %-20s %s\n\", \"$node\", sum*1024}'"
  # done

  sleep "$INTERVAL"
done
