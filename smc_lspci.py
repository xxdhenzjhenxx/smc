#!/usr/bin/env python3
"""
smc_lspci.py

--- Supermicro lspci info tool for HGX systems ---

Version: 1.0.0
Date: July 1, 2024
Author: dennisd@supermicro.com 

Usage:
  -h, --help  show this help message and exit
  -t          show Root port, GPU, NIC, and main PLX switch topology tree
  -g          show GPU bus ID, slot, PLX SW and CPU node mapping
  -n          show NIC bus ID, slot, PLX SW and CPU node mapping
  -v          show program's version number and exit

Tested System(s):
  -SYS-821GE-TNHR
"""

VERSION = "1.0.0"

from subprocess import run, PIPE, CompletedProcess
from sys import argv, stderr, exit
from re import search
from shutil import which
from os import geteuid
from textwrap import indent
import argparse

parser = argparse.ArgumentParser(description='--- Supermicro lspci info tool for HGX systems ---')
parser.add_argument("-t", action="store_true", help="show root port, GPU, NIC and MAIN PLX SW topology tree")
parser.add_argument("-g", action="store_true", help="show gpu bus ID, slot, PLX SW bus ID and CPU node mapping")
parser.add_argument("-n", action="store_true", help="show nic bus ID, slot, PLX SW bus ID and CPU node mapping")
parser.add_argument('-v', action='version', version='%(prog)s {}'.format(VERSION))

colors = {
    "RED": '\x1B[31;1m', "YELLOW": '\x1B[33;1m',     
    "CYAN": '\x1B[36;1m', "ORANGE": '\x1B[38;5;208m', 
    "GREEN": '\x1B[32;1m', "PURPLE": '\x1B[35;1m',     
    "MAGENTA": '\x1B[35;1m',"RST": '\x1B[0m'            
}

tested_systems = ['SYS-821GE-TNHR']

class SupermicroLspciInfo:
    def __init__(self):
        self.device_dict = {}

    def show_slot_info(self, pattern, color=None, device=None) -> None:
        self.get_plxsw(device)
        slot_type = "GPU" if pattern == "sxm5" else "NIC"
        slot_output = self.runcmd(f"lspci | grep -i {pattern} | grep -v 00.1")
        bus_ids = [line.split()[0] for line in slot_output.split('\n') if line.strip()]  # Filter out empty lines

        for bus_id in bus_ids:
            output = self.runcmd(f"lspci -s {bus_id} -vv | egrep -i 'Ethernet|Physical Slot:|NUMA node'")

            if "NUMA" in output:
                output = output.replace("NUMA node", "CPU slot")

            physical_slot = ""
            cpu_slot = ""

            lines = output.splitlines()
            for line in lines:
                if "Physical Slot:" in line:
                    physical_slot = line.strip().replace("Physical Slot:", "").strip()
                elif "CPU slot:" in line:
                    cpu_slot = line.strip().replace("CPU slot:", "").strip()

            
            plx = self.device_dict.get(bus_id)
            plx_busid = plx if plx != 'NA' else 'N/A    '

            if slot_type == "GPU":
                output_line = f"{colors['CYAN']}[GPU bus ID: {bus_id}]{colors['RST']}"
            elif slot_type == "NIC":
                output_line = f"{colors['YELLOW']}[NIC bus ID: {bus_id}]{colors['RST']}"

            if physical_slot:
                output_line += f" -> PCI Slot: {physical_slot} -> PLX bus ID: {plx_busid}"
            elif not physical_slot:
               side_sw = self.side_plx_sw()
               if bus_id == side_sw[0]:
                  output_line += f" -> PCI Slot: N/A -> Side SW bus ID: {side_sw[1]}"
            if cpu_slot:
                output_line += f" -> CPU slot: {cpu_slot}"
            print(output_line)

    def show_pci_tree(self) -> None:
        print(colors.get('MAGENTA') + '// Root Port ' + colors.get('RST'))
        lspcioutput = self.runcmd(
            "lspci -tvv | egrep -v 'Mesh 2 PCIe|RAS|0b23|PMON|MSM|324c|324d|2710' | grep -A 6 'Map/VT-d'"
        )
        keywords = [
            'H100 SXM5 80GB', 'ConnectX-7', 'LSI PCIe Switch', 'NVSwitch', '49-58'
        ] #49-58 - side sw range. testing only

        for line in lspcioutput.strip().split('\n'):
            if "Map/VT-d" in line:
                match = search(r'\[(.*?)\]', line)
                result = match.group(0)  # Get the first match
                wrapped_output = indent(result, ' ' * 3)
                print(colors.get('GREEN') + f'{wrapped_output}' + colors.get('RST'))
            if any(word in line for word in keywords):
                lines = line.splitlines()
                lines[0] = lines[0].replace('|', '', 1)
                adjusted_output = '\n'.join(lines)

                if "Broadcom" in line:
                    print(colors.get('ORANGE') + f'{adjusted_output}' + colors.get('RST'))
                else:
                    adjusted_lines = [line[8:] for line in lines]
                    adjusted_output = '\n'.join(adjusted_lines)
                    print(adjusted_output.replace("|", " ", 1))
    
    def get_plxsw(self, device) -> None:
        search_dev = 'H100 SXM5 80GB' if device == 'GPU' else 'ConnectX-7'
        keyword_remove = 'Subsystem|Kernel' if device  == 'GPU' else 'Subsystem|Kernel|00.1'
        plx_data = self.runcmd(f"lspci -k | egrep -vi \'{keyword_remove}\' | grep -A 2 \'{search_dev}\'")
        chunks = plx_data.strip().split('--')

        for chunk in chunks:
            lines = chunk.strip().split('\n')            
            key = None
            value = "NA"  # Default value if Broadcom is not found
            
            # Find 3D controller and set its PCI address as key
            for line in lines:
                if "3D controller" in line:
                    key = line.split()[0].lower()
                    break
                elif "ConnectX-7" in line:
                    key = line.split()[0].lower()
                    break
            
            # Find Broadcom controller and set its PCI address as value
            for line in lines:
                if "Broadcom" in line:
                    value = line.split()[0].lower()
                    break
            if key:
                self.device_dict[key] = value

    def side_plx_sw(self) -> list: 
        #Checks static side sw bus. Testing only.
        #Broadcom PEX890xx = Side SW, Broadcom PCIe Switch = Main PLX SW
        side_sw_busID = []
        side_sw = self.runcmd(
            "lspci -k | grep -A 5 Mellanox | egrep -vi \'subsystem|kernel|00.1\' | grep -B 2 'Broadcom / LSI PEX890xx\'"
            ).strip()

        for line in side_sw.split('\n'):
            words = line.split()
            if "Ethernet" in line or "PEX890xx" in line:
                side_sw_busID.append(words[0])
        return side_sw_busID
        
    def run(self) -> None:
        args = parser.parse_args()

        if len(argv)==1:
            parser.print_help(stderr)
            exit(1)

        option_actions = {
            't': self.show_pci_tree,
            'g': lambda: self.show_slot_info("sxm5", "CYAN", "GPU"),
            'n': lambda: self.show_slot_info("mella", "YELLOW", "NIC")
        }

        if any(vars(args).get(opt) for opt in option_actions):
            SystemChecker()
            for opt, action in option_actions.items():
                if vars(args).get(opt):
                    action()
                    break
    
    @staticmethod
    def runcmd(command: str) -> CompletedProcess:
        return run(command, shell=True, stdout=PIPE, encoding="utf-8").stdout.strip()

class SystemChecker:
    def __init__(self):
        self.check_root()
        self.check_ipmitool_installed()
        self.check_system_compatibility()

    def check_root(self) -> None:
        if geteuid() != 0:
            print(colors['RED'] + "Error: This script must be run as root." + colors['RST'])
            exit(1)

    def check_ipmitool_installed(self) -> None:
        if not which('ipmitool'):
            print(
                colors['RED'] + "Error: ipmitool is not installed. Please install it to use this script." + colors['RST']
                )
            exit(1)

    def check_system_compatibility(self) -> None:
        fru_output = SupermicroLspciInfo.runcmd("ipmitool fru list")
        for line in fru_output.split('\n'):
            if "Product Part Number" in line:
                sys_pn = line.split(":")[-1]
                if not any(pn in sys_pn for pn in tested_systems):
                    print(
                        colors['RED'] + "Error: This system PN is not listed on the compatible system list)." + colors['RST'])
                    exit(1)

if __name__ == "__main__":
    tool = SupermicroLspciInfo()
    tool.run()
