#! /usr/bin/env python3

#
# Purpose: Plot 2D variables from .vtk files.
#
# Date: 2023-09-20
# Author: Haowei Zhang, IPP Garching
#

import vtk
import vtkmodules.util.numpy_support as vtk_nump
import numpy as np
import matplotlib.pyplot as plt
import argparse
import scipy.interpolate as sci_intp
import mpl_toolkits.mplot3d as plt3d
import re
import os

time2si = 4.607490970e-04 # used to convert time from jorek units to millisecond

def read_ascii_file(file_path, skiprows, droprows=-1):

    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Extract column names from the first line and remove leading/trailing whitespace
    column_names = lines[0].strip().split()
    if skiprows >= 1:
        column_names = lines[skiprows - 1].strip().split()

    # Read data from the remaining lines, converting them to a NumPy array
    data = np.genfromtxt(lines[skiprows:droprows], dtype=float)

    return column_names, data


def get_time(path = './'):
    timefile = path + 'times.dat'
    if os.path.exists(timefile):
        column_names, data = read_ascii_file(timefile, 1)
        step_all_1d = data[:,0]
        times_all_1d = data[:,1]
    else:
        print("times.dat required, please firstly execute $ plot_live_data.sh -q time ")
        step_all_1d = []
        times_all_1d = []
        exit(-1)

    return step_all_1d, times_all_1d

def read_varlist_from_vtk(folder_name, reader, idx_plane, idx_step):
    file_path = folder_name + str(idx_plane) + '/' + 'jorek.' + str(idx_step).zfill(6) + '.vtk'

    reader.SetFileName(file_path)
    reader.ReadAllScalarsOn()
    reader.Update()

    # Get the VTK dataset
    Point_cordinates = reader.GetOutput().GetPoints().GetData()
    numpy_coordinates = vtk_nump.vtk_to_numpy(Point_cordinates)

    variable_array = [reader.GetScalarsNameInFile(
        i) for i in range(0, reader.GetNumberOfScalarsInFile())]

    return variable_array, numpy_coordinates

def read_data_from_vtk_for_iplane_istep(folder_name, reader, idx_plane, idx_step, variable):
    file_path = f"{folder_name}{idx_plane}/jorek.{idx_step:06d}.vtk"
    print(f"Reading {variable} from {file_path} ......")

    reader.SetFileName(file_path)
    reader.ReadAllScalarsOn()
    reader.Update()

    # Get the VTK dataset
    dataset = reader.GetOutput()

    variable_array = [reader.GetScalarsNameInFile(
        i) for i in range(0, reader.GetNumberOfScalarsInFile())]
    
    if variable not in variable_array:
        print(f"Error input variable name: {variable}, retry!!!")
        exit(-1)

    data_vtk = dataset.GetPointData().GetArray(variable)
    numpy_data = vtk_nump.vtk_to_numpy(data_vtk)

    return numpy_data

if __name__ == "__main__":

    # Define the input variables
    parser = argparse.ArgumentParser(
        description="Plot surfaces from vtk files.",
        epilog="Example usage: python3 plot_vtk_files.py -ip 0 -ik 4 -iv 0",
        formatter_class=argparse.RawTextHelpFormatter)
    
    parser.add_argument("-ip", "--i_plane_plot", type=int, default=0,
                        help="Input the toroidal plane to be plotted.")
    parser.add_argument("-ik", "--i_vtk_plot", type=int, default=4,
                        help="Input the vtk file to be plotted.")
    parser.add_argument("-iv", "--i_variable_plot", type=int, default=0,
                        help="Input the variable to be plotted.")
    
    parser.add_argument("-np", "--num_plane", type=int, default=16,
                        help="Define the number of toroidal planes.")
    parser.add_argument('-fd', "--folder_name", default="vtk_iplane",
                        help="Prefix of the folder name that contains the .vtk files.")
    
    parser.add_argument('-xylim', nargs=4, type=float, metavar=('xmin', 'xmax', 'ymin', 'ymax'),
                        help='Specify xmin, xmax, ymin, ymax for the plot.')
    parser.add_argument('-clim', nargs=2, type=float, metavar=('cmin', 'cmax'),
                        help='Specify cmin, cmax for the contourf.')

    args = parser.parse_args()

    # Initialize    
    folder_base = args.folder_name + str(1) + '/'
    file_pattern = re.compile(r'jorek\.(\d+)\.vtk')
    
    array_step = []
    array_time = [0]

    step_all_1d, times_all_1d = get_time()
    for filename in os.listdir(folder_base):
        match = file_pattern.match(filename)
        if match:
            step_number = int(match.group(1))
            array_step.append(step_number)
            if step_number > 0:
                array_time.append(times_all_1d[step_number])

    array_step = np.array(sorted(array_step))  # Sort if needed
    array_time = np.array(sorted(array_time)) * time2si # convert to ms
    array_plane = np.linspace(1, args.num_plane, args.num_plane, dtype=int)

    # Create a VTK reader
    vtkreader = vtk.vtkDataSetReader()

    # Read the variable list and R/Z coordinates from the first vtk file
    array_variable, numpy_coordinates = read_varlist_from_vtk(args.folder_name, vtkreader, array_plane[0], array_step[0])
    print('Variable list:\n', array_variable)

    # just modify these three integer variables to plot for different toroidal planes, different time points, and different variables
    i_plane    = args.i_plane_plot    # ranging from 0 ~ args.num_plane
    i_vtk      = args.i_vtk_plot      # ranging from 0 ~ number of .vtks in each folder
    i_variable = args.i_variable_plot # id/name of variables is listed below

    # ['0: Psi', '1: AR', '2: AZ', '3: UR', '4: UZ', '5: Uphi', '6: rho', '7: T_i', '8: T_e', '9: psi_norm', 
    # '10: B_R', '11: B_Z', '12: B_phi', '13: J_R', '14: J_Z', '15: J_phi', 
    # '16: FFprime_equi', '17: F_prof_equi', '18: Grad_P', '19: JxB', 
    # '20: V_parallel', 
    # '21: A_radial', '22: A_poloidal', 
    # '23: U_radial', '24: U_poloidal', 
    # '25: B_radial', '26: B_poloidal', 
    # '27: etaSpz', '28: Jsource', 
    # '29: F_over_R', '30: delta_Bphi', '31: Pres', 
    # '32: dBphidt', '33: n_charge_SI']

    # Read the data at each node for the given i_plane, i_vtk, and i_variable
    if i_plane < args.num_plane and i_vtk < len(array_step) and i_variable < len(array_variable):
        numpy_data = read_data_from_vtk_for_iplane_istep(args.folder_name, vtkreader, array_plane[i_plane], array_step[i_vtk], array_variable[i_variable])
    else:
        print(f"Error, please check if i_plane    = {i_plane} exceeds {len(array_step)}")
        print(f"Error, please check if i_vtk      = {i_vtk} exceeds {len(array_step)}")
        print(f"Error, please check if i_variable = {i_variable} exceeds {len(array_variable)}")
        exit(-1)
    
    # Plot the result
    plt.figure()
    if args.clim:
        plt.scatter(numpy_coordinates[:, 0], numpy_coordinates[:, 1], c = numpy_data, cmap = 'jet', marker = 'o', s = 50, vmin = args.clim[0], vmax = args.clim[1])
    else:
        plt.scatter(numpy_coordinates[:, 0], numpy_coordinates[:, 1], c = numpy_data, cmap = 'jet', marker = 'o', s = 50)
    
    phi_tmp = i_plane / args.num_plane * 2 * np.pi
    plt.title(fr"{array_variable[i_variable]} @ $\varphi$ = {phi_tmp:.2f}, t = {array_time[i_vtk]:.2f} ms")
    
    if args.xylim:
        xmin, xmax, ymin, ymax = args.xylim
        plt.xlim(xmin, xmax)
        plt.ylim(ymin, ymax)
        
    plt.colorbar()
    plt.xlabel('R')
    plt.ylabel('Z')    
    plt.axis('equal')
    plt.show()
    
