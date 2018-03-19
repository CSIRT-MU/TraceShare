#!/usr/bin/env python
import argparse     # arguments parser
import pipes        # pipes.quote returns shell-escaped string (shlex.quote in newer python)
import subprocess   # Executes commands in shell
import shlex        # Split the string s using shell-like syntax
import time         # Manipulates time values
import sys          # Common system functions
import os           # Common Operating system functions
import errno        # Standard errno system symbols
import shutil       # Copy files and directory trees
import paramiko
import re
import yaml         # YAML file manipulation
from distutils.dir_util import copy_tree

"""
Runs commands from YAML file and captures the resulting network traffic into pcap files.
If 'filter' is provided for 'command' in YAML file, uses that filter during network traffic capture.
Writes logs for every command.
Elevated privileges are necessary due to tshark capture.
Use: $ sudo python capture_attacks.py
"""


def parse_script_arguments():
    """
    Parse script arguments

    :return: object containing arguments
    """
    # Define application arguments (automatically creates -h argument)
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--commandfile',
                        help='Path to the commands configuration file.',
                        type=argparse.FileType('r'), required=False,
                        default='/vagrant/capture_attacks/cmd.yaml')
    parser.add_argument('-d', '--capturedirectory',
                        help='Directory for captured traffic.',
                        type=str, required=False,
                        default='/vagrant/capture/')
    parser.add_argument('-i', '--networkinterface',
                        help='Network interface used to capture network traffic.',
                        type=str, required=False,
                        default='eth1')
    parser.add_argument('-t', '--timedelay',
                        help='Additional time for wandering packets [seconds].',
                        type=int, required=False,
                        default=3)
    return parser.parse_args()


def read_yaml(cmd_file):
    """
    Read YAML file

    :return: dictionary containing file data
    """
    try:
        data = yaml.load(cmd_file)
    except yaml.YAMLError as exc:
        print '[error] YAML configuration not correctly loaded: ' + str(exc)
        sys.exit(1)

    return data


def prepare_tmp_capture_dir(path):
    """
    Creates temporary directory with necessary permissions (for tshark)

    :param path: path of directory
    """
    if not os.path.exists(path):
        os.makedirs(path)

    # tshark needs all permissions, if you know of a better way let me know
    subprocess.call("chmod 777 " + path, shell=True)


def prepare_tshark_filter(data):
    """
    Prepares tshark filter string.
    If 'filter' is supplied for 'command' in YAML file, it is processed and returned as string.
    Otherwise empty string is returned.

    :param data: dictionary containing data
    :return: tshark filter string or None
    """
    tshark_filter = None
    if 'filter' in data:
        tshark_filter = data['filter']
        tshark_filter = pipes.quote(tshark_filter)

    return tshark_filter


def prepare_tshark_command(command, network_interface, data, time_str):
    """
    Prepares tshark command string

    :param command: command that is to be captured, used in pcap filename
    :param network_interface: used for capture
    :param data: dictionary containing string for tshark filtering
    :param time_str: timestamp string
    :return: tshark command string
    """
    pcap_path = log_filename(capture_path_tmp, '.pcapng', time_str, command)

    tshark_filter = prepare_tshark_filter(data)

    # Tshark command
    if tshark_filter:
        tshark_run = 'tshark -i {0} -q -w {1} -F pcapng -f {2}'.format(network_interface, pcap_path, tshark_filter)
    else:
        tshark_run = 'tshark -i {0} -q -w {1} -F pcapng'.format(network_interface, pcap_path)

    return tshark_run


def timestamp():
    """
    Returns timestamp for timestamping files

    :return: timestamp string
    """
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def start_tshark(command, network_interface, data, time_str):
    """
    Starts tshark process

    :param command: command that is to be captured, used in pcap filename
    :param network_interface: used for capture
    :param data: dictionary containing string for tshark filtering
    :param time_str: timestamp string
    :return: tshark process
    """
    tshark_command = prepare_tshark_command(command, network_interface, data, time_str)

    # Start tshark capture
    # shlex.split splits into shell args list, alternatively use without shlex.split and add shell=True, see doc
    tshark_process = subprocess.Popen(shlex.split(tshark_command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return tshark_process


def process_command(command, time_str, path):
    # Start command to be captured
    cmd_process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # read stdout, stderr of process, wait for command to terminate
    stdout, stderr = cmd_process.communicate()

    # Log command output
    with open(log_filename(path, '.log', time_str, command), 'w') as cmd_stdout:
        cmd_stdout.write(stdout)

    # If error happened during execution of command, write log
    if stderr:
        with open(log_filename(path, '.log', time_str, command), 'w') as cmd_stderr:
            cmd_stderr.write(stderr)

    print stdout
    print stderr


# TODO zjednotit process_config a process_command do niecoho univerzalneho
def process_config(config, command, time_str, path, file_suffix=''):
    # Start command to be captured
    cmd_process = subprocess.Popen(shlex.split(config), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # read stdout, stderr of process, wait for command to terminate
    stdout, stderr = cmd_process.communicate()

    # Log command output
    with open(log_filename(path, '.log', time_str, command, file_suffix), 'w') as cmd_stdout:
        cmd_stdout.write(stdout)

    # If error happened during execution of command, write log
    if stderr:
        with open(log_filename(path, '.err', time_str, command, file_suffix), 'w') as cmd_stderr:
            cmd_stderr.write(stderr)

    print stdout
    print stderr


def configure_attacker(data, time_str):
    if 'configure' in data['attacker']:
        print '======== Configuring attacker. ======================================='
        config = data['attacker']['configure']
        command = data['attacker']['command']
        file_suffix = 'config_attacker'
        process_config(config, command, time_str, capture_path_tmp_config, file_suffix)


def ssh_connect(hostname, username, password):
    print '======== Connecting. ================================================='
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname, username=username, password=password)
    return ssh


def log_filename(path, file_extension, *args):
    separator = '__'
    filename = ''
    for arg in args[:-1]:
        if arg:
            filename += arg
            filename += separator
    filename += args[-1]

    # limits filename length due to OS limitations
    if len(filename) > 100:
        filename = filename[:100]

    filename += file_extension
    filename = re.sub(r'[ @#$%^&*<>{}:|;\'\\\"/]', r'_', filename)

    filename = os.path.join(path, filename)
    return filename


# see semi_realtime_stdout if interested in prototype.py
def ssh_send_command(ssh, config, command, time_str, victim_str):
    print '======== Executing configuration. ===================================='
    stdin_handle, stdout_handle, stderr_handle = ssh.exec_command(config)
    stdout = stdout_handle.read()
    stderr = stderr_handle.read()

    with open(log_filename(capture_path_tmp_config, '.log', time_str, command, 'config_victim', victim_str),
              'w') as cmd_stdout:
        cmd_stdout.write(stdout)

    if stderr:
        with open(log_filename(capture_path_tmp_config, '.err', time_str, command, 'config_victim', victim_str),
                  'w') as cmd_stderr:
            cmd_stderr.write(stderr)

    print stdout
    print stderr


def configure_victims(data, time_str):
    if 'victims' in data:
        for victim in data['victims']:
            ip = victim['ip']
            config = victim['configure']
            command = data['attacker']['command']
            print '======== Configuring victim ' + ip + ' ============================'

            ssh = ssh_connect(ip, username, password)
            ssh_send_command(ssh, config, command, time_str, ip)
            ssh.close()


# TODO test ci sa zachyti opozdene ssh; delay before capture?
# TODO vygenerovat ssh kluce attacker -> victims
# TODO solve FutureWarning: CTR mode needs counter parameter, not IV
def wrapper(data_loaded, network_interface, capture_path_tmp, time_delay):
    """
    Starts capture, executes command, ends capture, writes command logs

    :param data_loaded: dictionary containing command and filter data
    :param network_interface: used for capture
    :param capture_path_tmp: to store captured data and logs
    :param time_delay: extra time for wandering packets
    """
    for data in data_loaded:
        time_str = timestamp()
        command = data['attacker']['command']

        configure_victims(data, time_str)

        configure_attacker(data, time_str)

        tshark_process = start_tshark(command, network_interface, data, time_str)

        print '======== Executing command: ' + command

        process_command(command, time_str, capture_path_tmp)

        # Extra time left for wandering packets
        time.sleep(time_delay)

        # Finish capturing
        tshark_process.terminate()

        move_directory_tree(capture_path_tmp, capture_path)
        print '######################################################################'


def move_directory_tree(src, dst):
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            if not os.path.exists(d):
                os.makedirs(d)
            move_directory_tree(s, d)
        else:
            shutil.move(s, d)


def welcome_message():
    print '####################### STARTING SCRIPT ##############################'


def farewell_message():
    print '####################### DATA EXPORTED ################################'
    print '####################### DONE #########################################'
    print '######################################################################'
    print 'Virtual machines can be deleted using "vagrant destroy".'


def cleanup_files():
    shutil.rmtree(capture_path_tmp)


if __name__ == '__main__':
    username = 'vagrant'
    password = 'vagrant'

    args = parse_script_arguments()

    # Read YAML file
    cmd_loaded = read_yaml(args.commandfile)

    # Directory of captured files   '../capture/'
    capture_path = args.capturedirectory

    capture_path_tmp = '/tmp/capture/'
    prepare_tmp_capture_dir(capture_path_tmp)
    capture_path_tmp_config = os.path.join(capture_path_tmp, 'config')
    prepare_tmp_capture_dir(capture_path_tmp_config)

    # Network interface used to capture network traffic
    network_interface = args.networkinterface

    # Extra time left for wandering packets
    time_delay = args.timedelay

    welcome_message()

    wrapper(cmd_loaded, network_interface, capture_path_tmp, time_delay)

    # TODO zmazat za sebou (final version)
    # cleanup_files()

    farewell_message()
