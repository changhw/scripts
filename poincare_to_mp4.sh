#!/bin/bash

# Set output video name
OUTPUT_VIDEO="output.mp4"
FRAME_RATE=10  # Adjust as needed

# Remove previous input list if exists
rm -f input.txt

# Generate a sorted list of PNG files
ls poinc_R-Z_s*.png | sort -V > file_list.txt

# Create an input list for ffmpeg
while read -r file; do
    echo "file '$file'" >> input.txt
done < file_list.txt

# Run ffmpeg to create video from images
ffmpeg -r $FRAME_RATE -f concat -safe 0 -i input.txt -c:v libx264 -pix_fmt yuv420p "$OUTPUT_VIDEO"

# Cleanup temporary files
rm -f input.txt file_list.txt

echo "Video created: $OUTPUT_VIDEO"

