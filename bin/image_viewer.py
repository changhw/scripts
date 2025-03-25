#! /usr/bin/env python3

import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import glob
import sys
import os

# Get folder path and extension from command-line arguments
folder = sys.argv[1] if len(sys.argv) > 1 else "./"
extension = sys.argv[2] if len(sys.argv) > 2 else "png"

# Ensure folder path is absolute
folder = os.path.abspath(folder)
search_pattern = os.path.join(folder, f"*.{extension}")

# Load images
images = sorted(glob.glob(search_pattern))

if not images:
    print(f"No *.{extension} files found in {folder}")
    sys.exit(1)

print(f"Displaying images from: {folder}")
print(f"File extension: {extension}")
print("\nNavigation Instructions:")
print("- Left Click  -> Next Image")
print("- Right Click -> Previous Image")
print("- Close the window to exit")

# Initialize index
index = 0

def on_click(event):
    """Handle mouse clicks to navigate images."""
    global index
    if event.button == 1:  # Left click -> Next image
        index = min(index + 1, len(images) - 1)
    elif event.button == 3:  # Right click -> Previous image
        index = max(index - 1, 0)
    
    update_image()

def update_image():
    """Update the displayed image."""
    plt.clf()  # Clear figure
    img = mpimg.imread(images[index])
    plt.imshow(img)
    plt.axis('off')
    plt.title(f"Left Click: Next | Right Click: Previous\n{index + 1}/{len(images)}: {os.path.basename(images[index])}")
    plt.draw()

# Set up figure and event listener
fig = plt.figure()
fig.canvas.mpl_connect('button_press_event', on_click)

# # Show navigation manual first
# plt.text(0.5, 0.6, "Navigation Instructions:\n\nLeft Click  -> Next Image\nRight Click -> Previous Image\n\nClick to start", 
#          fontsize=14, ha='center', va='center')
# plt.axis('off')
# plt.show()

# Show first image
update_image()
plt.show()
