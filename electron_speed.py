#! /usr/bin/env python3
#
# Purpose: Estimate the amplitude of parallel electric field 
# for electron acceleration by tens of keV
#
# Date: 2023-09-18
# Author: Haowei Zhang, IPP Garching
#

import numpy as np
import matplotlib.pyplot as plt

ek_kev   = 50             # keV
e_charge = 1.60217663e-19 # c
e_mass   = 9.10938370e-31 # kg
c        = 2.99792458e8   # m/s
ek       = ek_kev * 1e3 * e_charge # J

e_mass_r = (ek + e_mass * c**2) / c**2
gamma    = e_mass_r / e_mass
e_velocity = np.sqrt(1 - 1/gamma**2) * c
beta     = e_velocity / c

print(f'kin energy = {ek_kev    } keV')
print(f'           = {ek        } J')
print(f'gamma      = {gamma     }')
print(f'electron v = {e_velocity} m/s')
print(f'           = {beta      } c')


R = 1.7 # m
loop_2piR_length = 2 * np.pi * R # m
loop_period   = loop_2piR_length / e_velocity
# print(f'electron transit period = {loop_period} s')

tau_interest  = 0.1 # s
loop_num      = tau_interest / loop_period
print(f'electron transit turns = {loop_num}')

delta_ek_kev = 40 # keV
delta_ek = delta_ek_kev * 1e3 * e_charge # J
e_mass_r_plus = (ek + delta_ek + e_mass * c**2) / c**2
gamma_plus = e_mass_r_plus / e_mass
e_velocity_plus = np.sqrt(1 - 1/gamma_plus**2) * c
beta_plus       = e_velocity_plus / c
loop_period_plus   = loop_2piR_length / e_velocity_plus
loop_num_plus      = tau_interest / loop_period_plus
print(f'electron v (+) = {e_velocity_plus} m/s')
print(f'               = {beta_plus      } c')
print(f'electron transit turns (+) = {loop_num_plus}')


e_mass_r_minus = (ek - delta_ek + e_mass * c**2) / c**2
gamma_minus = e_mass_r_minus / e_mass
e_velocity_minus = np.sqrt(1 - 1/gamma_minus**2) * c
beta_minus       = e_velocity_minus / c
loop_period_minus   = loop_2piR_length / e_velocity_minus
loop_num_minus      = tau_interest / loop_period_minus
print(f'electron v (-) = {e_velocity_minus} m/s')
print(f'               = {beta_minus      } c')
print(f'electron transit turns (-) = {loop_num_minus}')

electric_potential = delta_ek / e_charge # V
electric_loop_voltage = electric_potential / loop_num # V
electric_field     = electric_potential / (loop_2piR_length * loop_num) # V/m
print(f'>>for delta Ek = 40 keV:')
print(f'  electron potential = {electric_potential} V')
print(f'  loop voltage       = {electric_loop_voltage * 1e3} mV')
print(f'  electron field = {electric_field * 1e3} mV/m')

electric_field_plus     = electric_potential / (loop_2piR_length * loop_num_plus) # V/m
print(f'  electron field (+) = {electric_field_plus * 1e3} mV/m')

electric_field_minus     = electric_potential / (loop_2piR_length * loop_num_minus) # V/m
print(f'  electron field (-) = {electric_field_minus * 1e3} mV/m')