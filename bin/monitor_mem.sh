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

# --- helpers: size parsing and display -------------------------------
to_bytes() {
  local raw="$1"
  local default_unit="${2:-k}"

  raw="${raw,,}"
  raw="${raw//[[:space:]]/}"
  raw="${raw%%[cn]}"

  if [[ -z "$raw" || "$raw" == "0" || "$raw" == "n/a" ]]; then
    echo 0
    return
  fi

  if [[ "$raw" =~ ^([0-9]+([.][0-9]+)?)([kmgt]?i?b?|b)?$ ]]; then
    local num="${BASH_REMATCH[1]}"
    local unit="${BASH_REMATCH[3]}"
    case "$unit" in
      "") unit="$default_unit" ;;
      b) unit=b ;;
      k|kb|kib) unit=k ;;
      m|mb|mib) unit=m ;;
      g|gb|gib) unit=g ;;
      t|tb|tib) unit=t ;;
      *) unit="$default_unit" ;;
    esac

    awk -v n="$num" -v u="$unit" 'BEGIN {
      if (u == "t")      m = 1099511627776
      else if (u == "g") m = 1073741824
      else if (u == "m") m = 1048576
      else if (u == "k") m = 1024
      else               m = 1
      printf "%.0f\n", n * m
    }'
  else
    echo 0
  fi
}

gb() {
  local bytes=$1
  awk -v bytes="$bytes" 'BEGIN { printf "%.1f GB", bytes / 1073741824 }'
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
  IFS='|' read -r max_rss ave_rss node_list < <(
    sstat -j "$JOBID" --format=MaxRSS,AveRSS,MaxRSSNode --noheader --parsable 2>/dev/null |
      head -1
  )

  max_rss_bytes=$(to_bytes "${max_rss:-0}" k)
  ave_rss_bytes=$(to_bytes "${ave_rss:-0}" k)

  # query memory limit once
  mem_limit=$(scontrol show job "$JOBID" 2>/dev/null | \
              grep -oP 'MinMemoryNode=\K[^[:space:]]+' | head -1)
  mem_limit_bytes=$(to_bytes "${mem_limit:-0}" m)

  # percentage
  if (( mem_limit_bytes > 0 )); then
    pct=$(awk -v used="$max_rss_bytes" -v limit="$mem_limit_bytes" \
          'BEGIN { printf "%.1f", used * 100 / limit }')
  else
    pct="N/A"
  fi

  printf "%-12s  %-8s  %12s  %12s  %12s  %11s%%  %-40s\n" \
         "$(date +%H:%M:%S)" \
         "$(chk)" \
         "$(gb "$max_rss_bytes")" \
         "$(gb "$ave_rss_bytes")" \
         "$(gb "$mem_limit_bytes")" \
         "$pct" \
         "${node_list:-N/A}"

  sleep "$INTERVAL"
done
