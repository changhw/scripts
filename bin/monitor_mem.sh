#!/bin/bash
#
# monitor_mem.sh — live memory monitor for a SLURM job
#
# Usage:  ./monitor_mem.sh <JOBID> [INTERVAL_SECONDS]
#         Default interval is 30 seconds.
#
# Example: ./monitor_mem.sh 762008 10

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

# --- check job exists -----------------------------------------------
chk() {
  squeue -j "$JOBID" -h -o '%T' 2>/dev/null
}

STATE=$(chk)
if [[ -z "$STATE" ]]; then
  # job might be finished — try sacct for one last reading
  echo "Job $JOBID not found in queue. Trying sacct..."
  sacct -j "$JOBID" --format=JobID,State,MaxRSS,AveRSS,NodeList%60 --noheader 2>/dev/null | head -5
  exit 1
fi

# --- header ---------------------------------------------------------
printf "%-12s  %-8s  %12s  %12s  %12s  %12s  %-40s\n" \
       "TIME" "STATE" "MAX_RSS" "AVE_RSS" "MEM_LIMIT" "USED%" "PEAK_NODE"
printf "%s\n" "$(printf -- '-%.0s' {1..120})"

# --- main loop ------------------------------------------------------
while true; do
  if [[ -z "$(chk)" ]]; then
    echo ""
    echo "[$(date +%H:%M:%S)] Job $JOBID finished."
    break
  fi

  # one sstat call to grab fields
  read -r max_rss_kb ave_rss_kb node_list <<< \
    $(sstat -j "$JOBID" --format=MaxRSS,AveRSS,MaxRSSNode --noheader --parsable 2>/dev/null | \
      awk -F'|' '{print $1, $2, $3}')

  # strip trailing K/M/G suffix from sstat and fall back to 0 if empty
  max_rss_kb=${max_rss_kb:-0}; max_rss_kb=${max_rss_kb//[!0-9]/}
  ave_rss_kb=${ave_rss_kb:-0}; ave_rss_kb=${ave_rss_kb//[!0-9]/}
  max_rss_kb=${max_rss_kb:-0}
  ave_rss_kb=${ave_rss_kb:-0}
  max_rss_bytes=$(( max_rss_kb * 1024 ))
  ave_rss_bytes=$(( ave_rss_kb * 1024 ))

  # query memory limit once
  mem_limit_mb=$(scontrol show job "$JOBID" 2>/dev/null | \
                 grep -oP 'MinMemoryNode=\K[0-9]+' | head -1)
  mem_limit_mb=${mem_limit_mb:-0}
  mem_limit_bytes=$(( mem_limit_mb * 1048576 ))

  # percentage
  if (( mem_limit_bytes > 0 )); then
    pct=$(bc <<< "scale=1; $max_rss_bytes * 100 / $mem_limit_bytes")
  else
    pct="N/A"
  fi

  printf "%-12s  %-8s  %12s  %12s  %12s  %11s%%  %-40s\n" \
         "$(date +%H:%M:%S)" \
         "$(chk)" \
         "$(hr $max_rss_bytes)" \
         "$(hr $ave_rss_bytes)" \
         "$(hr $mem_limit_bytes)" \
         "$pct" \
         "${node_list:-N/A}"

  sleep "$INTERVAL"
done
